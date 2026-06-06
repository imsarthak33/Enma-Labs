"""Centralised runtime configuration.

Single source of truth for environment variables. All app modules import
``settings`` from here — never read ``os.environ`` directly. This makes the
config surface auditable and gives Pydantic validation as a hard gate.

Actual deployment stack:
  - Database  : Supabase (managed PostgreSQL 17, ap-northeast-1)
  - Vector RAG: pgvector on Supabase — for ca_firm_rules semantic search
  - Data RAG  : PageOne Index API — vectorless RAG over structured Supabase data
  - Deployment: GCP (Cloud Run / GKE)
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Settings(BaseSettings):
    """Runtime configuration. Loaded once at process start.

    All fields are validated by Pydantic. Boot fails fast if a required
    variable is missing — preferable to surfacing a None at request time.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Runtime --------------------------------------------------------------
    env: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Deployment environment.",
    )
    log_level: LogLevel = Field(default=LogLevel.INFO)
    app_host: str = Field(default="0.0.0.0")  # noqa: S104 — intentional bind
    app_port: int = Field(default=8000, ge=1, le=65535)

    # -- Database (Supabase) --------------------------------------------------
    # Use the Supabase connection string from Project Settings > Database.
    # Format: postgresql+asyncpg://postgres.[ref]:[password]@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres
    #
    # IMPORTANT — Supabase connection modes:
    #   Transaction mode (port 6543) : Use for stateless serverless/Cloud Run.
    #     pool_size must be 0 (Supavisor manages the pool externally).
    #     Does NOT support SET commands — RLS via server_settings only.
    #   Session mode    (port 5432)  : Use for long-lived processes (GKE pods).
    #     Supports SET app.current_firm_id for RLS.
    #     pool_size can be non-zero.
    #
    # Default below points to session mode — override with transaction mode
    # in Cloud Run via DATABASE_URL env var in GCP Secret Manager.
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://postgres:dev_password@localhost:5432/postgres"),
        description=(
            "Supabase async DSN (asyncpg driver). "
            "Session mode (port 5432) for GKE; "
            "transaction mode (port 6543) for Cloud Run."
        ),
    )
    # Pool settings — set db_pool_size=0 for Supabase transaction mode
    # (Supavisor handles pooling externally; SQLAlchemy should not pool).
    db_pool_size: int = Field(
        default=5,
        ge=0,  # 0 = NullPool (required for transaction mode / Cloud Run)
        le=100,
    )
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_s: int = Field(default=30, ge=1)
    db_echo: bool = Field(default=False, description="Echo SQL — debug only.")

    # -- Inter-service auth ---------------------------------------------------
    backend_api_key: SecretStr = Field(
        default=SecretStr("dev_backend_api_key_change_me"),
        description="Shared key the gateway presents on every dispatch.",
    )
    gateway_hmac_secret: SecretStr = Field(
        default=SecretStr("dev_hmac_secret_change_me"),
        description="Shared HMAC secret for envelope signature verification.",
    )

    # -- Telegram -------------------------------------------------------------
    telegram_bot_token: SecretStr = Field(default=SecretStr("changeme-telegram-bot-token"))

    # -- Model endpoints (Phase 4+) ------------------------------------------
    # Defaults point at OpenAI's public chat-completions endpoint so the
    # codebase is provider-agnostic but works out of the box for dev. Any
    # OpenAI-compatible server (NVIDIA NIM, vLLM, LiteLLM, Anthropic via
    # proxy, …) works by overriding the URL via env.
    layout_model_endpoint: str = Field(default="https://api.openai.com/v1/chat/completions")
    layout_model_name: str = Field(default="gpt-4o-mini")

    extraction_model_endpoint: str = Field(default="https://api.openai.com/v1/chat/completions")
    extraction_model_name: str = Field(default="gpt-4o")

    reasoning_model_endpoint: str = Field(default="https://api.openai.com/v1/chat/completions")
    reasoning_model_name: str = Field(default="gpt-4o")

    # Optional in Phase 4 — wired in later phases.
    whisper_endpoint: str | None = None
    summarization_endpoint: str | None = None

    # Embeddings (Phase 5). REQUIRED in production / staging. In dev/test
    # the embedding service falls back to a deterministic hash embedder
    # so the suite is offline. ``embedding_dimensions`` MUST match the
    # ca_firm_rules.rule_embedding VECTOR(1024) column.
    embedding_endpoint: str | None = None
    embedding_model_name: str = Field(default="text-embedding-3-large")
    embedding_dimensions: int = Field(default=1024, ge=64, le=4096)

    # LLM API key. Required at first LLM call (not at boot, so tests
    # that mock the client don't need it set).
    llm_api_key: SecretStr | None = Field(
        default=None,
        description="Bearer token for the OpenAI-compatible LLM endpoint.",
    )

    # Per-call ceiling — defence against runaway responses. Individual
    # agent calls can pass a lower value; this is the hard upper bound.
    llm_max_output_tokens: int = Field(default=4096, ge=64, le=64_000)
    llm_request_timeout_s: int = Field(default=90, ge=5, le=600)

    # -- PageOne Index API (vectorless RAG over structured Supabase data) -----
    # Used by the Supervisor Agent to answer natural language questions about
    # extracted documents, filing history, and ITC data stored in Supabase.
    # Distinct from pgvector RAG which handles ca_firm_rules retrieval.
    pageone_api_key: SecretStr | None = Field(
        default=None,
        description="PageOne Index API key for vectorless RAG over Supabase data.",
    )
    pageone_index_endpoint: str | None = Field(
        default=None,
        description="PageOne Index API base URL.",
    )

    # -- GCP deployment -------------------------------------------------------
    # GCP project ID — used for Cloud Logging, Secret Manager, and
    # service-to-service auth (Cloud Run invoker tokens).
    gcp_project_id: str | None = Field(
        default=None,
        description="GCP project ID for Cloud Logging and Secret Manager.",
    )
    # Service account for Workload Identity (GKE) or Cloud Run SA.
    # Leave None for local dev — ADC (Application Default Credentials) is used.
    gcp_service_account: str | None = Field(
        default=None,
        description=(
            "GCP service account email. "
            "Used for Workload Identity on GKE. "
            "Leave unset for local dev (ADC takes over)."
        ),
    )
    # Cloud Run specific: the service URL of this backend (used for
    # service-to-service calls and health check registration).
    cloud_run_service_url: str | None = Field(
        default=None,
        description="Full Cloud Run service URL (e.g. https://enma-backend-xxx-an.a.run.app).",
    )

    # -- Observability --------------------------------------------------------
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    # -- Phase 7: compliance news + voice ------------------------------------
    serper_api_key: SecretStr | None = Field(
        default=None,
        description="Serper.dev API key for the daily compliance-news briefing.",
    )
    serper_endpoint: str = Field(
        default="https://google.serper.dev/news",
        description="Serper.dev search endpoint (defaults to the news vertical).",
    )

    # -- CORS -----------------------------------------------------------------
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Explicit allowlist; '*' is rejected outside development.",
    )

    # -- Validators -----------------------------------------------------------
    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: PostgresDsn) -> PostgresDsn:
        # SQLAlchemy's async engine needs the +asyncpg dialect.
        if "+asyncpg" not in str(v):
            raise ValueError("database_url must use the postgresql+asyncpg driver " f"(got: {v})")
        return v

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_in_prod(cls, v: list[str]) -> list[str]:
        return v

    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION

    def is_development(self) -> bool:
        return self.env == Environment.DEVELOPMENT

    def uses_transaction_pooling(self) -> bool:
        """True when connected to Supabase transaction mode (port 6543).

        In transaction mode, SQLAlchemy pool must be disabled (pool_size=0)
        because Supavisor manages the connection pool externally.
        SET commands (used for RLS firm_id) are not supported — the backend
        must pass app.current_firm_id via the connection string's options
        parameter instead.
        """
        return ":6543/" in str(self.database_url)

    def model_post_init(self, __context: object) -> None:
        # Hard guard: no wildcard CORS in non-dev environments.
        if self.env != Environment.DEVELOPMENT and "*" in self.cors_origins:
            raise ValueError("cors_origins='*' is not allowed outside development.")
        # Phase 5: embedding endpoint is required in staging/production.
        # Dev/test can fall back to the deterministic hash embedder.
        if (
            self.env in (Environment.PRODUCTION, Environment.STAGING)
            and not self.embedding_endpoint
        ):
            raise ValueError("embedding_endpoint is required in staging/production.")
        # Embedding dimension must match the pgvector column (1024).
        if self.embedding_dimensions != 1024:
            raise ValueError("embedding_dimensions must equal 1024 to match VECTOR(1024).")

    # -- Logging-safe representation ------------------------------------------
    def safe_repr(self) -> dict[str, str | int | bool | None]:
        """Loggable view — secrets redacted."""

        def _mask(s: SecretStr | None) -> str:
            if s is None:
                return ""
            return "***" if s.get_secret_value() else ""

        return {
            "env": self.env.value,
            "log_level": self.log_level.value,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "db_pool_size": self.db_pool_size,
            "db_echo": self.db_echo,
            "db_transaction_mode": self.uses_transaction_pooling(),
            "backend_api_key": _mask(self.backend_api_key),
            "gateway_hmac_secret": _mask(self.gateway_hmac_secret),
            "telegram_bot_token": _mask(self.telegram_bot_token),
            "llm_api_key": _mask(self.llm_api_key),
            "layout_model_name": self.layout_model_name,
            "extraction_model_name": self.extraction_model_name,
            "reasoning_model_name": self.reasoning_model_name,
            "pageone_configured": bool(self.pageone_api_key),
            "gcp_project_id": self.gcp_project_id,
            "cloud_run_service_url": self.cloud_run_service_url,
            "sentry_dsn_configured": bool(self.sentry_dsn),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — first call wins for the process lifetime."""
    return Settings()


# Re-export the singleton for ergonomic imports.
settings: Settings = get_settings()


__all__ = ["Environment", "LogLevel", "Settings", "get_settings", "settings"]
