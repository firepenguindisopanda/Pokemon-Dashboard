"""Configuration safety tests.

A5 — the app started happily in production with `FLASK_SECRET_KEY` and
`JWT_SECRET_KEY` still set to placeholder strings. A guessable JWT signing key
lets anyone mint a valid token for any user, so the app must refuse to start
in a production posture rather than run silently unprotected.
"""

import secrets

import pytest
from pydantic import ValidationError

from App.config import Settings, get_settings

# The literal values that were live in the Render dashboard, plus the in-code defaults.
KNOWN_PLACEHOLDERS = [
    "change-me-in-production",
    "change-me-production",
    "super-secret-key-change-in-production",
    "super-secret-jwt-key-change-in-production",
]


def build_settings(**overrides):
    """Construct Settings from explicit values only, ignoring any .env file.

    Defaults to a valid *production* configuration so each test can make
    exactly one thing invalid.
    """
    values = {
        "debug": False,
        "flask_secret_key": secrets.token_urlsafe(48),
        "jwt_secret_key": secrets.token_urlsafe(48),
        # Required with debug off — sessions must not fall back to cookies.
        "redis_url": "rediss://default:pw@example.upstash.io:6379",
        # Required with debug off — a wildcard origin is rejected.
        "cors_origins": "https://example.onrender.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestProductionRejectsWeakSecrets:
    """With debug off, placeholder or trivially weak secrets must block startup."""

    @pytest.mark.parametrize("placeholder", KNOWN_PLACEHOLDERS)
    def test_placeholder_flask_secret_is_rejected(self, placeholder):
        with pytest.raises(ValidationError) as excinfo:
            build_settings(flask_secret_key=placeholder)
        assert "FLASK_SECRET_KEY" in str(excinfo.value)

    @pytest.mark.parametrize("placeholder", KNOWN_PLACEHOLDERS)
    def test_placeholder_jwt_secret_is_rejected(self, placeholder):
        with pytest.raises(ValidationError) as excinfo:
            build_settings(jwt_secret_key=placeholder)
        assert "JWT_SECRET_KEY" in str(excinfo.value)

    def test_short_secret_is_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            build_settings(jwt_secret_key="hunter2")
        assert "JWT_SECRET_KEY" in str(excinfo.value)

    def test_empty_secret_is_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            build_settings(flask_secret_key="   ")
        assert "FLASK_SECRET_KEY" in str(excinfo.value)

    def test_error_reports_every_offending_variable_at_once(self):
        """Fixing one placeholder should not just reveal the next one."""
        with pytest.raises(ValidationError) as excinfo:
            build_settings(
                flask_secret_key="change-me-in-production",
                jwt_secret_key="change-me-in-production",
            )
        message = str(excinfo.value)
        assert "FLASK_SECRET_KEY" in message and "JWT_SECRET_KEY" in message

    def test_error_explains_how_to_generate_a_strong_secret(self):
        with pytest.raises(ValidationError) as excinfo:
            build_settings(jwt_secret_key="change-me-in-production")
        assert "secrets.token_urlsafe" in str(excinfo.value)

    def test_the_in_code_defaults_are_themselves_rejected(self):
        """Falling through to the class defaults must not silently work."""
        with pytest.raises(ValidationError):
            Settings(_env_file=None, debug=False)


class TestValidConfigurationStillStarts:
    """The guard must not obstruct legitimate configurations."""

    def test_strong_secrets_are_accepted_in_production(self):
        settings = build_settings()
        assert settings.debug is False
        assert len(settings.jwt_secret_key) >= 32

    def test_debug_mode_tolerates_placeholders_for_local_development(self):
        """Local dev must stay frictionless — the guard is about production."""
        settings = build_settings(
            debug=True,
            flask_secret_key="change-me-in-production",
            jwt_secret_key="change-me-in-production",
        )
        assert settings.debug is True

    def test_jwt_expiry_helpers_still_work(self):
        settings = build_settings()
        assert settings.jwt_access_expires_timedelta.total_seconds() == 900
        assert settings.jwt_refresh_expires_timedelta.days == 7


class TestStartupFailureIsReadable:
    """An operator reading a deploy log must see the reason, not a traceback."""

    def test_get_settings_exits_rather_than_raising_a_pydantic_traceback(
        self, monkeypatch, capsys
    ):
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("FLASK_SECRET_KEY", "change-me-in-production")
        monkeypatch.setenv("JWT_SECRET_KEY", "change-me-in-production")

        get_settings.cache_clear()
        try:
            with pytest.raises(SystemExit) as excinfo:
                get_settings()
            assert excinfo.value.code == 1

            printed = capsys.readouterr().err
            assert "CONFIGURATION ERROR" in printed
            assert "FLASK_SECRET_KEY" in printed
            assert "JWT_SECRET_KEY" in printed
            assert "Value error," not in printed, "pydantic noise leaked into the message"
        finally:
            get_settings.cache_clear()


class TestDatabaseUriResolution:
    """A12 — production must be able to point at Neon via DATABASE_URL."""

    def test_database_url_takes_precedence(self):
        settings = build_settings(
            database_url="postgresql://u:p@host/db",
            sqlalchemy_database_uri="sqlite:///data.db",
        )
        assert settings.database_uri == "postgresql://u:p@host/db"

    def test_falls_back_to_sqlite_for_local_development(self):
        settings = build_settings(sqlalchemy_database_uri="sqlite:///data.db")
        assert settings.database_uri == "sqlite:///data.db"

    def test_legacy_postgres_scheme_is_normalised(self):
        """SQLAlchemy 2 rejects `postgres://`; several providers still emit it."""
        settings = build_settings(database_url="postgres://u:p@host/db")
        assert settings.database_uri.startswith("postgresql://")

    def test_normalising_preserves_credentials_and_query_string(self):
        """Neon's URL carries sslmode and channel_binding — dropping them breaks TLS."""
        settings = build_settings(
            database_url="postgres://user:pw@ep-x.neon.tech/neondb"
                         "?sslmode=require&channel_binding=require"
        )
        assert settings.database_uri == (
            "postgresql://user:pw@ep-x.neon.tech/neondb"
            "?sslmode=require&channel_binding=require"
        )

    def test_postgresql_scheme_is_left_untouched(self):
        url = "postgresql://u:p@host/db?sslmode=require"
        assert build_settings(database_url=url).database_uri == url


class TestEngineOptions:
    """T9 — pool settings existed in config but were never applied to any engine."""

    def test_pool_pre_ping_is_always_enabled(self):
        """Neon's pooler drops idle connections; without this the next query fails."""
        assert build_settings().engine_options["pool_pre_ping"] is True

    def test_pool_sizing_is_applied_for_server_backed_databases(self):
        options = build_settings(database_url="postgresql://u:p@host/db").engine_options
        assert options["pool_size"] == 5
        assert options["max_overflow"] == 10

    def test_pool_sizing_is_omitted_for_sqlite(self):
        """SQLite's in-memory pool raises TypeError on max_overflow."""
        options = build_settings(sqlalchemy_database_uri="sqlite:///data.db").engine_options
        assert "pool_size" not in options
        assert "max_overflow" not in options

    @pytest.mark.parametrize(
        "uri",
        [
            "sqlite:///local.db",
            "sqlite://",
            "postgresql+psycopg2://u:p@host/db",
        ],
    )
    def test_engine_options_are_accepted_by_create_engine(self, uri, tmp_path):
        """The options must actually construct an engine for every dialect we use."""
        from sqlalchemy import create_engine

        if uri.startswith("sqlite:///"):
            uri = f"sqlite:///{tmp_path / 'local.db'}"
        settings = build_settings(database_url=uri)
        engine = create_engine(uri, **settings.engine_options)
        engine.dispose()


class TestAppAppliesEngineOptions:
    """The settings are worthless unless create_app actually passes them through."""

    def test_app_config_carries_engine_options(self):
        from App.app import app

        options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS")
        assert options, "SQLALCHEMY_ENGINE_OPTIONS was never set"
        assert options.get("pool_pre_ping") is True


class TestEnvExampleDocumentsTheRequirement:
    """.env.example is the only guidance an operator gets before deploying."""

    def test_env_example_covers_both_checked_secrets(self):
        with open(".env.example", encoding="utf8") as handle:
            content = handle.read()
        assert "FLASK_SECRET_KEY" in content
        assert "JWT_SECRET_KEY" in content

    def test_env_example_explains_how_to_generate_secrets(self):
        with open(".env.example", encoding="utf8") as handle:
            content = handle.read()
        assert "token_urlsafe" in content, (
            ".env.example must show how to generate a strong secret, or operators "
            "will keep shipping the placeholder"
        )


class TestSuiteIsHermetic:
    """The suite must never reach live infrastructure, whatever is configured.

    `conftest.py` used to isolate the suite by deleting DATABASE_URL and
    REDIS_URL from the environment. That covers a developer who exported them
    in their shell, but not a `.env` file — pydantic-settings reads that
    straight off disk, so deleting the variable achieves nothing.

    The consequence was not theoretical. With a real `.env` present the limiter
    bound to the production Upstash instance, and the rate-limit fixture's
    `limiter.reset()` deletes keys there — on a Redis explicitly documented as
    shared with another application.
    """

    def test_the_database_under_test_is_local(self):
        from App.app import app

        uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        assert uri.startswith("sqlite"), (
            f"the suite is pointed at a non-SQLite database: {uri.split('@')[-1]!r}. "
            "Tests would read and write live data."
        )

    def test_the_rate_limiter_does_not_use_a_network_backend(self):
        """`limiter.reset()` runs in a fixture, so a live backend loses keys."""
        from App.app import app

        uri = str(app.config.get("RATELIMIT_STORAGE_URI", ""))
        assert uri.startswith("memory://"), (
            f"rate limiting is backed by {uri.split('@')[-1]!r} rather than memory. "
            "The rate-limit fixture calls limiter.reset(), which deletes keys "
            "from that backend."
        )

    def test_settings_do_not_resolve_to_live_services(self):
        """Catches the leak at the source rather than at one consumer."""
        settings = get_settings()

        assert not settings.database_url, (
            "DATABASE_URL resolved to a value during the test run; conftest must "
            "shadow it so a .env file cannot point the suite at Neon"
        )
        assert not settings.redis_url, (
            "REDIS_URL resolved to a value during the test run; conftest must "
            "shadow it so a .env file cannot point the suite at Upstash"
        )
