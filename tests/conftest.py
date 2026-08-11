"""Shared pytest fixtures for the Pokemon Dashboard test suite.

The application is a module-level singleton (``App.app.app``) created at import
time, so tests swap the SQLAlchemy engine underneath it rather than building a
fresh app per test. Every database-backed test goes through ``client``, which
guarantees a throwaway SQLite file — the suite never touches a real database.
"""

import os
import tempfile

# Isolate the suite from its surroundings BEFORE importing the app.
#
# These are *overwritten*, not deleted. Deleting them only defends against a
# developer who exported them in their shell; `Settings` also reads `.env` off
# disk via pydantic-settings, and an absent variable just lets the file's value
# through. Environment variables outrank the dotenv file, so assigning an
# explicit safe value is what actually shadows it.
#
# This is not hypothetical. With a real `.env` present the suite bound the rate
# limiter to the production Upstash instance — which is shared with another
# application — and the rate-limit fixture calls `limiter.reset()`, deleting
# keys there. `TestSuiteIsHermetic` in tests/test_config.py guards this.
#
# Empty string rather than a sentinel URL: `database_url` and `redis_url` are
# Optional and the app treats falsy as unset, which is exactly the local
# posture the suite expects.
os.environ.update({
    "DATABASE_URL": "",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "REDIS_URL": "",
    "DEBUG": "true",
    "FLASK_SECRET_KEY": "test-only-key-not-used-outside-the-suite-0123456789",
    "JWT_SECRET_KEY": "test-only-jwt-key-not-used-outside-the-suite-01234",
    "RATE_LIMIT_AUTH": "20 per minute",
    "RATE_LIMIT_ENABLED": "true",
    # Pin the feature flag so the suite's baseline is the production default
    # regardless of what a developer has in .env. The chat tests turn it on
    # for themselves.
    "CHAT_ENABLED": "false",
})

# Keep the suite out of the committed model cache. Training during tests used
# to rewrite the tracked manifest.json, so `git status` came back dirty after
# every run — and a manifest committed in that state points at artifacts that
# were never added to git.
os.environ["MODEL_CACHE_DIR"] = tempfile.mkdtemp(prefix="pokemon-model-cache-")

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402
from flask_session import Session  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from App.app import app as flask_app, db  # noqa: E402
from App.blueprints.auth import initialize_db  # noqa: E402
from App.extensions import limiter  # noqa: E402
from tests.helpers import login  # noqa: E402

# Rate limits are keyed by client IP, and every test shares 127.0.0.1. Left on,
# the counters would accumulate across the suite and unrelated tests would start
# getting 429s depending on run order. The rate-limit tests enable it
# explicitly for themselves.
limiter.enabled = False

# Exercise the real server-side session code path without touching Upstash.
# The app singleton is built at import time with no REDIS_URL, so it would
# otherwise fall back to cookie sessions and the leak tests would be testing
# the wrong thing.
flask_app.config["SESSION_TYPE"] = "redis"
flask_app.config["SESSION_REDIS"] = fakeredis.FakeRedis()
flask_app.config["SESSION_KEY_PREFIX"] = "test-session:"
flask_app.config["SESSION_PERMANENT"] = False
Session(flask_app)


@pytest.fixture
def sqlite_db():
    """Point the app at a throwaway SQLite file for the duration of one test.

    Replaces the engine at the Flask-SQLAlchemy extension level, because the
    app and its engine were already built when ``App.app`` was imported.

    Yields:
        The SQLite URI in use for this test.
    """
    db_fd, db_path = tempfile.mkstemp()
    sqlite_uri = f"sqlite:///{db_path}"

    original_uri = flask_app.config.get("SQLALCHEMY_DATABASE_URI")
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = sqlite_uri
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        test_engine = create_engine(sqlite_uri)
        if "sqlalchemy" in flask_app.extensions:
            extension = flask_app.extensions["sqlalchemy"]
            for key in list(extension.engines.keys()):
                extension.engines[key].dispose()
            extension.engines[None] = test_engine

    yield sqlite_uri

    test_engine.dispose()
    with flask_app.app_context():
        if "sqlalchemy" in flask_app.extensions:
            flask_app.extensions["sqlalchemy"].engines.pop(None, None)
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = original_uri
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(sqlite_db):
    """Test client backed by a freshly seeded throwaway database.

    Seeds the full 801-Pokemon dataset plus the default users, matching what a
    real deployment looks like after `flask init`.
    """
    with flask_app.app_context():
        db.create_all()
        initialize_db()
    return flask_app.test_client()


class CsrfAwareClient(FlaskClient):
    """Test client that sends CSRF tokens the way the real frontend does.

    `dashboard.js` attaches `X-CSRF-TOKEN` to every state-changing request, so
    gameplay tests should too — otherwise they exercise a browser that does not
    exist. Tests that target CSRF itself use the plain `client` fixture.
    """

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def open(self, *args, **kwargs):
        method = str(kwargs.get("method", "GET")).upper()
        if method not in self.SAFE_METHODS:
            path = str(args[0]) if args else str(kwargs.get("path", ""))
            # The refresh endpoint validates its own token, not the access one.
            name = (
                "csrf_refresh_token"
                if "/api/auth/refresh" in path
                else "csrf_access_token"
            )
            cookie = self.get_cookie(name)
            if cookie is not None:
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("X-CSRF-TOKEN", cookie.value)
                kwargs["headers"] = headers
        return super().open(*args, **kwargs)


@pytest.fixture
def auth_client(sqlite_db):
    """Browser-like client, logged in as the seeded user 'bob'.

    Saves every authenticated test from repeating the login dance, and carries
    CSRF tokens so it behaves like a real page.
    """
    with flask_app.app_context():
        db.create_all()
        initialize_db()

    original = flask_app.test_client_class
    flask_app.test_client_class = CsrfAwareClient
    try:
        browser = flask_app.test_client()
    finally:
        flask_app.test_client_class = original

    login(browser, "bob", "bobpass")
    return browser
