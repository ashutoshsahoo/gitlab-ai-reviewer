from app.chunker import chunk_lines
from app.config import Settings
from app.diff_parser import added_line_numbers, flatten, parse_diff, render_lines
from app.language import detect_language, is_test_file, should_skip
from app.llm_review import anchor_line
from app.models import Category, Finding, Severity, parse_llm_findings
from app.ollama_client import extract_json
from app.quality_gate import GateConfig, evaluate
from app.webhook import route_event

DIFF = """@@ -1,3 +1,5 @@
 import os
-import sys
+import json
+import re

@@ -10,2 +12,3 @@ def f():
     x = 1
+    y = eval(x)
     return x
"""


def test_parse_diff_line_numbers():
    lines = flatten(parse_diff(DIFF))
    added = {ln.new_no: ln.text for ln in lines if ln.kind == "+"}
    assert added == {2: "import json", 3: "import re", 13: "    y = eval(x)"}
    assert added_line_numbers(lines) == {2, 3, 13}
    removed = [ln for ln in lines if ln.kind == "-"]
    assert removed[0].old_no == 2 and removed[0].new_no is None


def test_render_has_gap_marker_and_numbers():
    text = render_lines(flatten(parse_diff(DIFF)))
    assert "    2 + import json" in text
    assert "  ..." in text
    assert "   13 +     y = eval(x)" in text


def test_chunker_splits_and_keeps_added_lines():
    lines = flatten(parse_diff(DIFF))
    chunks = chunk_lines(lines, max_chars=60, overlap=1)
    assert len(chunks) > 1
    covered = {ln.new_no for c in chunks for ln in c if ln.kind == "+"}
    assert covered == {2, 3, 13}


def test_anchor_snaps_to_nearby_added_line():
    line_map = {ln.new_no: ln for ln in flatten(parse_diff(DIFF)) if ln.new_no}
    assert anchor_line(line_map, 13).new_no == 13
    assert anchor_line(line_map, 12).new_no == 13  # context line -> nearest added
    assert anchor_line(line_map, 500) is None


def test_language_and_skip_rules():
    assert detect_language("src/A.java") == "java"
    assert detect_language("a/b.tsx") == "typescript"
    assert detect_language("README.md") is None
    assert should_skip("web/node_modules/x/index.js")
    assert should_skip("app/types.d.ts")
    assert should_skip("src/gen/a.py", ["src/gen/*"])
    assert should_skip("src/app.py") is None
    assert is_test_file("tests/test_x.py") and is_test_file("src/foo.spec.ts")


def test_llm_output_is_normalised_and_invalid_dropped():
    raw = {
        "findings": [
            {"line": 3, "severity": "Major", "category": "Bug", "title": "t", "message": "m", "confidence": 85},
            {"line": "x", "severity": "high", "category": "security", "title": "t", "message": "m", "confidence": 1},
            {"line": 4, "severity": "weird", "category": "security", "title": "t", "message": "m", "confidence": 1},
        ]
    }
    out = parse_llm_findings(raw)
    assert len(out) == 1
    assert out[0].severity == Severity.high and out[0].category == Category.correctness
    assert out[0].confidence == 0.85


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('here:\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('noise {"a": 3} trailing') == {"a": 3}


def _f(sev, line=1):
    return Finding("a.py", line, sev, Category.security, "t", "m", anchor_text=f"x{line}")


def test_quality_gate_thresholds():
    cfg = GateConfig(max_critical=0, max_high=0, max_medium=1)
    assert evaluate([_f(Severity.low), _f(Severity.medium)], cfg).passed
    assert not evaluate([_f(Severity.medium, 1), _f(Severity.medium, 2)], cfg).passed
    res = evaluate([_f(Severity.high)], cfg)
    assert not res.passed and res.state == "failed" and "1 high" in res.reasons[0]
    adv = evaluate([_f(Severity.critical)], GateConfig(mode="advisory"))
    assert adv.passed and adv.reasons


def test_fingerprint_is_stable_across_line_shifts():
    a, b = _f(Severity.high, 5), _f(Severity.high, 5)
    b.line = 99
    assert a.fingerprint == b.fingerprint


def test_webhook_routing():
    s = Settings()
    mr = {
        "project": {"id": 7},
        "object_attributes": {"iid": 3, "action": "open", "state": "opened", "last_commit": {"id": "abc"}},
    }
    assert route_event("Merge Request Hook", mr, s).mr_iid == 3
    upd = {"project": {"id": 7}, "object_attributes": {"iid": 3, "action": "update", "state": "opened"}}
    assert route_event("Merge Request Hook", upd, s) is None  # title edit, no new commits
    upd["object_attributes"]["oldrev"] = "old"
    assert route_event("Merge Request Hook", upd, s) is not None
    draft = {"project": {"id": 7}, "object_attributes": {"iid": 3, "action": "open", "draft": True}}
    assert route_event("Merge Request Hook", draft, s) is None
    note = {
        "project": {"id": 7},
        "user": {"username": "alice"},
        "object_attributes": {"noteable_type": "MergeRequest", "note": "please\n/ai-review"},
        "merge_request": {"iid": 3, "last_commit": {"id": "abc"}},
    }
    assert route_event("Note Hook", note, s).trigger == "manual"
    note["user"]["username"] = s.gitlab_bot_username
    assert route_event("Note Hook", note, s) is None
