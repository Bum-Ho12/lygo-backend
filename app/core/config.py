from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_INSECURE_SECRETS = {"", "change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"  # local | staging | production
    log_level: str = "INFO"
    frontend_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]  # comma-separated in env

    database_url: str = "postgresql+asyncpg://lygo_app:lygo_app@localhost:5432/lygo"
    database_migrator_url: str = "postgresql+psycopg://lygo_owner:lygo_owner@localhost:5432/lygo"
    redis_url: str = "redis://localhost:6379/0"

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
    max_upload_mb: int = 50
    ingest_mode: str = "inline"  # inline (in-process, local/dev) | celery
    clamd_host: str = ""  # required outside local: ingestion fails closed without a scanner
    clamd_port: int = 3310
    ocr_engine: str = "tesseract"
    ocr_langs: str = "eng+swa"
    ocr_review_threshold: float = 0.80

    llm_base_url: str = ""
    llm_api_key: str = ""
    embedding_model: str = ""
    embedding_dim: int = 1024  # fixed in the chunks migration (vector(1024)); changing it requires a re-index migration
    embed_batch_size: int = 64

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

    @model_validator(mode="after")
    def reject_insecure_defaults(self) -> "Settings":
        if not self.is_local and (self.jwt_secret in _INSECURE_SECRETS or len(self.jwt_secret) < 32):
            raise ValueError("JWT_SECRET must be at least 32 characters outside local env")
        return self


settings = Settings()
