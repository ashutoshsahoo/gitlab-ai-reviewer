"""Split a file's diff lines into prompt-sized chunks (with a little overlap)."""

from __future__ import annotations

from .diff_parser import DiffLine

_OVERHEAD = 12  # line-number gutter etc.


def _size(line: DiffLine) -> int:
    return len(line.text) + _OVERHEAD


def chunk_lines(lines: list[DiffLine], max_chars: int, overlap: int = 8) -> list[list[DiffLine]]:
    chunks: list[list[DiffLine]] = []
    cur: list[DiffLine] = []
    size = 0
    for ln in lines:
        n = _size(ln)
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur = cur[-overlap:] if overlap else []
            size = sum(_size(x) for x in cur)
        cur.append(ln)
        size += n
    if cur and any(x.kind == "+" for x in cur):
        chunks.append(cur)
    return chunks
