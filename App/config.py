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
    # DATABASE_URL is what hosting providers inject; it wins when present.
    database_url: Optional[str] = None
    sqlalchemy_database_uri: str = "sqlite:///data.db"
    sqlalchemy_track_modifications: bool = False
    debug: bool = True

    # ── JWT Authentication ──
    jwt_secret_key: str = "change-me-in-production"
    jwt_access_token_expires_hours: float = 0.25  # 15 minutes
    jwt_refresh_token_expires_days: int = 7        # 7 days
    jwt_token_leeway_seconds: int = 30             # clock skew tolerance
    jwt_cookie_secure: bool = False  # False in dev, True on production HTTPS
    # Auth lives in cookies, so the browser attaches it to cross-site requests
    # too. Double-submit CSRF tokens are what stop another origin forging
    # state changes. Defaults on: opting out must be deliberate.
    jwt_cookie_csrf_protect: bool = True
    # Server-rendered forms cannot set headers, so also accept the token as a
    # hidden `csrf_token` field.
    jwt_csrf_check_form: bool = True
    jwt_token_location: list[str] = ["cookies"]
    jwt_header_name: str = "Cookie"
    jwt_access_cookie_name: str = "access_token"
    jwt_refresh_cookie_name: str = "refresh_token"

    # ── Sessions / Redis ──
    # Native Redis URL (rediss:// for TLS). Upstash's REST URL and token are a
    # different, HTTP-based API and will not work here.
    redis_url: Optional[str] = None
    session_key_prefix: str = "pokemon-dashboard:session:"

    # ── Rate limiting ──
    # Applies to the authentication endpoints only. Generous enough that a
    # trainer fumbling a password is never affected, tight enough that
    # credential grinding is impractical.
    rate_limit_auth: str = "20 per minute"
    rate_limit_enabled: bool = True
    # The Redis instance may be shared with other applications, and
    # Flask-Limiter's default keys carry no application identifier.
    rate_limit_key_prefix: str = "pokemon-dashboard"

    # ── CORS ──
    # Comma-separated origin list. "*" is rejected when DEBUG is false: auth
    # rides in cookies, so a permissive origin policy hands every authenticated
    # endpoint to any site a trainer happens to visit.
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

        if not self.redis_url:
            problems.append(
                "  - REDIS_URL is not set, so sessions would fall back to Flask's "
                "signed cookie. That cookie is readable by the client, which "
                "exposes quiz answers and arena state."
            )

        if "*" in self.cors_origin_list:
            problems.append(
                "  - CORS_ORIGINS is '*'. Authentication rides in cookies, so a "
                "wildcard origin exposes every authenticated endpoint to any "
                "site. Set it to the deployed origin, e.g. "
                "https://your-app.onrender.com"
            )

        if problems:
            raise ValueError(
                "Refusing to start: DEBUG is false but the configuration is not "
                "production-safe:\n"
                + "\n".join(problems)
                + "\n\n"
                + _GENERATE_HINT
            )

        return self

    @property
    def database_uri(self) -> str:
        """The database URI to connect with, normalised for SQLAlchemy 2.

        ``DATABASE_URL`` takes precedence so a hosting provider can inject the
        connection string without the app needing to know its own environment.

        Returns:
            A SQLAlchemy-compatible URI. The legacy ``postgres://`` scheme is
            rewritten to ``postgresql://`` — SQLAlchemy 2 refuses the former,
            and several providers still hand it out.
        """
        uri = self.database_url or self.sqlalchemy_database_uri
        legacy_prefix = "postgres://"
        if uri.startswith(legacy_prefix):
            uri = f"postgresql://{uri[len(legacy_prefix):]}"
        return uri

    @property
    def engine_options(self) -> dict:
        """Engine options for SQLAlchemy, tailored to the target dialect.

        ``pool_pre_ping`` matters most: Neon's pooled endpoint closes idle
        connections, and without a liveness check the first query after an
        idle period fails with a stale-connection error.

        Pool sizing is only meaningful for server-backed databases. SQLite's
        in-memory pool rejects ``max_overflow`` outright, so it is omitted
        rather than guessed at.

        Returns:
            Keyword arguments suitable for ``create_engine``.
        """
        options = {"pool_pre_ping": self.database_pool_pre_ping}

        if not self.database_uri.startswith("sqlite"):
            options["pool_size"] = self.database_pool_size
            options["max_overflow"] = self.database_max_overflow

        return options

    @property
    def cors_origin_list(self) -> list[str]:
        """Allowed CORS origins, parsed from the comma-separated setting.

        Returns:
            One entry per origin, whitespace stripped and blanks dropped.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def rate_limit_storage_uri(self) -> str:
        """Where rate-limit counters live.

        Redis when available: in-memory counters reset on every restart and are
        per-process, so a limit would be trivially bypassed by waiting for a
        deploy or by hitting a different worker.

        Returns:
            A limits-compatible storage URI.
        """
        return self.redis_url or "memory://"

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
