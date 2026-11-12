"""Unified-diff parsing with new-file line numbers (needed for inline comments)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class DiffLine:
    kind: str  # "+" added, "-" removed, " " context
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


def parse_diff(diff: str) -> list[Hunk]:
    """Parse GitLab's per-file diff text (starts at the first `@@` header)."""
    hunks: list[Hunk] = []
    cur: Hunk | None = None
    old_no = new_no = 0
    for raw in diff.splitlines():
        m = HUNK_RE.match(raw)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(2))
            cur = Hunk(header=raw)
            hunks.append(cur)
            continue
        if cur is None or raw.startswith("\\"):  # file headers / "\ No newline at end of file"
            continue
        if raw.startswith("+"):
            cur.lines.append(DiffLine("+", None, new_no, raw[1:]))
            new_no += 1
        elif raw.startswith("-"):
            cur.lines.append(DiffLine("-", old_no, None, raw[1:]))
            old_no += 1
        else:
            text = raw[1:] if raw.startswith(" ") else raw
            cur.lines.append(DiffLine(" ", old_no, new_no, text))
            old_no += 1
            new_no += 1
    return hunks


def flatten(hunks: Iterable[Hunk]) -> list[DiffLine]:
    return [line for h in hunks for line in h.lines]


def render_lines(lines: Iterable[DiffLine]) -> str:
    """Render for the LLM: the left column is the NEW file line number."""
    out: list[str] = []
    prev_new: int | None = None
    for ln in lines:
        if ln.new_no is not None and prev_new is not None and ln.new_no != prev_new + 1:
            out.append("  ...")
        if ln.kind == "+":
            out.append(f"{ln.new_no:>5} + {ln.text}")
        elif ln.kind == " ":
            out.append(f"{ln.new_no:>5}   {ln.text}")
        else:
            out.append(f"      - {ln.text}")
        if ln.new_no is not None:
            prev_new = ln.new_no
    return "\n".join(out)


def added_line_numbers(lines: Iterable[DiffLine]) -> set[int]:
    return {ln.new_no for ln in lines if ln.kind == "+" and ln.new_no is not None}
