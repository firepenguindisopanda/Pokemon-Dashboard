"""Session storage tests.

A7 — the quiz's correct answer and the arena's encounter HP were kept in
Flask's default session cookie. That cookie is *signed, not encrypted*: any
player can base64-decode it and read the answer, then farm unlimited pokeballs.

Moving session state server-side fixes the whole class of problem rather than
the two known instances, so these tests assert on what the cookie contains,
not on which fields happen to be in it today.
"""

import base64
import json
import zlib

import pytest

from App.app import app as flask_app
from App.config import Settings


def decode_session_cookie(client):
    """Decode a Flask session cookie without knowing the signing secret.

    This is exactly what an attacker does — the cookie is signed to prevent
    tampering, but its payload is plain readable base64.

    Returns:
        The decoded payload dict, or None when no session cookie is set.
    """
    cookie = client.get_cookie("session")
    if cookie is None:
        return None

    payload = cookie.value.split(".")[0]
    if payload.startswith("-"):  # itsdangerous compression marker
        payload = payload[1:]
        raw = zlib.decompress(base64.urlsafe_b64decode(payload + "=="))
    else:
        raw = base64.urlsafe_b64decode(payload + "==")

    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


class TestNoGameStateInTheCookie:
    """The client must not be able to read or forge game state."""

    def test_quiz_answer_is_not_readable_from_the_cookie(self, auth_client):
        auth_client.get("/api/quiz/question")
        payload = decode_session_cookie(auth_client) or {}
        assert "quiz_current_answer" not in payload, (
            "the correct quiz answer is readable in the session cookie — a "
            f"player can decode it and farm pokeballs: {payload}"
        )

    def test_no_cookie_value_matches_the_correct_answer(self, auth_client):
        """Guard against merely renaming the key."""
        auth_client.get("/api/quiz/question")
        with auth_client.session_transaction() as session:
            answer = session.get("quiz_current_answer")
        assert answer is not None, "test setup failed: no answer recorded server-side"

        payload = decode_session_cookie(auth_client) or {}
        assert answer not in {str(v) for v in payload.values()}, (
            f"the answer {answer!r} still appears in the cookie payload"
        )

    def test_arena_encounter_hp_is_not_readable_from_the_cookie(self, auth_client):
        auth_client.get("/arena/encounter")
        payload = decode_session_cookie(auth_client) or {}
        assert "arena_hp" not in payload and "arena_pokemon_id" not in payload, (
            f"arena encounter state is exposed to the client: {payload}"
        )

    def test_cookie_carries_only_an_opaque_identifier(self, auth_client):
        """Whatever we store later must not leak either."""
        auth_client.get("/api/quiz/question")
        auth_client.get("/arena/encounter")
        payload = decode_session_cookie(auth_client)
        assert payload in (None, {}), (
            f"session cookie still carries application data: {payload}"
        )


class TestGameplayStillWorks:
    """Moving storage must not change behaviour."""

    def test_quiz_scoring_still_awards_pokeballs(self, auth_client):
        from App.models import User

        with flask_app.app_context():
            before = User.query.filter_by(username="bob").first().pokeballs

        auth_client.get("/api/quiz/question")
        with auth_client.session_transaction() as session:
            answer = session.get("quiz_current_answer")

        result = auth_client.post("/api/quiz/answer", json={"answer": answer}).get_json()
        assert result["correct"] is True
        assert result["pokeballs"] == before + 1

    def test_arena_encounter_and_attack_still_work(self, auth_client):
        encounter = auth_client.get("/arena/encounter").get_json()
        with auth_client.session_transaction() as session:
            pokemon_id = session.get("arena_pokemon_id")
        assert pokemon_id is not None

        result = auth_client.post(f"/arena/attack/{pokemon_id}").get_json()
        assert 10 <= result["damage"] <= 29
        # HP is clamped at zero — damage can exceed a low-HP Pokemon's total.
        assert result["current_hp"] == max(0, encounter["max_hp"] - result["damage"])

    def test_session_survives_across_requests(self, auth_client):
        auth_client.get("/arena/encounter")
        with auth_client.session_transaction() as session:
            first = session.get("arena_pokemon_id")
        auth_client.get("/arena/current")
        with auth_client.session_transaction() as session:
            assert session.get("arena_pokemon_id") == first


class TestProductionRequiresRedis:
    """Falling back to cookie sessions in production would silently restore the leak."""

    def test_production_without_redis_url_refuses_to_start(self):
        with pytest.raises(Exception) as excinfo:
            Settings(
                _env_file=None,
                debug=False,
                flask_secret_key="x" * 48,
                jwt_secret_key="y" * 48,
                redis_url=None,
            )
        assert "REDIS_URL" in str(excinfo.value)

    def test_production_with_redis_url_is_accepted(self):
        settings = Settings(
            _env_file=None,
            debug=False,
            flask_secret_key="x" * 48,
            jwt_secret_key="y" * 48,
            redis_url="rediss://default:pw@example.upstash.io:6379",
            cors_origins="https://example.onrender.com",
        )
        assert settings.redis_url.startswith("rediss://")

    def test_debug_mode_tolerates_a_missing_redis_url(self):
        """Local development stays frictionless."""
        settings = Settings(
            _env_file=None,
            debug=True,
            flask_secret_key="change-me-in-production",
            jwt_secret_key="change-me-in-production",
        )
        assert settings.redis_url is None
