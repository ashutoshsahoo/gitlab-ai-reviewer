"""Language detection and file filtering."""

from __future__ import annotations

import fnmatch
import os

EXT_LANG = {
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

SKIP_PARTS = (
    "/node_modules/",
    "/dist/",
    "/build/",
    "/target/",
    "/vendor/",
    "/__pycache__/",
    "/.git/",
    "/generated/",
    "/.venv/",
)
SKIP_SUFFIXES = (".min.js", ".d.ts", "_pb2.py", "_pb2_grpc.py", ".bundle.js", ".map")


def detect_language(path: str) -> str | None:
    return EXT_LANG.get(os.path.splitext(path)[1].lower())


def should_skip(path: str, extra_ignore: list[str] | None = None) -> str | None:
    """Return a human-readable skip reason, or None if the file should be reviewed."""
    p = "/" + path.lstrip("/")
    for part in SKIP_PARTS:
        if part in p:
            return f"ignored directory ({part.strip('/')})"
    if path.endswith(SKIP_SUFFIXES):
        return "generated/minified file"
    for pattern in extra_ignore or []:
        if fnmatch.fnmatch(path, pattern):
            return f"ignored by .ai-review.yml ({pattern})"
    return None


def is_test_file(path: str) -> bool:
    name = os.path.basename(path).lower()
    p = "/" + path.lower()
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".test.tsx", ".spec.js", ".spec.ts", ".spec.tsx"))
        or name.endswith(("test.java", "tests.java", "it.java"))
        or "/tests/" in p
        or "/test/" in p
        or "/__tests__/" in p
    )
