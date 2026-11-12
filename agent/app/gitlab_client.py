"""Minimal GitLab REST v4 client (only what the agent needs)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

FP_MARKER_RE = re.compile(r"ai-review:fp=([0-9a-f]{12})")
SUMMARY_MARKER = "<!-- ai-review-summary -->"


class GitLabError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitLab API error {status}: {message}")
        self.status = status


class NotFound(GitLabError):
    pass


def enc(project: int | str) -> str:
    return quote(str(project), safe="")


class GitLabClient:
    def __init__(self, base_url: str, token: str, verify_ssl: bool = True, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(
            base_url=f"{self.base}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
            verify=verify_ssl,
        )

    # -- low level ---------------------------------------------------------
    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self.http.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 500 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404:
                raise NotFound(404, r.text[:300])
            if r.status_code >= 400:
                raise GitLabError(r.status_code, r.text[:500])
            return r
        raise GitLabError(0, f"transport error: {last}")

    def paginate(self, path: str, params: dict | None = None, max_pages: int = 20) -> Iterator[Any]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1
        while page and page <= max_pages:
            params["page"] = page
            r = self.request("GET", path, params=params)
            yield from r.json()
            nxt = r.headers.get("X-Next-Page", "")
            page = int(nxt) if nxt.isdigit() else 0

    # -- merge requests ----------------------------------------------------------
    def get_mr(self, project: int | str, iid: int) -> dict:
        return self.request("GET", f"/projects/{enc(project)}/merge_requests/{iid}").json()

    def latest_version(self, project: int | str, iid: int) -> dict:
        r = self.request("GET", f"/projects/{enc(project)}/merge_requests/{iid}/versions")
        versions = r.json()
        if not versions:
            raise GitLabError(0, "merge request has no diff versions yet")
        return versions[0]

    def get_mr_diffs(self, project: int | str, iid: int) -> list[dict]:
        try:
            return list(self.paginate(f"/projects/{enc(project)}/merge_requests/{iid}/diffs"))
        except NotFound:  # GitLab < 15.7
            r = self.request("GET", f"/projects/{enc(project)}/merge_requests/{iid}/changes")
            return r.json().get("changes", [])

    def get_file_raw(self, project: int | str, path: str, ref: str) -> str:
        r = self.request(
            "GET",
            f"/projects/{enc(project)}/repository/files/{quote(path, safe='')}/raw",
            params={"ref": ref},
        )
        return r.text

    # -- notes / discussions ------------------------------------------------------
    def existing_fingerprints(self, project: int | str, iid: int) -> set[str]:
        found: set[str] = set()
        for disc in self.paginate(f"/projects/{enc(project)}/merge_requests/{iid}/discussions"):
            for note in disc.get("notes", []):
                found.update(FP_MARKER_RE.findall(note.get("body", "")))
        return found

    def create_inline_discussion(
        self, project: int | str, iid: int, body: str, refs: dict, new_path: str, old_path: str, new_line: int
    ) -> dict:
        payload = {
            "body": body,
            "position": {
                "position_type": "text",
                "base_sha": refs["base_sha"],
                "start_sha": refs["start_sha"],
                "head_sha": refs["head_sha"],
                "new_path": new_path,
                "old_path": old_path,
                "new_line": new_line,
            },
        }
        return self.request("POST", f"/projects/{enc(project)}/merge_requests/{iid}/discussions", json=payload).json()

    def upsert_summary_note(self, project: int | str, iid: int, body: str) -> None:
        notes = self.paginate(
            f"/projects/{enc(project)}/merge_requests/{iid}/notes", {"sort": "desc", "order_by": "created_at"}
        )
        for note in notes:
            if SUMMARY_MARKER in note.get("body", "") and not note.get("system"):
                self.request(
                    "PUT", f"/projects/{enc(project)}/merge_requests/{iid}/notes/{note['id']}", json={"body": body}
                )
                return
        self.request("POST", f"/projects/{enc(project)}/merge_requests/{iid}/notes", json={"body": body})

    # -- commit status (quality gate) ---------------------------------------------
    def post_commit_status(
        self,
        project: int | str,
        sha: str,
        state: str,
        description: str,
        *,
        name: str = "ai-code-review",
        ref: str | None = None,
        target_url: str | None = None,
        pipeline_id: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"state": state, "name": name, "description": description[:255]}
        if ref:
            payload["ref"] = ref
        if target_url:
            payload["target_url"] = target_url
        if pipeline_id:
            payload["pipeline_id"] = pipeline_id
        self.request("POST", f"/projects/{enc(project)}/statuses/{sha}", json=payload)
