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
    """Construct Settings from explicit values only, ignoring any .env file."""
    values = {
        "debug": False,
        "flask_secret_key": secrets.token_urlsafe(48),
        "jwt_secret_key": secrets.token_urlsafe(48),
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
