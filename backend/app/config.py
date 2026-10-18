"""Application settings, read from environment variables (or a .env file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/newtonite"
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_ttl_minutes: int = 8 * 60
    cors_origins: list[str] = ["http://localhost:5173"]

    # Background worker
    worker_poll_seconds: float = 1.0
    worker_batch_size: int = 20


settings = Settings()
