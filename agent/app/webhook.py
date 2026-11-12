"""Pure routing logic: GitLab webhook payload -> review request (or None)."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass
class ReviewRequest:
    project_id: int
    mr_iid: int
    head_sha: str
    trigger: str  # webhook | manual


def route_event(event: str | None, payload: dict, settings: Settings) -> ReviewRequest | None:
    if event == "Merge Request Hook":
        attrs = payload.get("object_attributes", {})
        if attrs.get("state") not in (None, "opened"):
            return None
        if (attrs.get("draft") or attrs.get("work_in_progress")) and not settings.review_drafts:
            return None
        action = attrs.get("action")
        # "update" fires for title/label edits too; only a push carries `oldrev`.
        if action in ("open", "reopen") or (action == "update" and "oldrev" in attrs):
            sha = (attrs.get("last_commit") or {}).get("id", "")
            return ReviewRequest(payload["project"]["id"], attrs["iid"], sha, "webhook")
        return None

    if event == "Note Hook":
        attrs = payload.get("object_attributes", {})
        if attrs.get("noteable_type") != "MergeRequest":
            return None
        user = (payload.get("user") or {}).get("username", "")
        if user == settings.gitlab_bot_username:
            return None
        body = attrs.get("note", "")
        if not any(line.strip().startswith("/ai-review") for line in body.splitlines()):
            return None
        mr = payload.get("merge_request", {})
        sha = (mr.get("last_commit") or {}).get("id", "")
        return ReviewRequest(payload["project"]["id"], mr["iid"], sha, "manual")

    return None
