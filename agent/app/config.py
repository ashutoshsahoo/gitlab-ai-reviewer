"""Runtime configuration (all values come from environment variables)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # --- GitLab -----------------------------------------------------------
    gitlab_url: str = "http://gitlab:8929"  # URL reachable from inside the compose network
    gitlab_token: str = ""  # bot user personal access token (scope: api)
    gitlab_admin_token: str = ""  # admin PAT, only used by the CLI helpers
    gitlab_webhook_secret: str = "change-me-webhook-secret"
    gitlab_bot_username: str = "ai-reviewer"
    gitlab_verify_ssl: bool = True

    # --- Ollama -----------------------------------------------------------
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen2.5-coder:14b"
    ollama_num_ctx: int = 12288
    ollama_num_predict: int = 2048
    ollama_temperature: float = 0.1
    ollama_timeout_s: int = 900

    # --- Infra ------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    database_url: str = "postgresql+psycopg://reviewer:reviewer@postgres:5432/reviewer"
    log_level: str = "INFO"

    # --- Review behaviour ---------------------------------------------------
    max_files_per_review: int = 30
    max_diff_lines_per_file: int = 1200
    max_prompt_chars: int = 18000
    max_inline_comments: int = 25
    min_confidence: float = 0.6
    inline_min_severity: str = "low"  # critical|high|medium|low
    review_drafts: bool = False
    use_llm_summary: bool = True

    # --- Static analysis ------------------------------------------------------
    static_analysis_enabled: bool = True
    semgrep_extra_config: str = ""  # e.g. "p/default" (needs internet access)
    static_timeout_s: int = 180

    # --- Quality gate -------------------------------------------------------
    gate_mode: str = "enforce"  # enforce | advisory
    gate_max_critical: int = 0
    gate_max_high: int = 0
    gate_max_medium: int = 5
    gate_on_error: str = "pass"  # pass | fail  (what to report if the agent itself breaks)


@lru_cache
def get_settings() -> Settings:
    return Settings()
