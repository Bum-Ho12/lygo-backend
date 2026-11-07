from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    frontend_origins: list[str] = ["http://localhost:3000"]

    database_url: str = "postgresql+asyncpg://lygo_app:lygo_app@localhost:5432/lygo"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "change-me"
    jwt_access_ttl_min: int = 15

    llm_base_url: str = ""
    llm_api_key: str = ""
    embedding_dim: int = 1024

    thrivewave_base_url: str = ""
    thrivewave_api_key: str = ""
    thrivewave_sender_id: str = ""

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def split_origins(cls, v: str | list[str]) -> list[str]:
        return [o.strip() for o in v.split(",")] if isinstance(v, str) else v


settings = Settings()
