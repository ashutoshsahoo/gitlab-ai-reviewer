"""LLM-backed review of a single file's diff."""

from __future__ import annotations

import logging

from .chunker import chunk_lines
from .config import Settings
from .diff_parser import DiffLine, render_lines
from .models import Finding, parse_llm_findings, review_json_schema
from .ollama_client import OllamaError
from .prompts import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger(__name__)


def anchor_line(line_map: dict[int, DiffLine], line: int) -> DiffLine | None:
    """Map the model's line number to an ADDED diff line (exact, else +-2 lines)."""
    hit = line_map.get(line)
    if hit and hit.kind == "+":
        return hit
    for delta in (1, -1, 2, -2):
        cand = line_map.get(line + delta)
        if cand and cand.kind == "+":
            return cand
    return None


class LLMReviewer:
    def __init__(self, settings: Settings, ollama):
        self.s = settings
        self.ollama = ollama
        self.errors = 0

    def review_file(
        self,
        *,
        path: str,
        language: str,
        is_test: bool,
        lines: list[DiffLine],
        mr_title: str,
        mr_description: str,
        instructions: str = "",
        static_hints: list[Finding] | None = None,
    ) -> list[Finding]:
        line_map = {ln.new_no: ln for ln in lines if ln.new_no is not None}
        chunks = chunk_lines(lines, self.s.max_prompt_chars)
        results: list[Finding] = []
        for i, chunk in enumerate(chunks, 1):
            nums = [x.new_no for x in chunk if x.new_no is not None]
            lo, hi = (min(nums), max(nums)) if nums else (0, 0)
            hints = [h for h in (static_hints or []) if lo <= h.line <= hi]
            prompt = build_user_prompt(
                path=path,
                language=language,
                is_test=is_test,
                mr_title=mr_title,
                mr_description=mr_description,
                diff_text=render_lines(chunk),
                instructions=instructions,
                static_hints=hints,
                chunk_info=f"part {i}/{len(chunks)}" if len(chunks) > 1 else "",
            )
            try:
                raw = self.ollama.chat_json(SYSTEM_PROMPT, prompt, review_json_schema())
            except OllamaError as exc:
                self.errors += 1
                log.error("LLM review failed for %s (part %d): %s", path, i, exc)
                continue
            for lf in parse_llm_findings(raw):
                anchor = anchor_line(line_map, lf.line)
                results.append(
                    Finding(
                        file=path,
                        line=anchor.new_no if anchor else lf.line,
                        severity=lf.severity,
                        category=lf.category,
                        title=lf.title.strip()[:120],
                        message=lf.message.strip(),
                        suggestion=lf.suggestion,
                        confidence=lf.confidence,
                        source="llm",
                        anchor_text=anchor.text if anchor else "",
                        placeable=anchor is not None,
                    )
                )
        return results
