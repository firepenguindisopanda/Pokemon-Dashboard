"""Application configuration using Pydantic Settings.

Loads configuration from environment variables / .env file with typed validation.
Provides a single source of truth for all app configuration.
"""
import datetime
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Flask Core ──
    flask_secret_key: str = "change-me-in-production"
    sqlalchemy_database_uri: str = "sqlite:///data.db"
    sqlalchemy_track_modifications: bool = False
    debug: bool = True

    # ── JWT Authentication ──
    jwt_secret_key: str = "change-me-in-production"
    jwt_access_token_expires_hours: float = 0.25  # 15 minutes
    jwt_refresh_token_expires_days: int = 7        # 7 days
    jwt_token_leeway_seconds: int = 30             # clock skew tolerance
    jwt_cookie_secure: bool = False  # False in dev, True on production HTTPS
    jwt_cookie_csrf_protect: bool = False
    jwt_token_location: list[str] = ["cookies"]
    jwt_header_name: str = "Cookie"
    jwt_access_cookie_name: str = "access_token"
    jwt_refresh_cookie_name: str = "refresh_token"

    # ── CORS ──
    cors_origins: str = "*"

    # ── Database Pool (used when switching to PostgreSQL) ──
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_pre_ping: bool = True

    # ── Analytics / ML ──
    model_cache_dir: str = "App/models_cache"
    analytics_auto_initialize: bool = True
    random_seed: int = 42

    @property
    def jwt_access_expires_timedelta(self) -> datetime.timedelta:
        return datetime.timedelta(hours=self.jwt_access_token_expires_hours)

    @property
    def jwt_refresh_expires_timedelta(self) -> datetime.timedelta:
        return datetime.timedelta(days=self.jwt_refresh_token_expires_days)


@lru_cache()
def get_settings() -> Settings:
    """Return cached Settings singleton (re-reads .env on first call per process)."""
    return Settings()
