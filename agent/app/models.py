"""Domain models shared by the whole agent."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ValidationError, field_validator

log = logging.getLogger(__name__)


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


SEVERITY_RANK = {Severity.critical: 0, Severity.high: 1, Severity.medium: 2, Severity.low: 3, Severity.info: 4}


class Category(str, Enum):
    correctness = "correctness"
    security = "security"
    performance = "performance"
    concurrency = "concurrency"
    error_handling = "error_handling"
    maintainability = "maintainability"
    readability = "readability"
    testing = "testing"
    api_design = "api_design"
    documentation = "documentation"
    style = "style"
    dependencies = "dependencies"
    observability = "observability"


SEVERITY_ALIASES = {
    "blocker": "critical",
    "major": "high",
    "error": "high",
    "warning": "medium",
    "moderate": "medium",
    "minor": "low",
    "nit": "low",
    "note": "info",
    "suggestion": "info",
}
CATEGORY_ALIASES = {
    "bug": "correctness",
    "logic": "correctness",
    "vulnerability": "security",
    "perf": "performance",
    "tests": "testing",
    "test": "testing",
    "docs": "documentation",
    "exception": "error_handling",
    "exceptions": "error_handling",
    "errors": "error_handling",
    "thread": "concurrency",
    "threading": "concurrency",
    "maintenance": "maintainability",
    "design": "maintainability",
    "api": "api_design",
    "logging": "observability",
    "dependency": "dependencies",
}


class LLMFinding(BaseModel):
    """One finding as returned by the LLM (validated + normalised)."""

    line: int
    severity: Severity
    category: Category
    title: str
    message: str
    suggestion: str | None = None
    confidence: float = 0.7

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v):
        v = str(v).strip().lower()
        return SEVERITY_ALIASES.get(v, v)

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v):
        v = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        return CATEGORY_ALIASES.get(v, v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.5
        if v > 1:
            v = v / 100 if v <= 100 else 1.0
        return max(0.0, min(1.0, v))

    @field_validator("suggestion", mode="before")
    @classmethod
    def _sug(cls, v):
        if v is None:
            return None
        v = str(v).strip()
        return v or None


def review_json_schema() -> dict:
    """JSON schema passed to Ollama's structured-output `format` parameter."""
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer"},
                        "severity": {"type": "string", "enum": [s.value for s in Severity]},
                        "category": {"type": "string", "enum": [c.value for c in Category]},
                        "title": {"type": "string"},
                        "message": {"type": "string"},
                        "suggestion": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["line", "severity", "category", "title", "message", "confidence"],
                },
            }
        },
        "required": ["findings"],
    }


def summary_json_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "overview": {"type": "string"},
            "key_risks": {"type": "array", "items": {"type": "string"}},
            "positives": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["overview", "key_risks"],
    }


def parse_llm_findings(raw: dict) -> list[LLMFinding]:
    """Leniently parse an LLM response; invalid entries are dropped, never fatal."""
    out: list[LLMFinding] = []
    items = raw.get("findings") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return out
    for item in items:
        try:
            out.append(LLMFinding.model_validate(item))
        except (ValidationError, TypeError) as exc:
            log.debug("dropping invalid finding %r: %s", item, exc)
    return out


@dataclass
class Finding:
    file: str
    line: int
    severity: Severity
    category: Category
    title: str
    message: str
    suggestion: str | None = None
    confidence: float = 0.7
    source: str = "llm"  # llm | ruff | semgrep
    rule_id: str | None = None
    anchor_text: str = ""  # text of the diff line the comment is anchored to
    placeable: bool = True  # False if no diff line could be matched
    disposition: str = "new"  # new | existing | overflow | unplaced | post_failed

    @property
    def fingerprint(self) -> str:
        """Stable across re-runs/line shifts: file + category + rule + anchored code."""
        raw = f"{self.file}|{self.category.value}|{self.rule_id or ''}|{self.anchor_text.strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def rank(self) -> int:
        return SEVERITY_RANK[self.severity]
