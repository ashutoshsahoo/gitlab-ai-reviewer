"""Redis/RQ job submission with simple de-duplication."""

from __future__ import annotations

from redis import Redis
from rq import Queue

from .config import Settings
from .webhook import ReviewRequest

QUEUE_NAME = "reviews"


def get_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url)


def enqueue_review(settings: Settings, req: ReviewRequest) -> str | None:
    """Queue a review. Returns the job id, or None if an identical job is already queued/done."""
    conn = get_redis(settings)
    if req.trigger != "manual":
        key = f"ai-review:lock:{req.project_id}:{req.mr_iid}:{req.head_sha}"
        if not conn.set(key, "1", nx=True, ex=3600):
            return None
    job = Queue(QUEUE_NAME, connection=conn).enqueue(
        "app.tasks.process_review",
        req.project_id,
        req.mr_iid,
        req.head_sha,
        req.trigger,
        job_timeout=3600,
        result_ttl=3600,
        failure_ttl=86400,
    )
    return job.id
