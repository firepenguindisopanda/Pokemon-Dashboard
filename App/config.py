"""Application configuration using Pydantic Settings.

Loads configuration from environment variables / .env file with typed validation.
Provides a single source of truth for all app configuration.
"""
import datetime
import logging
import sys
from typing import Optional
from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)

# ── Secret strength policy ──

MIN_SECRET_LENGTH = 32

# Substrings that mark a value as a placeholder someone forgot to replace.
# Matched case-insensitively against the whole secret.
PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change-in-production",
    "change_in_production",
    "super-secret",
    "your-secret",
    "placeholder",
    "example",
)

_GENERATE_HINT = (
    'Generate one with:\n'
    '    python -c "import secrets; print(secrets.token_urlsafe(48))"'
)


def describe_secret_weakness(value: str) -> Optional[str]:
    """Return why a secret is unfit for production, or None if it is fine.

    Args:
        value: The candidate secret.

    Returns:
        A human-readable reason, or None when the secret is acceptable.
    """
    if not value or not value.strip():
        return "is empty"

    lowered = value.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"looks like an unreplaced placeholder (contains {marker!r})"

    if len(value) < MIN_SECRET_LENGTH:
        return (
            f"is too short ({len(value)} characters; "
            f"at least {MIN_SECRET_LENGTH} required)"
        )

    return None


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

    @model_validator(mode="after")
    def _refuse_insecure_production_secrets(self) -> "Settings":
        """Block startup when running with debug off and unsafe signing keys.

        JWT_SECRET_KEY signs the access and refresh tokens: anyone who can
        guess it can mint a valid token for any user ID. FLASK_SECRET_KEY signs
        the session cookie. Neither may be a placeholder in production, and
        failing closed here is far better than serving traffic unprotected.

        Debug mode is exempt so local development stays frictionless.
        """
        if self.debug:
            if describe_secret_weakness(self.jwt_secret_key):
                logger.warning(
                    "Running in DEBUG mode with a weak JWT_SECRET_KEY. "
                    "This is tolerated locally but will refuse to start once "
                    "DEBUG is false."
                )
            return self

        problems = []
        for field_name, value in (
            ("FLASK_SECRET_KEY", self.flask_secret_key),
            ("JWT_SECRET_KEY", self.jwt_secret_key),
        ):
            reason = describe_secret_weakness(value)
            if reason:
                problems.append(f"  - {field_name} {reason}")

        if problems:
            raise ValueError(
                "Refusing to start: DEBUG is false but these secrets are not "
                "production-safe:\n"
                + "\n".join(problems)
                + "\n\n"
                + _GENERATE_HINT
            )

        return self

    @property
    def jwt_access_expires_timedelta(self) -> datetime.timedelta:
        return datetime.timedelta(hours=self.jwt_access_token_expires_hours)

    @property
    def jwt_refresh_expires_timedelta(self) -> datetime.timedelta:
        return datetime.timedelta(days=self.jwt_refresh_token_expires_days)


@lru_cache()
def get_settings() -> Settings:
    """Return cached Settings singleton (re-reads .env on first call per process).

    Raises:
        SystemExit: If configuration is unsafe for production. Exiting with a
            plain message keeps the reason readable in a deploy log, instead of
            burying it in a pydantic traceback.
    """
    try:
        return Settings()
    except ValidationError as exc:
        for error in exc.errors():
            message = str(error.get("msg", error)).removeprefix("Value error, ")
            print(f"\nCONFIGURATION ERROR\n\n{message}\n", file=sys.stderr)
        raise SystemExit(1) from exc
