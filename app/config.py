"""Environment-driven configuration.

Every value comes from the environment (loaded from a gitignored ``.env``).
Nothing secret is ever hardcoded here — the defaults are either safe local
development values or empty.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "SmartReco"
    secret_key: str = "dev-only-insecure-secret-change-me"
    debug: bool = False
    session_ttl_hours: int = 24 * 14

    # --- Mesh API (the only LLM gateway this project uses) -----------------
    mesh_api_key: str = ""
    mesh_base_url: str = "https://api.meshapi.ai/v1"
    mesh_chat_model: str = "openai/gpt-4o"
    mesh_embed_model: str = "openai/text-embedding-3-small"
    mesh_embed_dim: int = 1536
    mesh_timeout_seconds: float = 60.0
    mesh_max_retries: int = 2

    # --- Datastores --------------------------------------------------------
    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "smartreco_products"

    # --- Event ingest ------------------------------------------------------
    event_buffer_key: str = "smartreco:events:buffer"
    event_flush_batch_size: int = 500
    event_max_batch_payload: int = 50
    #: The API drains the buffer itself so the app works without Celery running.
    #: Set false when Celery Beat is definitely running and you want it to own
    #: the job exclusively.
    event_drain_in_process: bool = True
    event_drain_interval_seconds: int = 5
    event_drain_lock_seconds: int = 30
    #: When the broker is unreachable, generate the recommendation in-process
    #: after the response has been sent rather than dropping it on the floor.
    inline_generation_fallback: bool = True
    #: How long to stop calling Mesh after it refuses for a reason retrying
    #: cannot fix — an empty balance or a bad key.
    mesh_backoff_seconds: int = 600

    # --- Recommendation triggers ------------------------------------------
    reco_score_threshold: int = 12
    reco_cooldown_seconds: int = 600
    reco_min_events: int = 4
    reco_behavior_window_hours: int = 72
    reco_max_items: int = 4
    reco_lock_ttl_seconds: int = 180
    reco_candidate_pool: int = 12
    reco_relevance_floor: float = 0.55
    reco_max_retrieval_attempts: int = 3

    # --- Observability -----------------------------------------------------
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "smartreco"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # --- Scheduled digest --------------------------------------------------
    digest_enabled: bool = True
    digest_hour: int = 16
    digest_minute: int = 0
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "SmartReco <no-reply@smartreco.local>"
    smtp_starttls: bool = True
    public_base_url: str = "http://localhost:8000"

    @property
    def sqlalchemy_url(self) -> str:
        """Postgres when configured, otherwise a local SQLite file.

        The SQLite fallback exists so the project can be cloned and run with a
        single command; docker-compose supplies Postgres for the real run.
        """
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(DATA_DIR / 'smartreco.db').as_posix()}"

    @property
    def mesh_configured(self) -> bool:
        return bool(self.mesh_api_key)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host)

    @property
    def digest_dir(self) -> Path:
        return DATA_DIR / "digests"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Done here rather than at each entry point (API, Celery worker, seed script) so
# that no process can miss it. See app/tls.py for why it is needed.
from app.tls import enable_system_trust_store  # noqa: E402

enable_system_trust_store()
