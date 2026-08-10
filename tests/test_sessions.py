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
from types import SimpleNamespace

import pytest
from flask.sessions import TaggedJSONSerializer
from itsdangerous import URLSafeTimedSerializer

from App.app import app as flask_app
from App.config import Settings


def decode_session_cookie(client):
    """Decode a Flask session cookie without knowing the signing secret.

    This is exactly what an attacker does — the cookie is signed to prevent
    tampering, but its payload is plain readable base64.

    Returns:
        The decoded payload dict, or None when there is no session cookie or
        the cookie is not a readable JSON payload — which is the expected
        result for a server-side session, where the cookie holds only an
        opaque id.
    """
    cookie = client.get_cookie("session")
    if cookie is None:
        return None

    value = cookie.value
    # itsdangerous marks a compressed payload with a leading "." on the whole
    # value. That is also the segment separator, so the marker has to come off
    # before splitting. Note the marker is "." and not "-": "-" is an ordinary
    # base64url character that begins roughly 1 in 64 session ids.
    is_compressed = value.startswith(".")
    if is_compressed:
        value = value[1:]

    payload = value.split(".")[0]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        if is_compressed:
            raw = zlib.decompress(raw)
    except (ValueError, zlib.error):
        return None

    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def _client_holding(cookie_value):
    """A stand-in test client carrying one prepared session cookie."""
    return SimpleNamespace(
        get_cookie=lambda name: (
            None
            if name != "session" or cookie_value is None
            else SimpleNamespace(value=cookie_value)
        )
    )


def _signed_cookie(payload):
    """Build a real Flask cookie-session value the way Flask itself would.

    Flask compresses the payload when that makes it smaller, so a large or
    repetitive `payload` exercises the compressed branch and a small one does
    not.
    """
    serializer = URLSafeTimedSerializer(
        "secret",
        salt="cookie-session",
        serializer=TaggedJSONSerializer(),
        signer_kwargs={"key_derivation": "hmac"},
    )
    return serializer.dumps(payload)


class TestSessionCookieDecoder:
    """The decoder above is the instrument every leak test reads from.

    It had two defects that made those tests unreliable, so it is now tested
    directly rather than trusted:

    1. It treated a leading "-" as itsdangerous' compression marker. The real
       marker is "."; "-" is an ordinary base64url character, and a
       server-side session id begins with one about 1 time in 64. That raised
       `zlib.error` and turned the suite flaky at roughly 8% per run.
    2. It could not read a genuinely compressed cookie at all, returning None
       — which the leak tests below coerce to `{}` and then pass. A cookie
       that actually carried the quiz answer would have been reported clean.
    """

    def test_an_opaque_session_id_beginning_with_a_dash_is_not_mistaken_for_compression(self):
        """Server-side session ids are random base64url; ~1 in 64 starts "-".

        This is a real 43-character `secrets.token_urlsafe(32)` id with its
        first character swapped for "-", so it reproduces the production
        failure exactly rather than tripping some other decode error.
        """
        client = _client_holding("--xSRiBhAAUKGxlbkt2_tB7uuu8hMJ8oerXfokv7Pv0")
        assert decode_session_cookie(client) is None

    def test_a_compressed_cookie_carrying_game_state_is_decoded(self):
        """The security-critical case: a real leak must not decode to None."""
        cookie = _signed_cookie({"quiz_current_answer": "Pikachu", "pad": "aaaaaaaaaa" * 60})
        assert cookie.startswith("."), "test setup failed: payload was not compressed"

        payload = decode_session_cookie(_client_holding(cookie))
        assert payload is not None, (
            "a compressed cookie decoded to None — a leak in a compressed "
            "cookie would be invisible to every test in this file"
        )
        assert payload["quiz_current_answer"] == "Pikachu"

    def test_an_uncompressed_cookie_carrying_game_state_is_decoded(self):
        """Regression guard on the path that already worked."""
        cookie = _signed_cookie({"quiz_current_answer": "Mew"})
        assert not cookie.startswith("."), "test setup failed: payload was compressed"

        payload = decode_session_cookie(_client_holding(cookie))
        assert payload["quiz_current_answer"] == "Mew"

    def test_no_cookie_decodes_to_none(self):
        assert decode_session_cookie(_client_holding(None)) is None


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
        """Local development stays frictionless.

        `redis_url` is passed explicitly rather than left to fall through to
        the field default. `_env_file=None` only switches off the dotenv
        source — environment variables still apply — so without this the test
        asserts on whatever REDIS_URL happens to hold, which is conftest's
        business and not this test's subject.
        """
        settings = Settings(
            _env_file=None,
            debug=True,
            flask_secret_key="change-me-in-production",
            jwt_secret_key="change-me-in-production",
            redis_url=None,
        )
        assert settings.redis_url is None
