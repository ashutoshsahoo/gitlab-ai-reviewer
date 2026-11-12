"""FastAPI service: receives GitLab webhooks and exposes a small admin API."""

from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .config import get_settings
from .db import Review, init_db, make_session_factory
from .gitlab_client import GitLabClient
from .ollama_client import OllamaClient
from .queueing import enqueue_review, get_redis
from .webhook import ReviewRequest, route_event

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app.main")
session_factory = make_session_factory(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(session_factory)
    yield


app = FastAPI(title="GitLab AI Code Review Agent", version="0.1.0", lifespan=lifespan)


def _check_secret(provided: str | None) -> None:
    if not provided or not hmac.compare_digest(provided, settings.gitlab_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/livez")
def livez() -> dict:
    return {"status": "alive"}


@app.get("/healthz")
def healthz() -> dict:
    checks: dict[str, str] = {}
    try:
        get_redis(settings).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"
    try:
        with session_factory() as db:
            db.query(Review).limit(1).all()
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc}"
    ok, msg = OllamaClient(settings).health()
    checks["ollama"] = msg if not ok else "ok"
    try:
        gl = GitLabClient(settings.gitlab_url, settings.gitlab_token, settings.gitlab_verify_ssl, timeout=5)
        user = gl.request("GET", "/user").json()
        checks["gitlab"] = f"ok (as {user.get('username')})"
    except Exception as exc:  # noqa: BLE001
        checks["gitlab"] = f"error: {exc}"
    return {"status": "ok" if all(v.startswith("ok") for v in checks.values()) else "degraded", "checks": checks}


@app.post("/webhook/gitlab")
def gitlab_webhook(
    payload: dict = Body(...),
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
) -> dict:
    _check_secret(x_gitlab_token)
    req = route_event(x_gitlab_event, payload, settings)
    if req is None:
        return {"status": "ignored"}
    job_id = enqueue_review(settings, req)
    if job_id is None:
        return {"status": "duplicate"}
    log.info("queued review job=%s project=%s mr=!%s trigger=%s", job_id, req.project_id, req.mr_iid, req.trigger)
    return {"status": "queued", "job_id": job_id}


class ManualReview(BaseModel):
    project_id: int
    mr_iid: int


@app.post("/api/review")
def manual_review(body: ManualReview, x_admin_token: str | None = Header(default=None)) -> dict:
    """Trigger a review without a webhook (header X-Admin-Token = webhook secret)."""
    _check_secret(x_admin_token)
    job_id = enqueue_review(settings, ReviewRequest(body.project_id, body.mr_iid, "", "manual"))
    return {"status": "queued", "job_id": job_id}


@app.get("/api/reviews")
def list_reviews(limit: int = 20) -> list[dict]:
    with session_factory() as db:
        rows = db.query(Review).order_by(Review.id.desc()).limit(min(limit, 100)).all()
        return [
            {
                "id": r.id,
                "project_id": r.project_id,
                "mr_iid": r.mr_iid,
                "head_sha": r.head_sha[:8],
                "status": r.status,
                "gate": r.gate_state,
                "counts": json.loads(r.counts_json or "{}"),
                "files_reviewed": r.files_reviewed,
                "duration_s": r.duration_s,
                "model": r.model,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "error": r.error or None,
            }
            for r in rows
        ]
