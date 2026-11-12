"""Review orchestrator: ties GitLab, the LLM, static analysis, the gate and publishing together."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import yaml

from .config import Settings
from .db import FindingRow, Review
from .diff_parser import DiffLine, added_line_numbers, flatten, parse_diff
from .gitlab_client import GitLabClient, GitLabError, NotFound
from .language import detect_language, is_test_file, should_skip
from .llm_review import LLMReviewer
from .models import Finding, Severity, SEVERITY_RANK, summary_json_schema
from .ollama_client import OllamaError
from .prompts import SUMMARY_SYSTEM, build_summary_prompt
from .publisher import build_summary, format_inline_comment
from .quality_gate import GateConfig, GateResult, count_by_severity, evaluate

log = logging.getLogger(__name__)

STATUS_NAME = "ai-code-review"


def load_repo_config(gl: GitLabClient, project_id: int, ref: str) -> dict:
    """Optional `.ai-review.yml` in the repo root (ignore globs, instructions, gate overrides)."""
    try:
        raw = gl.get_file_raw(project_id, ".ai-review.yml", ref)
        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except (NotFound, yaml.YAMLError):
        return {}
    except GitLabError as exc:
        log.warning("could not read .ai-review.yml: %s", exc)
        return {}


class Orchestrator:
    def __init__(self, settings: Settings, gitlab: GitLabClient, ollama, session_factory, analyzer=None):
        self.s = settings
        self.gl = gitlab
        self.ollama = ollama
        self.sf = session_factory
        self.analyzer = analyzer

    # ------------------------------------------------------------------ public
    def run(self, project_id: int, mr_iid: int, trigger: str = "webhook") -> dict:
        t0 = time.time()
        mr = self.gl.get_mr(project_id, mr_iid)
        version = self.gl.latest_version(project_id, mr_iid)
        refs = {
            "base_sha": version["base_commit_sha"],
            "start_sha": version["start_commit_sha"],
            "head_sha": version["head_commit_sha"],
        }
        head_sha = refs["head_sha"]

        if trigger != "manual" and self._already_reviewed(project_id, mr_iid, head_sha):
            log.info("MR !%s @ %s already reviewed; skipping", mr_iid, head_sha[:8])
            return {"status": "skipped", "reason": "already reviewed"}

        pipeline = mr.get("head_pipeline") or {}
        pipeline_id = pipeline.get("id") if pipeline.get("sha") == head_sha else None
        post_status = lambda state, desc: self._status(  # noqa: E731
            project_id, head_sha, state, desc, mr, pipeline_id
        )

        review_id = self._start_review(project_id, mr_iid, head_sha, trigger)
        post_status("running", "AI code review in progress")
        try:
            result = self._review(project_id, mr_iid, mr, refs, review_id, t0)
        except Exception as exc:  # noqa: BLE001
            log.exception("review failed")
            self._fail_review(review_id, str(exc))
            state = "success" if self.s.gate_on_error == "pass" else "failed"
            post_status(state, f"AI review could not complete: {exc}"[:200])
            raise
        post_status(result["gate"].state, result["gate"].description)
        return {
            "status": "done",
            "review_id": review_id,
            "gate": result["gate"].state,
            "counts": result["gate"].counts,
        }

    # ----------------------------------------------------------------- internals
    def _review(self, project_id: int, mr_iid: int, mr: dict, refs: dict, review_id: int, t0: float) -> dict:
        s, gl = self.s, self.gl
        head_sha = refs["head_sha"]
        repo_cfg = load_repo_config(gl, project_id, head_sha)
        instructions = str(repo_cfg.get("instructions", "") or "")
        gate_cfg = GateConfig.from_settings(s, repo_cfg.get("gate"))

        # 1. select files -------------------------------------------------------
        reviewable: list[dict] = []
        skipped: list[tuple[str, str]] = []
        for d in gl.get_mr_diffs(project_id, mr_iid):
            path = d.get("new_path") or d.get("old_path")
            if d.get("deleted_file") or not path:
                continue
            language = detect_language(path)
            if not language:
                continue
            reason = should_skip(path, repo_cfg.get("ignore"))
            if reason:
                skipped.append((path, reason))
                continue
            text = d.get("diff") or ""
            if not text.strip() or d.get("too_large") or d.get("collapsed"):
                skipped.append((path, "diff empty or too large"))
                continue
            reviewable.append({"path": path, "old_path": d.get("old_path") or path, "language": language, "diff": text})
        if len(reviewable) > s.max_files_per_review:
            for item in reviewable[s.max_files_per_review :]:
                skipped.append((item["path"], "file limit reached"))
            reviewable = reviewable[: s.max_files_per_review]

        # 2. parse diffs ------------------------------------------------------------
        lines_by_file: dict[str, list[DiffLine]] = {}
        for item in reviewable:
            lines = flatten(parse_diff(item["diff"]))
            if len(lines) > s.max_diff_lines_per_file:
                lines = lines[: s.max_diff_lines_per_file]
                skipped.append((item["path"], f"truncated to {s.max_diff_lines_per_file} diff lines"))
            if added_line_numbers(lines):
                lines_by_file[item["path"]] = lines
        reviewable = [r for r in reviewable if r["path"] in lines_by_file]
        old_paths = {r["path"]: r["old_path"] for r in reviewable}

        # 3. static analysis ------------------------------------------------------------
        static_all = self._static_findings(project_id, head_sha, reviewable, lines_by_file)
        static_by_file: dict[str, list[Finding]] = {}
        for f in static_all:
            static_by_file.setdefault(f.file, []).append(f)

        # 4. LLM review ------------------------------------------------------------------
        llm = LLMReviewer(s, self.ollama)
        llm_findings: list[Finding] = []
        for idx, item in enumerate(reviewable, 1):
            log.info("reviewing %s (%d/%d)", item["path"], idx, len(reviewable))
            llm_findings += llm.review_file(
                path=item["path"],
                language=item["language"],
                is_test=is_test_file(item["path"]),
                lines=lines_by_file[item["path"]],
                mr_title=mr.get("title", ""),
                mr_description=mr.get("description") or "",
                instructions=instructions,
                static_hints=static_by_file.get(item["path"], []),
            )

        # 5. merge, filter, rank -------------------------------------------------------------
        static_post = [f for f in static_all if f.rank <= SEVERITY_RANK[Severity.medium]]
        findings = self._merge(static_post, llm_findings)
        findings = [f for f in findings if f.confidence >= s.min_confidence]
        findings.sort(key=lambda f: (f.rank, -f.confidence))
        gate = evaluate(findings, gate_cfg)

        # 6. publish inline comments -------------------------------------------------------------
        self._publish_inline(project_id, mr_iid, refs, findings, old_paths)

        # 7. summary note ---------------------------------------------------------------------------
        overview, risks, positives = self._overview(mr, findings, len(reviewable))
        duration = time.time() - t0
        summary = build_summary(
            mr_web_url=mr.get("web_url", ""),
            head_sha=head_sha,
            model=s.ollama_model,
            gate=gate,
            findings=findings,
            overview=overview,
            key_risks=risks,
            positives=positives,
            files_reviewed=len(reviewable),
            skipped=skipped,
            llm_errors=llm.errors,
            duration_s=duration,
        )
        gl.upsert_summary_note(project_id, mr_iid, summary)

        self._finish_review(review_id, gate, findings, len(reviewable), len(skipped), summary, duration)
        return {"gate": gate}

    # -- helpers ------------------------------------------------------------------------
    def _static_findings(self, project_id, head_sha, reviewable, lines_by_file) -> list[Finding]:
        if not self.analyzer or not self.s.static_analysis_enabled or not reviewable:
            return []
        contents: dict[str, str] = {}
        for item in reviewable:
            try:
                contents[item["path"]] = self.gl.get_file_raw(project_id, item["path"], head_sha)
            except GitLabError as exc:
                log.warning("cannot fetch %s for static analysis: %s", item["path"], exc)
        try:
            found = self.analyzer.run(contents)
        except Exception as exc:  # noqa: BLE001 - analyzers must never break a review
            log.warning("static analysis failed: %s", exc)
            return []
        kept: list[Finding] = []
        for f in found:
            lines = lines_by_file.get(f.file)
            if not lines:
                continue
            by_no = {ln.new_no: ln for ln in lines if ln.kind == "+"}
            hit = by_no.get(f.line)
            if hit:  # only report on lines touched by this MR
                f.anchor_text = hit.text
                kept.append(f)
        return kept

    @staticmethod
    def _merge(static: list[Finding], llm: list[Finding]) -> list[Finding]:
        merged: list[Finding] = []
        seen_fp: set[str] = set()
        seen_loc: set[tuple[str, int, str]] = set()
        for f in [*static, *llm]:  # static first: it wins on duplicates
            key = (f.file, f.line, f.category.value)
            if f.fingerprint in seen_fp or key in seen_loc:
                continue
            seen_fp.add(f.fingerprint)
            seen_loc.add(key)
            merged.append(f)
        return merged

    def _publish_inline(self, project_id, mr_iid, refs, findings: list[Finding], old_paths: dict[str, str]) -> None:
        s = self.s
        min_rank = SEVERITY_RANK[Severity(s.inline_min_severity)]
        try:
            existing = self.gl.existing_fingerprints(project_id, mr_iid)
        except GitLabError as exc:
            log.warning("could not list existing discussions: %s", exc)
            existing = set()
        posted = 0
        for f in findings:
            if not f.placeable:
                f.disposition = "unplaced"
            elif f.rank > min_rank:
                f.disposition = "overflow" if f.severity != Severity.info else "overflow"
            elif f.fingerprint in existing:
                f.disposition = "existing"
            elif posted >= s.max_inline_comments:
                f.disposition = "overflow"
            else:
                try:
                    self.gl.create_inline_discussion(
                        project_id,
                        mr_iid,
                        format_inline_comment(f),
                        refs,
                        new_path=f.file,
                        old_path=old_paths.get(f.file, f.file),
                        new_line=f.line,
                    )
                    f.disposition = "new"
                    posted += 1
                except GitLabError as exc:
                    log.warning("inline comment failed for %s:%s: %s", f.file, f.line, exc)
                    f.disposition = "post_failed"
        # Only findings that are not already visible inline need to be listed in the summary.
        for f in findings:
            if f.disposition == "overflow" and f.rank > min_rank:
                f.disposition = "overflow"

    def _overview(self, mr: dict, findings: list[Finding], n_files: int) -> tuple[str | None, list[str], list[str]]:
        if not self.s.use_llm_summary:
            return self._fallback_overview(findings, n_files), [], []
        stats = f"{n_files} files reviewed, {len(findings)} findings {count_by_severity(findings)}"
        try:
            raw = self.ollama.chat_json(
                SUMMARY_SYSTEM, build_summary_prompt(mr.get("title", ""), stats, findings), summary_json_schema()
            )
            overview = str(raw.get("overview", "")).strip() or None
            risks = [str(x) for x in raw.get("key_risks", []) if str(x).strip()]
            positives = [str(x) for x in raw.get("positives", []) if str(x).strip()]
            if findings == [] and not overview:
                overview = self._fallback_overview(findings, n_files)
            return overview, risks, positives if not findings else []
        except (OllamaError, AttributeError, TypeError) as exc:
            log.warning("LLM summary failed, using fallback: %s", exc)
            return self._fallback_overview(findings, n_files), [], []

    @staticmethod
    def _fallback_overview(findings: list[Finding], n_files: int) -> str:
        if not findings:
            return f"Reviewed {n_files} file(s) and found no issues above the confidence threshold."
        return f"Reviewed {n_files} file(s) and found {len(findings)} potential issue(s)."

    def _status(self, project_id, sha, state, desc, mr, pipeline_id) -> None:
        try:
            self.gl.post_commit_status(
                project_id,
                sha,
                state,
                desc,
                name=STATUS_NAME,
                ref=mr.get("source_branch"),
                target_url=mr.get("web_url"),
                pipeline_id=pipeline_id,
            )
        except GitLabError as exc:
            log.warning("could not post commit status (%s): %s", state, exc)

    # -- persistence --------------------------------------------------------------------
    def _already_reviewed(self, project_id: int, mr_iid: int, head_sha: str) -> bool:
        with self.sf() as db:
            row = (
                db.query(Review)
                .filter_by(project_id=project_id, mr_iid=mr_iid, head_sha=head_sha, status="done")
                .first()
            )
            return row is not None

    def _start_review(self, project_id, mr_iid, head_sha, trigger) -> int:
        with self.sf() as db:
            row = Review(
                project_id=project_id, mr_iid=mr_iid, head_sha=head_sha, trigger=trigger, model=self.s.ollama_model
            )
            db.add(row)
            db.commit()
            return row.id

    def _fail_review(self, review_id: int, error: str) -> None:
        with self.sf() as db:
            row = db.get(Review, review_id)
            if row:
                row.status = "error"
                row.error = error[:2000]
                row.finished_at = datetime.now(timezone.utc)
                db.commit()

    def _finish_review(
        self, review_id, gate: GateResult, findings: list[Finding], reviewed, skipped, summary, duration
    ) -> None:
        with self.sf() as db:
            row = db.get(Review, review_id)
            row.status = "done"
            row.gate_state = gate.state
            row.gate_passed = gate.passed
            row.files_reviewed = reviewed
            row.files_skipped = skipped
            row.findings_total = len(findings)
            row.counts_json = json.dumps(gate.counts)
            row.summary_md = summary
            row.finished_at = datetime.now(timezone.utc)
            row.duration_s = duration
            for f in findings:
                row.findings.append(
                    FindingRow(
                        fingerprint=f.fingerprint,
                        file=f.file,
                        line=f.line,
                        severity=f.severity.value,
                        category=f.category.value,
                        title=f.title,
                        message=f.message,
                        suggestion=f.suggestion,
                        confidence=f.confidence,
                        source=f.source,
                        disposition=f.disposition,
                    )
                )
            db.commit()
