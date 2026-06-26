"""Centralised runtime configuration.

Single source of truth for environment variables. All app modules import
``settings`` from here — never read ``os.environ`` directly. This makes the
config surface auditable and gives Pydantic validation as a hard gate.

Actual deployment stack:
  - Database  : Supabase (managed PostgreSQL 17, ap-northeast-1)
  - Vector RAG: pgvector on Supabase — for ca_firm_rules semantic search
  - Data RAG  : PageOne Index API — vectorless RAG over structured Supabase data
  - Deployment: AWS (Fargate / EKS)
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


class LogFormat(StrEnum):
    """Wire format for application logs.

    ``console`` renders human-readable text via structlog.dev.ConsoleRenderer
    — what you want when you're iterating locally and reading the agent's
    thought process in plain text.

    ``json`` emits the dense single-line JSON that CloudWatch + Sentry
    structure-aware ingestion expect.

    ``auto`` picks json in production, console everywhere else. That's
    the default; LOG_FORMAT=console can force console even in production
    for one-off debugging windows.
    """

    JSON = "json"
    CONSOLE = "console"
    AUTO = "auto"


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
    log_format: LogFormat = Field(
        default=LogFormat.AUTO,
        description=(
            "console (dev-friendly text), json (CloudWatch/Sentry), or auto "
            "(json in production, console otherwise)."
        ),
    )
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
    # in Fargate via DATABASE_URL env var in AWS Secrets Manager.
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

    # -- Multi-channel credential encryption (W4) ----------------------------
    # Fernet key for at-rest encryption of per-firm WhatsApp tokens.
    # Generate once via Fernet.generate_key() and set in the prod task
    # def env. The default below is a VALID Fernet key reserved for
    # dev / test only — never commit a real key here.
    enma_crypto_key: SecretStr = Field(
        # Valid Fernet key (base64 of 32 bytes) reserved for dev/test
        # ONLY. Real keys live in the prod task-def env. Decoded value
        # is the ASCII string "dev-only-key-not-for-production-".
        default=SecretStr("ZGV2LW9ubHkta2V5LW5vdC1mb3ItcHJvZHVjdGlvbi0="),
        description="Fernet key for at-rest credential encryption.",
    )

    # -- Telegram -------------------------------------------------------------
    telegram_bot_token: SecretStr = Field(default=SecretStr("changeme-telegram-bot-token"))

    # -- Model endpoints (Phase 4+) ------------------------------------------
    # Provider: NVIDIA NIM (OpenAI-compatible surface).
    # All three roles share the same base URL; the model name selects the
    # specific NIM. Override any of these via environment variables.
    # Docs: https://docs.api.nvidia.com/nim/reference/
    layout_model_endpoint: str = Field(
        default="https://integrate.api.nvidia.com/v1/chat/completions"
    )
    layout_model_name: str = Field(default="meta/llama-3.1-8b-instruct")

    extraction_model_endpoint: str = Field(
        default="https://integrate.api.nvidia.com/v1/chat/completions"
    )
    extraction_model_name: str = Field(default="nvidia/nemotron-ocr-v1")

    reasoning_model_endpoint: str = Field(
        default="https://integrate.api.nvidia.com/v1/chat/completions"
    )
    reasoning_model_name: str = Field(default="meta/llama-3.3-70b-instruct")

    # Voice transcription (NVIDIA NIM audio endpoint, Whisper-compatible).
    whisper_endpoint: str | None = Field(
        default="https://integrate.api.nvidia.com/v1/audio/transcriptions"
    )
    summarization_endpoint: str | None = None

    # Embeddings (Phase 5). REQUIRED in production / staging. In dev/test
    # the embedding service falls back to a deterministic hash embedder
    # so the suite is offline. ``embedding_dimensions`` MUST match the
    # ca_firm_rules.rule_embedding VECTOR(1024) column.
    # ⚠  Use nvidia/nv-embedqa-e5-v5 — produces exactly 1024 dims.
    #    Do NOT use nv-embedcode-7b-v1 (code embeddings, wrong domain).
    embedding_endpoint: str | None = Field(
        default="https://integrate.api.nvidia.com/v1/embeddings"
    )
    embedding_model_name: str = Field(default="nvidia/nv-embedqa-e5-v5")
    embedding_dimensions: int = Field(default=1024, ge=64, le=4096)
    # Force-override the embedder's "send dimensions?" auto-detection.
    # None  = auto (OpenAI text-embedding-3-* sends; everything else omits,
    #         with one-shot retry-without-dimensions if the model 400s).
    # True  = always send (use when running a Matryoshka model the heuristic
    #         doesn't recognise).
    # False = never send (force-omit for an unknown NIM model).
    embedding_send_dimensions: bool | None = Field(
        default=None,
        description="Override embedding-client dimensions-parameter behaviour.",
    )

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

    # -- AWS deployment -------------------------------------------------------
    # AWS account ID — used for CloudWatch, Secrets Manager, and
    # service-to-service auth.
    aws_account_id: str | None = Field(
        default=None,
        description="AWS account ID for CloudWatch and Secrets Manager.",
    )
    # IAM Role for EKS or Fargate task execution.
    # Leave None for local dev — AWS profile is used.
    aws_iam_role: str | None = Field(
        default=None,
        description=(
            "AWS IAM role ARN. "
            "Used for EKS or Fargate tasks. "
            "Leave unset for local dev (AWS profile takes over)."
        ),
    )
    # Fargate specific: the service URL of this backend (used for
    # service-to-service calls and health check registration).
    fargate_service_url: str | None = Field(
        default=None,
        description="Full Fargate service URL.",
    )

    # -- Web onboarding redirect ---------------------------------------------
    # Shown to Telegram users who type /start without a deep-link payload
    # and don't already have a firm. The web flow collects consents and
    # password — we don't reproduce that over Telegram.
    web_onboarding_url: str = Field(
        default="https://enmalabs.in/onboarding",
        description="Public URL of the marketing-site onboarding form.",
    )
    telegram_bot_username: str = Field(
        default="enmalabsbot",
        description=(
            "Bot username (without @) for per-client deep links "
            "(t.me/<username>?start=client_<uuid>). ADR-017."
        ),
    )

    # -- Observability --------------------------------------------------------
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    # -- Cache (Phase 8 — Redis) ---------------------------------------------
    # OPTIONAL: when unset, the cache layer becomes a no-op and the rate
    # limiter falls back to in-memory storage. In production this MUST
    # point at the ElastiCache Serverless endpoint (use rediss:// for TLS).
    # Example: rediss://enma-cache-xxxxx.serverless.aps1.cache.amazonaws.com:6379/0
    redis_url: str | None = Field(
        default=None,
        description="Redis DSN. Use rediss:// for ElastiCache Serverless (TLS required).",
    )

    # -- Rate limiting (Phase 8) ---------------------------------------------
    # slowapi rate-string. ``count/window`` where window is one of
    # second|minute|hour|day. Applied as the global default; per-route
    # policies live in app/api/middleware/rate_limit.py.
    rate_limit_default: str = Field(
        default="100/minute",
        description="Default per-IP request budget across all routes.",
    )

    # -- Phase 7: compliance news + voice ------------------------------------
    serper_api_key: SecretStr | None = Field(
        default=None,
        description="Serper.dev API key for the daily compliance-news briefing.",
    )
    serper_endpoint: str = Field(
        default="https://google.serper.dev/news",
        description="Serper.dev search endpoint (defaults to the news vertical).",
    )

    # -- Track-A automation: provider adapters (env-gated, drop-in-key) -------
    # Each external provider stays DORMANT until its credentials appear in the
    # env. The factory returns a safe no-op client when a provider is
    # unconfigured, so the cron poll is a no-op and nothing fires. Paste the
    # subscription keys into the env and the adapter activates — no code change.
    #
    # Phase 2 — bank-statement email ingestion. When host + username + password
    # are all set, the email-ingest cron polls this IMAP mailbox for bank
    # statement attachments and lands them in the Brain.
    email_ingest_host: str | None = Field(
        default=None,
        description="IMAP host for the monitored bank-statement mailbox (e.g. imap.gmail.com).",
    )
    email_ingest_port: int = Field(
        default=993,
        description="IMAP-over-SSL port for the bank-statement mailbox.",
    )
    email_ingest_username: str | None = Field(
        default=None,
        description="IMAP username for the bank-statement mailbox.",
    )
    email_ingest_password: SecretStr | None = Field(
        default=None,
        description="IMAP password / app-password for the bank-statement mailbox.",
    )
    email_ingest_mailbox: str = Field(
        default="INBOX",
        description="IMAP folder to poll for bank-statement attachments.",
    )
    email_ingest_domain: str = Field(
        default="ingest.enmalabs.in",
        description=(
            "Domain for per-client ingest addresses (client-<uuid>@<domain>). "
            "A catch-all on this domain must deliver to the polled mailbox."
        ),
    )

    # Phase 3 — GSTR-2B auto-pull via a GST Suvidha Provider. Dormant until the
    # GSP base URL + API key are set (the per-client OTP auth token is stored
    # separately, per consented client).
    gsp_base_url: str | None = Field(
        default=None,
        description="GST Suvidha Provider API base URL (e.g. the GSP's GSTR-2B endpoint root).",
    )
    gsp_api_key: SecretStr | None = Field(
        default=None,
        description="GST Suvidha Provider API key / client secret.",
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

    def is_email_ingest_configured(self) -> bool:
        """True when the bank-statement IMAP mailbox has full credentials.

        The email-ingest adapter stays a no-op until this is True.
        """
        return bool(
            self.email_ingest_host
            and self.email_ingest_username
            and self.email_ingest_password
        )

    def is_gsp_configured(self) -> bool:
        """True when the GST Suvidha Provider base URL + API key are set."""
        return bool(self.gsp_base_url and self.gsp_api_key)

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
            "aws_account_id": self.aws_account_id,
            "fargate_service_url": self.fargate_service_url,
            "sentry_dsn_configured": bool(self.sentry_dsn),
            # Phase 8 — cache + rate limit (URL deliberately not echoed)
            "redis_configured": bool(self.redis_url),
            "redis_tls": bool(self.redis_url and self.redis_url.startswith("rediss://")),
            "rate_limit_default": self.rate_limit_default,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — first call wins for the process lifetime."""
    return Settings()


# Re-export the singleton for ergonomic imports.
settings: Settings = get_settings()


__all__ = [
    "Environment",
    "LogFormat",
    "LogLevel",
    "Settings",
    "get_settings",
    "settings",
]
