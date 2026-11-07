from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_SECRETS = {"", "change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"  # local | staging | production
    log_level: str = "INFO"
    frontend_origins: list[str] = ["http://localhost:3000"]

    database_url: str = "postgresql+asyncpg://lygo_app:lygo_app@localhost:5432/lygo"
    database_migrator_url: str = "postgresql+psycopg://lygo_owner:lygo_owner@localhost:5432/lygo"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "change-me"
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 14

    llm_base_url: str = ""
    llm_api_key: str = ""
    embedding_dim: int = 1024

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
