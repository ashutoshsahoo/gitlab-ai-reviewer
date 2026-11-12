"""Quality gate: turns findings into a pass/fail decision."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .models import Finding, Severity


@dataclass
class GateConfig:
    mode: str = "enforce"
    max_critical: int = 0
    max_high: int = 0
    max_medium: int = 5

    @classmethod
    def from_settings(cls, s: Settings, overrides: dict | None = None) -> "GateConfig":
        cfg = cls(
            mode=s.gate_mode,
            max_critical=s.gate_max_critical,
            max_high=s.gate_max_high,
            max_medium=s.gate_max_medium,
        )
        for key, val in (overrides or {}).items():
            if hasattr(cfg, key) and val is not None:
                setattr(cfg, key, type(getattr(cfg, key))(val))
        return cfg


@dataclass
class GateResult:
    passed: bool
    state: str  # GitLab commit-status state: success | failed
    mode: str
    counts: dict[str, int]
    reasons: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        c = self.counts
        tally = f"{c['critical']} critical, {c['high']} high, {c['medium']} medium, {c['low']} low"
        if self.passed and not self.reasons:
            return f"Quality gate passed ({tally})"
        if self.passed:  # advisory mode
            return f"Advisory: gate would fail - {'; '.join(self.reasons)}"[:255]
        return f"Quality gate failed: {'; '.join(self.reasons)} ({tally})"[:255]


def count_by_severity(findings: list[Finding]) -> dict[str, int]:
    counts = {s.value: 0 for s in Severity}
    for f in findings:
        counts[f.severity.value] += 1
    return counts


def evaluate(findings: list[Finding], cfg: GateConfig) -> GateResult:
    counts = count_by_severity(findings)
    reasons: list[str] = []
    if counts["critical"] > cfg.max_critical:
        reasons.append(f"{counts['critical']} critical (max {cfg.max_critical})")
    if counts["high"] > cfg.max_high:
        reasons.append(f"{counts['high']} high (max {cfg.max_high})")
    if counts["medium"] > cfg.max_medium:
        reasons.append(f"{counts['medium']} medium (max {cfg.max_medium})")
    would_fail = bool(reasons)
    if cfg.mode == "advisory":
        return GateResult(True, "success", cfg.mode, counts, reasons)
    return GateResult(not would_fail, "failed" if would_fail else "success", cfg.mode, counts, reasons)
