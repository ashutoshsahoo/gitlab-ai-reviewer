"""Formatting of inline comments and the MR summary note."""

from __future__ import annotations

from collections import Counter

from .gitlab_client import SUMMARY_MARKER
from .models import Finding, Severity
from .quality_gate import GateResult

EMOJI = {
    Severity.critical: "🛑",
    Severity.high: "🔴",
    Severity.medium: "🟠",
    Severity.low: "🟡",
    Severity.info: "🔵",
}

_LANG_BY_EXT = {".py": "python", ".java": "java", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx"}


def _lang(path: str) -> str:
    for ext, lang in _LANG_BY_EXT.items():
        if path.endswith(ext):
            return lang
    return ""


def format_inline_comment(f: Finding) -> str:
    lines = [
        f"**{EMOJI[f.severity]} {f.severity.value.upper()} · {f.category.value}** — {f.title}",
        "",
        f.message.strip(),
    ]
    if f.suggestion:
        lines += [
            "",
            "<details><summary>Suggested fix</summary>",
            "",
            f"```{_lang(f.file)}",
            f.suggestion.strip("\n"),
            "```",
            "</details>",
        ]
    source = f.source if f.source != "llm" else "AI"
    lines += ["", f"<sub>{source} review · confidence {f.confidence:.2f} · <!-- ai-review:fp={f.fingerprint} --></sub>"]
    return "\n".join(lines)


def build_summary(
    *,
    mr_web_url: str,
    head_sha: str,
    model: str,
    gate: GateResult,
    findings: list[Finding],
    overview: str | None,
    key_risks: list[str],
    positives: list[str],
    files_reviewed: int,
    skipped: list[tuple[str, str]],
    llm_errors: int,
    duration_s: float,
) -> str:
    project_url = mr_web_url.split("/-/merge_requests/")[0]
    icon = "✅" if gate.passed and not gate.reasons else ("⚠️" if gate.passed else "❌")
    verdict = "passed" if gate.passed and not gate.reasons else ("advisory warning" if gate.passed else "failed")
    out = [SUMMARY_MARKER, f"## 🤖 AI Code Review — {icon} Quality gate {verdict}", ""]
    if overview:
        out += [overview.strip(), ""]
    if key_risks:
        out += ["**Key risks**"] + [f"- {r}" for r in key_risks[:4]] + [""]
    if positives:
        out += ["**Looks good**"] + [f"- {p}" for p in positives[:2]] + [""]

    c = gate.counts
    out += [
        "| 🛑 Critical | 🔴 High | 🟠 Medium | 🟡 Low | 🔵 Info |",
        "|:--:|:--:|:--:|:--:|:--:|",
        f"| {c['critical']} | {c['high']} | {c['medium']} | {c['low']} | {c['info']} |",
        "",
    ]
    if gate.reasons:
        out += [f"**Gate ({gate.mode}):** " + "; ".join(gate.reasons), ""]
    else:
        out += [f"**Gate ({gate.mode}):** all thresholds satisfied", ""]

    cats = Counter(f.category.value for f in findings)
    if cats:
        out += ["**By category:** " + ", ".join(f"{k} ({v})" for k, v in cats.most_common()), ""]

    extra = [f for f in findings if f.disposition in ("overflow", "unplaced", "post_failed")]
    if extra:
        out += ["<details><summary>Findings not posted inline (" + str(len(extra)) + ")</summary>", ""]
        out += ["| Sev | Location | Issue |", "|---|---|---|"]
        for f in sorted(extra, key=lambda x: x.rank):
            loc = f"[{f.file}:{f.line}]({project_url}/-/blob/{head_sha}/{f.file}#L{f.line})"
            msg = f.message.replace("\n", " ").replace("|", "\\|")[:240]
            out.append(f"| {EMOJI[f.severity]} {f.severity.value} | {loc} | **{f.title}** — {msg} |")
        out += ["", "</details>", ""]

    notes = []
    if skipped:
        shown = ", ".join(f"`{p}` ({r})" for p, r in skipped[:8])
        more = f" and {len(skipped) - 8} more" if len(skipped) > 8 else ""
        notes.append(f"Skipped: {shown}{more}")
    if llm_errors:
        notes.append(f"{llm_errors} model call(s) failed; those chunks were not reviewed")
    if notes:
        out += ["<sub>" + " · ".join(notes) + "</sub>", ""]

    out.append(
        f"<sub>Model `{model}` · commit `{head_sha[:8]}` · {files_reviewed} file(s) reviewed · "
        f"{duration_s:.0f}s · comment `/ai-review` to re-run</sub>"
    )
    return "\n".join(out)
