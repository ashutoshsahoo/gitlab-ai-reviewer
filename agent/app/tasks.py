"""RQ task entry point executed by the worker process."""

from __future__ import annotations

import logging

from .config import get_settings
from .db import init_db, make_session_factory
from .gitlab_client import GitLabClient
from .ollama_client import OllamaClient
from .reviewer import Orchestrator
from .static_analysis import StaticAnalyzer

log = logging.getLogger(__name__)


def process_review(project_id: int, mr_iid: int, head_sha: str = "", trigger: str = "webhook") -> dict:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("starting review project=%s mr=!%s trigger=%s", project_id, mr_iid, trigger)

    session_factory = make_session_factory(settings)
    init_db(session_factory)
    orchestrator = Orchestrator(
        settings,
        GitLabClient(settings.gitlab_url, settings.gitlab_token, settings.gitlab_verify_ssl),
        OllamaClient(settings),
        session_factory,
        StaticAnalyzer(settings) if settings.static_analysis_enabled else None,
    )
    result = orchestrator.run(project_id, mr_iid, trigger)
    log.info("review finished: %s", result)
    return result
