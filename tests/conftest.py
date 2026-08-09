"""Shared pytest fixtures for the Pokemon Dashboard test suite.

The application is a module-level singleton (``App.app.app``) created at import
time, so tests swap the SQLAlchemy engine underneath it rather than building a
fresh app per test. Every database-backed test goes through ``client``, which
guarantees a throwaway SQLite file — the suite never touches a real database.
"""

import os
import tempfile

import pytest
from sqlalchemy import create_engine

from App.app import app as flask_app, db
from App.blueprints.auth import initialize_db
from tests.helpers import login


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


@pytest.fixture
def auth_client(client):
    """Test client already logged in as the seeded user 'bob'.

    Saves every authenticated test from repeating the login dance.
    """
    login(client, "bob", "bobpass")
    return client
