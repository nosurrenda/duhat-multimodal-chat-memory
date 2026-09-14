from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Credentials and connection endpoints, intentionally separate from hashed config."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: SecretStr
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str
    postgres_port: int
    minio_root_user: str
    minio_root_password: SecretStr
    minio_endpoint: str
    minio_bucket: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
