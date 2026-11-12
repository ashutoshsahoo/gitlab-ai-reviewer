"""Run deterministic analyzers (ruff, semgrep) on the changed files.

Their findings are (a) fed to the LLM as hints and (b) posted directly when
severity >= medium. Missing tools are skipped gracefully.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Settings
from .models import Category, Finding, Severity

log = logging.getLogger(__name__)

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
RUFF_SELECT = "E9,F,B,S,PERF,ASYNC,C4,SIM"

_HIGH_RUFF = {"F821", "F822", "F823", "S102", "S301", "S307", "S602", "S605", "S608"}


def _ruff_severity(code: str) -> Severity:
    if code.startswith("E9"):
        return Severity.critical
    if code in _HIGH_RUFF:
        return Severity.high
    if code.startswith(("S", "B")):
        return Severity.medium
    return Severity.low


def _ruff_category(code: str) -> Category:
    if code.startswith("S"):
        return Category.security
    if code.startswith("PERF"):
        return Category.performance
    if code.startswith("ASYNC"):
        return Category.concurrency
    if code.startswith(("F", "B", "E9")):
        return Category.correctness
    return Category.maintainability


_SEMGREP_SEV = {"ERROR": Severity.high, "WARNING": Severity.medium, "INFO": Severity.low}


class StaticAnalyzer:
    def __init__(self, settings: Settings):
        self.s = settings

    def run(self, files: dict[str, str]) -> list[Finding]:
        py = [p for p in files if p.endswith(".py")]
        if not files:
            return []
        findings: list[Finding] = []
        with tempfile.TemporaryDirectory(prefix="aireview-") as tmp:
            for rel, content in files.items():
                dest = Path(tmp) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            if py:
                findings += self._ruff(tmp, py)
            findings += self._semgrep(tmp, list(files))
        return findings

    # -- ruff ---------------------------------------------------------------
    def _ruff(self, cwd: str, paths: list[str]) -> list[Finding]:
        exe = shutil.which("ruff")
        if not exe:
            log.info("ruff not installed; skipping")
            return []
        cmd = [exe, "check", "--isolated", "--no-cache", "--output-format", "json", "--select", RUFF_SELECT, *paths]
        out = self._exec(cmd, cwd)
        if not out:
            return []
        try:
            items = json.loads(out)
        except json.JSONDecodeError:
            return []
        res: list[Finding] = []
        for it in items:
            code = it.get("code") or ""
            rel = os.path.relpath(it.get("filename", ""), cwd)
            res.append(
                Finding(
                    file=rel,
                    line=it["location"]["row"],
                    severity=_ruff_severity(code),
                    category=_ruff_category(code),
                    title=f"{code}: {it.get('message', '')}"[:120],
                    message=f"Ruff rule {code}: {it.get('message', '')}",
                    confidence=0.9,
                    source="ruff",
                    rule_id=code,
                )
            )
        return res

    # -- semgrep ---------------------------------------------------------------
    def _semgrep(self, cwd: str, paths: list[str]) -> list[Finding]:
        exe = shutil.which("semgrep")
        if not exe:
            log.info("semgrep not installed; skipping")
            return []
        cmd = [exe, "scan", "--json", "--quiet", "--metrics=off", "--disable-version-check", "--no-git-ignore"]
        cmd += ["--config", str(RULES_DIR)]
        if self.s.semgrep_extra_config:
            for cfg in self.s.semgrep_extra_config.split(","):
                cmd += ["--config", cfg.strip()]
        cmd += paths
        out = self._exec(cmd, cwd)
        if not out:
            return []
        try:
            results = json.loads(out).get("results", [])
        except json.JSONDecodeError:
            return []
        res: list[Finding] = []
        for r in results:
            extra = r.get("extra", {})
            meta = extra.get("metadata", {}) or {}
            cat = str(meta.get("category", "security")).lower()
            try:
                category = Category(cat)
            except ValueError:
                category = Category.security
            rule = r.get("check_id", "").split(".")[-1]
            msg = (extra.get("message") or "").strip()
            res.append(
                Finding(
                    file=os.path.normpath(r.get("path", "")),
                    line=r["start"]["line"],
                    severity=_SEMGREP_SEV.get(extra.get("severity", "WARNING"), Severity.medium),
                    category=category,
                    title=f"{rule}: {msg.splitlines()[0] if msg else rule}"[:120],
                    message=msg or rule,
                    confidence=0.85,
                    source="semgrep",
                    rule_id=rule,
                )
            )
        return res

    def _exec(self, cmd: list[str], cwd: str) -> str:
        try:
            p = subprocess.run(  # noqa: S603
                cmd, cwd=cwd, capture_output=True, text=True, timeout=self.s.static_timeout_s, check=False
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("%s failed: %s", cmd[0], exc)
            return ""
        if p.returncode not in (0, 1):  # both tools use 1 for "findings present"
            log.warning("%s exited %s: %s", cmd[0], p.returncode, p.stderr[:300])
        return p.stdout
