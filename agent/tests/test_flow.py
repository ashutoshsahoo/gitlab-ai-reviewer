"""End-to-end orchestrator test with fake GitLab + fake Ollama + SQLite."""

from app.config import Settings
from app.db import Review, init_db, make_session_factory
from app.gitlab_client import FP_MARKER_RE, GitLabError
from app.reviewer import Orchestrator

DIFF = """@@ -0,0 +1,6 @@
+import sqlite3
+
+def get(conn, name):
+    cur = conn.cursor()
+    cur.execute("SELECT * FROM t WHERE n = '" + name + "'")
+    return cur.fetchone()
"""


class FakeGitLab:
    def __init__(self):
        self.discussions, self.notes, self.statuses = [], [], []
        self.fail_inline = False

    def get_mr(self, p, i):
        return {
            "title": "Add getter",
            "description": "",
            "web_url": "http://gl/g/p/-/merge_requests/1",
            "source_branch": "feat",
            "head_pipeline": None,
        }

    def latest_version(self, p, i):
        return {"base_commit_sha": "b" * 40, "start_commit_sha": "s" * 40, "head_commit_sha": "h" * 40}

    def get_mr_diffs(self, p, i):
        return [
            {"new_path": "app/db.py", "old_path": "app/db.py", "diff": DIFF, "new_file": True},
            {"new_path": "README.md", "old_path": "README.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"},
            {"new_path": "web/node_modules/x.js", "old_path": "web/node_modules/x.js", "diff": DIFF},
        ]

    def get_file_raw(self, p, path, ref):
        raise GitLabError(404, "none")  # no .ai-review.yml, no static analysis content

    def existing_fingerprints(self, p, i):
        found = set()
        for d in self.discussions:
            found.update(FP_MARKER_RE.findall(d["body"]))
        return found

    def create_inline_discussion(self, p, i, body, refs, new_path, old_path, new_line):
        if self.fail_inline:
            raise GitLabError(400, "bad position")
        self.discussions.append({"body": body, "path": new_path, "line": new_line})

    def upsert_summary_note(self, p, i, body):
        self.notes.append(body)

    def post_commit_status(self, p, sha, state, desc, **kw):
        self.statuses.append((state, desc))


class FakeOllama:
    def __init__(self):
        self.calls = 0

    def chat_json(self, system, user, schema=None):
        self.calls += 1
        if "findings" in schema["properties"]:
            return {
                "findings": [
                    {
                        "line": 5,
                        "severity": "critical",
                        "category": "security",
                        "title": "SQL injection",
                        "message": "User input is concatenated into SQL.",
                        "suggestion": 'cur.execute("SELECT * FROM t WHERE n = ?", (name,))',
                        "confidence": 0.95,
                    },
                    {"line": 1, "severity": "low", "category": "style", "title": "meh", "message": "m", "confidence": 0.2},
                ]
            }
        return {"overview": "One serious issue.", "key_risks": ["SQL injection"], "positives": []}


def make(gl=None):
    s = Settings(database_url="sqlite+pysqlite:///:memory:")
    sf = make_session_factory(s)
    init_db(sf)
    gl = gl or FakeGitLab()
    ollama = FakeOllama()
    return Orchestrator(s, gl, ollama, sf, analyzer=None), gl, ollama, sf


def test_full_review_posts_inline_summary_and_failing_gate():
    orch, gl, ollama, sf = make()
    res = orch.run(1, 1)
    assert res["status"] == "done" and res["gate"] == "failed"
    assert ollama.calls == 2  # one file reviewed (README + node_modules skipped) + summary
    assert len(gl.discussions) == 1
    assert gl.discussions[0]["line"] == 5 and gl.discussions[0]["path"] == "app/db.py"
    assert "SQL injection" in gl.discussions[0]["body"] and "ai-review:fp=" in gl.discussions[0]["body"]
    assert "Quality gate failed" in gl.notes[0] and "node_modules" in gl.notes[0]
    assert [s for s, _ in gl.statuses] == ["running", "failed"]
    with sf() as db:
        row = db.query(Review).one()
        assert row.status == "done" and row.findings_total == 1  # low-confidence finding filtered


def test_rereview_same_sha_is_skipped_and_manual_does_not_duplicate_comments():
    orch, gl, _, _ = make()
    orch.run(1, 1)
    assert orch.run(1, 1)["status"] == "skipped"
    orch.run(1, 1, trigger="manual")
    assert len(gl.discussions) == 1  # fingerprint marker prevents duplicates


def test_inline_failure_falls_back_to_summary():
    gl = FakeGitLab()
    gl.fail_inline = True
    orch, gl, _, _ = make(gl)
    orch.run(1, 1)
    assert gl.discussions == []
    assert "not posted inline" in gl.notes[0] and "SQL injection" in gl.notes[0]


def test_error_reports_status_per_gate_on_error_setting():
    orch, gl, _, sf = make()
    orch.gl.get_mr_diffs = lambda p, i: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        orch.run(1, 1)
    except RuntimeError:
        pass
    assert gl.statuses[-1][0] == "success" and "could not complete" in gl.statuses[-1][1]
    with sf() as db:
        assert db.query(Review).one().status == "error"
