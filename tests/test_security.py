"""Security regression tests.

Each test here corresponds to a finding in tasks/spec.md and must fail against
the vulnerable code. Do not weaken these to make a change pass.
"""

import os
import tempfile
import pytest
from sqlalchemy import create_engine

from App.app import app, db
from App.blueprints.auth import initialize_db
from App.models import User, Pokemon


@pytest.fixture(autouse=True)
def _use_sqlite():
    """Override the database to use a temporary SQLite file for all tests."""
    db_fd, db_path = tempfile.mkstemp()
    sqlite_uri = f"sqlite:///{db_path}"

    orig_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    app.config['SQLALCHEMY_DATABASE_URI'] = sqlite_uri
    app.config['TESTING'] = True

    with app.app_context():
        test_engine = create_engine(sqlite_uri)
        if 'sqlalchemy' in app.extensions:
            ext = app.extensions['sqlalchemy']
            for key in list(ext.engines.keys()):
                ext.engines[key].dispose()
            ext.engines[None] = test_engine

    yield

    test_engine.dispose()
    with app.app_context():
        if 'sqlalchemy' in app.extensions:
            app.extensions['sqlalchemy'].engines.pop(None, None)
    app.config['SQLALCHEMY_DATABASE_URI'] = orig_uri
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        initialize_db()

    yield client


class TestNoUnauthenticatedDatabaseWipe:
    """A1 — GET /init was an unauthenticated db.drop_all() on the public internet."""

    def test_init_route_is_not_reachable_over_http(self, client):
        """The /init route must not exist. It took no auth and dropped every table."""
        response = client.get('/init')
        assert response.status_code == 404, (
            f"/init returned {response.status_code}, not 404 — the destructive "
            "route is still reachable without authentication"
        )

    def test_requesting_init_does_not_destroy_user_data(self, client):
        """Hitting /init must not destroy a real trainer's account.

        Row counts alone cannot detect this: /init drops and then reseeds, so
        totals return to their seeded values while every real user is gone.
        This plants a canary that only survives if no wipe happened.
        """
        with app.app_context():
            canary = User(username='canary', email='canary@example.com', password='pw')
            db.session.add(canary)
            db.session.commit()
            assert User.query.filter_by(username='canary').first() is not None

        client.get('/init', follow_redirects=True)

        with app.app_context():
            survivor = User.query.filter_by(username='canary').first()
            assert survivor is not None, (
                "GET /init destroyed a real user account — the database was wiped "
                "by an unauthenticated HTTP request"
            )

    def test_no_url_rule_exposes_the_init_path(self, client):
        """No URL rule may serve /init, under any endpoint name."""
        offenders = [rule.rule for rule in app.url_map.iter_rules() if rule.rule == '/init']
        assert offenders == [], f"a destructive route is still mapped at: {offenders}"


class TestSeedingStillAvailableToOperators:
    """Removing the route must not remove the ability to seed the database."""

    def test_initialize_db_still_seeds_when_called_directly(self, client):
        """The flask init CLI command depends on this function remaining importable."""
        with app.app_context():
            initialize_db()
            assert Pokemon.query.count() == 801
            assert User.query.filter_by(username='bob').first() is not None

    def test_flask_init_cli_command_is_registered(self):
        """`flask init` must remain the supported way to seed."""
        import wsgi  # noqa: F401 — registers the CLI command on import

        assert 'init' in app.cli.commands, "the `flask init` CLI command is missing"
