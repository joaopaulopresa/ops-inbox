"""Typed application settings, read once from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every tunable value of the application. No literals elsewhere in the code."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL
    database_url: str = "postgresql+psycopg://opsinbox:opsinbox@localhost:54410/opsinbox"

    # Object storage (S3 compatible)
    s3_endpoint: str = "localhost:9410"
    s3_access_key: str = "opsinbox"
    s3_secret_key: str = "opsinbox-secret"
    s3_bucket: str = "opsinbox"
    s3_secure: bool = False

    # Incoming mail (IMAP)
    imap_host: str = "localhost"
    imap_port: int = 3443
    imap_user: str = "bookings"
    imap_password: str = "bookings-secret"
    imap_mailbox: str = "INBOX"
    imap_ssl: bool = False
    ingest_poll_seconds: float = 5.0

    # Outgoing mail (SMTP)
    smtp_host: str = "localhost"
    smtp_port: int = 3425
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    mail_from: str = "bookings@larkspur.example"
    mail_from_name: str = "Larkspur Classic Car Hire"

    # Model provider
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "deepseek/deepseek-v4.1-flash-1017"
    seed_model: str = "meta-llama/llama-3.1-8b-instruct"
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 2

    # Worker
    worker_poll_seconds: float = 1.0
    job_max_attempts: int = 5
    job_lock_timeout_seconds: int = 300

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
