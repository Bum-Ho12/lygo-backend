from pathlib import Path
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_INSECURE_SECRETS = {"", "change-me"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Absolute path: .env must load no matter which directory the server is started from (e.g. `cd app && fastapi dev`).
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_env: str = "local"  # local | staging | production
    log_level: str = "INFO"
    frontend_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]  # comma-separated in env

    database_url: str = "postgresql+asyncpg://lygo_app:lygo_app@localhost:5432/lygo"
    database_migrator_url: str = "postgresql+psycopg://lygo_owner:lygo_owner@localhost:5432/lygo"
    redis_url: str = "redis://localhost:6379/0"

    master_key: str = ""  # base64 32-byte key that wraps per-firm data keys; REQUIRED outside local (KMS/Vault in production)
    mfa_required_for_firm_roles: bool = False  # docs/07: TOTP MFA required for firm roles; MUST be true in production (validated)
    trusted_proxy_hops: int = 0  # how many reverse proxies append to X-Forwarded-For; 0 = use the socket address
    auth_lock_seconds: int = 900
    auth_window_seconds: int = 900
    auth_max_failures_pair: int = 5  # failures per (account, IP) before a lock
    auth_max_failures_account: int = 20  # across all IPs (slows distributed guessing without letting one IP lock a victim out)
    auth_max_failures_ip: int = 60
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_sends_per_window: int = 3  # per user per 10 minutes (SMS costs money and is a fraud target)
    otp_sends_per_ip_hour: int = 20
    password_reset_ttl_minutes: int = 30
    jwt_secret: str = "change-me"
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 14

    # --- storage (default Lygo-managed destination; per-firm BYO arrives with storage policies) ---
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "data/objects"
    storage_endpoint: str = ""
    storage_region: str = "local"
    storage_bucket: str = "lygo-local"
    storage_access_key: str = ""
    storage_secret_key: str = ""

    # --- ingestion ---
    sse_poll_seconds: float = 0.5
    sse_max_seconds: int = 120
    max_upload_mb: int = 50
    ingest_mode: str = "inline"  # inline (in-process, local/dev) | celery
    clamd_host: str = ""  # required outside local: ingestion fails closed without a scanner
    clamd_port: int = 3310
    ocr_engine: str = "tesseract"
    ocr_langs: str = "eng+swa"
    ocr_review_threshold: float = 0.80

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"  # set ANTHROPIC_MODEL=claude-sonnet-5-5 to halve the price once quality is evaluated
    anthropic_effort: str = "medium"  # low | medium | high | xhigh | max ("" = API default). Opus 5.5 always thinks; effort is the only dial
    anthropic_fallbacks: bool = True  # server-side refusal fallback (beta); ignored for models that do not support it
    llm_model_chat: str = ""
    llm_timeout_s: float = 60.0
    ai_monthly_message_cap: int = 2000  # per firm; assistant messages
    rag_min_similarity: float = 0.25  # abstain unless a lexical match exists or the best vector similarity reaches this
    llm_base_url: str = ""
    llm_api_key: str = ""
    voyage_api_key: str = ""
    voyage_model: str = "voyage-law-2"  # legal retrieval; 1024 dims, matching the chunks.embedding column
    embedding_model: str = ""
    embedding_dim: int = 1024  # fixed in the chunks migration (vector(1024)); changing it requires a re-index migration
    embed_batch_size: int = 64

    frontend_base_url: str = "http://localhost:3000"  # where emailed links point (accept-invite, reset-password)
    smtp_host: str = ""  # empty => no outbound email (console logger in local, fail closed elsewhere)
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls | tls (implicit) | none (local test servers only)
    email_from: str = "no-reply@lygo.local"
    email_from_name: str = "Lygo"
    api_base_url: str = ""  # public URL of this API; used to build SMS delivery-report callbacks
    sms_max_segments: int = 3
    sms_monthly_quota: int = 500  # per firm; Thrivewave has no balance API so metering is ours
    thrivewave_base_url: str = ""
    thrivewave_api_key: str = ""
    thrivewave_sender_id: str = ""

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def split_origins(cls, v: str | list[str]) -> list[str]:
        return [o.strip() for o in v.split(",")] if isinstance(v, str) else v

    @field_validator("storage_local_dir")
    @classmethod
    def anchor_storage_dir(cls, v: str) -> str:
        p = Path(v)
        return str(p if p.is_absolute() else PROJECT_ROOT / p)  # same reason: never depend on the working directory

    @model_validator(mode="after")
    def require_mfa_in_production(self) -> "Settings":
        if self.is_production and not self.mfa_required_for_firm_roles:
            raise ValueError("MFA_REQUIRED_FOR_FIRM_ROLES must be true in production")
        return self

    @model_validator(mode="after")
    def reject_insecure_defaults(self) -> "Settings":
        if not self.is_local and (self.jwt_secret in _INSECURE_SECRETS or len(self.jwt_secret) < 32):
            raise ValueError("JWT_SECRET must be at least 32 characters outside local env")
        return self


settings = Settings()
