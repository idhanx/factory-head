"""Runtime configuration. Everything env-driven; nothing secret in the repo."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- app -----------------------------------------------------------------
    app_name: str = "FactoryManager AI"
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # --- data ----------------------------------------------------------------
    database_url: PostgresDsn = Field(
        default="postgresql+psycopg://factorymanager:factorymanager@localhost:5432/factorymanager"
    )
    # Migrations and seed scripts need the owning role (DDL, and writes that
    # span plants). The API and worker connect as a restricted role that row-
    # level security applies to, so the two URLs are deliberately separate.
    migration_database_url: PostgresDsn | None = None
    # Password the migration gives the application role (see RUNNING.md).
    app_db_password: str | None = None
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")

    # --- auth ----------------------------------------------------------------
    # Overridden in every non-local environment; startup refuses to boot with
    # the default outside `local`.
    secret_key: str = "dev-only-insecure-key-change-me-before-any-real-deploy"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 12  # a supervisor's whole shift
    refresh_token_days: int = 30

    # --- CORS ----------------------------------------------------------------
    # NoDecode: take the raw env string and split it on commas ourselves, rather
    # than requiring operators to write a JSON array in a .env file.
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # --- MQTT / EMQX ---------------------------------------------------------
    # §2/§9: the data-source seam. A sensor feed must enter through the same
    # internal contract a supervisor's thumb uses today, so the hardware phase
    # is a data-source swap, not a rebuild.
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "factorymanager"
    # Shared secret the EMQX rule sends as X-Ingest-Token. Ingest refuses every
    # reading while it is empty, so enabling MQTT without it opens nothing.
    ingest_token: str = ""

    # --- LLM (vLLM, OpenAI-compatible endpoint) ------------------------------
    llm_enabled: bool = False
    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "not-needed-for-local-vllm"
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 700

    # --- Ollama: local AI for the Kaizen plugin ------------------------------
    # Ollama serves an OpenAI-compatible API at /v1, so one client speaks to it
    # and to vLLM alike. Kaizen defaults to Ollama for a reason beyond cost: a
    # photo of somebody's shop floor, and an operator's voice note describing
    # what is wrong with it, never leave the plant's own machine. Any of these
    # may point at the shared vLLM tier instead by setting the URL.
    #
    # Pull once on the plant server:
    #   ollama pull qwen2.5:7b-instruct    # the interview, challenge, rewrite
    #   ollama pull qwen2.5vl:7b           # Step 2 — reading the photo
    #   ollama pull nomic-embed-text       # pattern-detection fingerprints
    #
    # Every path that uses these degrades to a deterministic fallback when
    # Ollama is unreachable. Nothing in the operator journey blocks on a model.
    ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_vision_model: str = "qwen2.5vl:7b"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_timeout_seconds: float = 60.0
    # Vision is the most expensive call in the plugin and ~80% of volume is
    # Quick Observations, so §13.4's cost question gets an explicit lever: a
    # ceiling on how many photos a plant analyses per day, after which photos
    # are still stored as evidence but not read.
    vision_daily_limit: int = 400

    # --- WhatsApp (WATI) -----------------------------------------------------
    # §16.4: apply on day 1, approval takes 3-5 weeks. Until then the escalation
    # ladder runs with the console/SMS transport and loses nothing but reach.
    wati_enabled: bool = False
    wati_base_url: str = ""
    wati_api_token: str = ""
    # Outside WhatsApp's 24-hour session window only approved templates reach a
    # number. When true, messages go as template "<prefix><kind>" with the text
    # in the "message" parameter; when false, as session messages.
    wati_use_templates: bool = False
    wati_template_prefix: str = "fm_"

    # --- SMS fallback (MSG91) — §12 risk 6 ---------------------------------
    msg91_enabled: bool = False
    msg91_auth_key: str = ""
    # A DLT-approved Flow template with a ##message## variable.
    msg91_template_id: str = ""

    # --- Browser push (VAPID) — the supervisor's 5-minute rung ---------------
    # Generate a key with `python scripts/generate_vapid_keys.py`.
    webpush_enabled: bool = False
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:ops@factorymanager.ai"

    # --- Delivery ------------------------------------------------------------
    delivery_max_attempts: int = 5
    # Where links in messages point (the web app).
    app_base_url: str = "http://localhost:3000"
    # Photos of fixes (§5.1) are stored here, one directory per plant.
    storage_dir: str = "storage"

    # --- Plant defaults (§16.5 "default if unresolved") ----------------------
    default_action_cost_threshold_paise: int = 250_000  # ₹2,500
    default_action_duration_minutes: int = 30
    default_recurrence_count: int = 3
    default_recurrence_window_shifts: int = 7
    default_reconciliation_tolerance_parts: int = 5
    default_reconciliation_tolerance_fraction: float = 0.02

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        """Alembic runs synchronously — same psycopg3 driver, sync mode."""
        return str(self.database_url).replace("+psycopg_async", "+psycopg")

    @property
    def admin_database_url(self) -> str:
        """The owning role, for migrations and scripts; falls back to the app URL."""
        url = self.migration_database_url or self.database_url
        return str(url).replace("+psycopg_async", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment != "local" and settings.secret_key.startswith("dev-only"):
        raise RuntimeError(
            "SECRET_KEY must be set to a real value outside the local environment"
        )
    return settings


settings = get_settings()
