"""Security regression tests.

Each test here corresponds to a finding in tasks/spec.md and must fail against
the vulnerable code. Do not weaken these to make a change pass.
"""

import ast
import csv
import os

import pytest

from App.app import app, db
from App.blueprints.auth import initialize_db
from App.models import User, Pokemon
from tests.helpers import login



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


class TestNoCodeExecutionFromSeedData:
    """A2 — auth.py:41 ran eval() on the abilities column of every CSV row."""

    @staticmethod
    def _csv_with_abilities(tmpdir, payload):
        """Build a valid one-row CSV, substituting the abilities cell."""
        with open('pokemon.csv', newline='', encoding='utf8') as src:
            reader = csv.reader(src)
            header = next(reader)
            row = next(reader)
        row[header.index('abilities')] = payload

        path = os.path.join(tmpdir, 'malicious.csv')
        with open(path, 'w', newline='', encoding='utf8') as out:
            writer = csv.writer(out)
            writer.writerow(header)
            writer.writerow(row)
        return path

    def test_no_eval_call_anywhere_in_app_source(self):
        """Static guard: no module under App/ may call eval()."""
        offenders = []
        for root, _dirs, files in os.walk('App'):
            for filename in files:
                if not filename.endswith('.py'):
                    continue
                source_path = os.path.join(root, filename)
                with open(source_path, encoding='utf8') as handle:
                    tree = ast.parse(handle.read(), filename=source_path)
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == 'eval'
                    ):
                        offenders.append(f"{source_path}:{node.lineno}")
        assert offenders == [], f"eval() is called at: {offenders}"

    def test_malicious_abilities_cell_does_not_execute(self, client, tmp_path):
        """A crafted abilities cell must raise, not run arbitrary code."""
        canary = tmp_path / 'pwned.txt'
        payload = f"__import__('pathlib').Path({str(canary)!r}).write_text('pwned')"
        csv_path = self._csv_with_abilities(str(tmp_path), payload)

        with app.app_context():
            with pytest.raises((ValueError, SyntaxError)):
                initialize_db(csv_path)

        assert not canary.exists(), (
            "seeding executed code from the CSV — the abilities column is still "
            "being passed to eval()"
        )

    def test_malformed_abilities_cell_names_the_offending_row(self, client, tmp_path):
        """A broken cell must fail loudly enough to locate it."""
        csv_path = self._csv_with_abilities(str(tmp_path), "['Overgrow',")

        with app.app_context():
            with pytest.raises(ValueError) as excinfo:
                initialize_db(csv_path)

        message = str(excinfo.value)
        assert 'abilities' in message.lower()
        assert 'Bulbasaur' in message, f"error does not identify the row: {message}"

    def test_real_seed_data_still_parses_identically(self, client):
        """Behaviour guard: the switch must not change what gets stored."""
        with app.app_context():
            bulbasaur = Pokemon.query.filter_by(name='Bulbasaur').first()
            assert bulbasaur is not None
            assert bulbasaur.abilities == 'Overgrow,Chlorophyll'
            assert Pokemon.query.count() == 801


class TestCsrfProtection:
    """A4 — auth rides in cookies, so every mutating route was cross-site forgeable.

    A page on any other origin could POST to /pokemon/<id>, /release-pokemon/<id>
    or the arena endpoints, and the browser would attach the victim's cookies.
    """

    @staticmethod
    def _csrf(client, cookie_name="csrf_access_token"):
        cookie = client.get_cookie(cookie_name)
        return cookie.value if cookie else None

    def test_csrf_protection_is_enabled(self):
        assert app.config["JWT_COOKIE_CSRF_PROTECT"] is True

    def test_login_issues_a_csrf_cookie_readable_by_javascript(self, client):
        """The double-submit pattern needs JS to be able to read this one."""
        login(client, "bob", "bobpass")
        assert self._csrf(client) is not None, "no csrf_access_token cookie was set"

    def test_post_without_csrf_token_is_rejected(self, client):
        """A forged cross-site POST carries cookies but cannot read them."""
        login(client, "bob", "bobpass")
        response = client.post("/arena/attack/1")
        assert response.status_code == 401, (
            f"mutating request without a CSRF token returned {response.status_code}; "
            "cross-site forgery is still possible"
        )

    def test_post_with_wrong_csrf_token_is_rejected(self, client):
        login(client, "bob", "bobpass")
        response = client.post(
            "/arena/run", headers={"X-CSRF-TOKEN": "not-the-right-token"}
        )
        assert response.status_code == 401

    def test_post_with_correct_csrf_token_succeeds(self, client):
        login(client, "bob", "bobpass")
        response = client.post(
            "/arena/run", headers={"X-CSRF-TOKEN": self._csrf(client)}
        )
        assert response.status_code == 200

    def test_form_submissions_accept_the_token_as_a_field(self, client):
        """Server-rendered forms cannot set headers; they post a hidden field."""
        login(client, "bob", "bobpass")
        assert app.config["JWT_CSRF_CHECK_FORM"] is True
        response = client.post(
            "/pokemon/4",
            data={"nickname": "Charmy", "csrf_token": self._csrf(client)},
            headers={"Referer": "/"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Successfully captured" in response.data

    def test_form_submission_without_the_field_is_rejected(self, client):
        login(client, "bob", "bobpass")
        response = client.post(
            "/pokemon/5", data={"nickname": "NoToken"}, headers={"Referer": "/"}
        )
        assert response.status_code == 401

    def test_refresh_endpoint_uses_the_refresh_csrf_token(self, client):
        """Refresh is validated against its own token, not the access one."""
        login(client, "bob", "bobpass")
        rejected = client.post(
            "/api/auth/refresh", headers={"X-CSRF-TOKEN": self._csrf(client)}
        )
        assert rejected.status_code == 401

        accepted = client.post(
            "/api/auth/refresh",
            headers={"X-CSRF-TOKEN": self._csrf(client, "csrf_refresh_token")},
        )
        assert accepted.status_code == 200

    def test_get_requests_are_unaffected(self, client):
        """CSRF applies to state changes; reads must keep working."""
        login(client, "bob", "bobpass")
        assert client.get("/arena/encounter").status_code == 200
        assert client.get("/api/quiz/question").status_code == 200


class TestAuthRateLimiting:
    """A11/A15 — /login and /signup accepted unlimited attempts from one IP.

    Nothing stopped a script from grinding passwords, or from creating accounts
    in bulk. The limiter is disabled for the rest of the suite (see conftest),
    so these tests turn it on deliberately.
    """

    @pytest.fixture(autouse=True)
    def _limiter_on(self):
        from App.extensions import limiter

        limiter.enabled = True
        limiter.reset()
        yield
        limiter.reset()
        limiter.enabled = False

    def test_limit_is_twenty_per_minute(self):
        from App.config import get_settings

        assert get_settings().rate_limit_auth == "20 per minute"

    def test_login_attempts_are_capped(self, client):
        """The 21st attempt in a minute is refused."""
        for attempt in range(20):
            response = client.post(
                "/login", data={"username": "bob", "password": "wrong"}
            )
            assert response.status_code != 429, f"throttled early, on attempt {attempt + 1}"

        response = client.post("/login", data={"username": "bob", "password": "wrong"})
        assert response.status_code == 429

    def test_signup_is_capped(self, client):
        for index in range(20):
            client.post(
                "/signup",
                data={
                    "username": f"spam{index}",
                    "email": f"spam{index}@example.com",
                    "password": "password",
                },
            )
        response = client.post(
            "/signup",
            data={
                "username": "spam-final",
                "email": "final@example.com",
                "password": "password",
            },
        )
        assert response.status_code == 429

    def test_a_normal_login_is_never_throttled(self, client):
        """Guard against the limit being set so tight it hurts real users."""
        response = client.post(
            "/login", data={"username": "bob", "password": "bobpass"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    def test_throttled_form_post_is_readable_not_raw_json(self, client):
        """A browser posting the login form should see the page, not a JSON blob."""
        for _ in range(21):
            response = client.post(
                "/login", data={"username": "bob", "password": "wrong"},
                follow_redirects=True,
            )
        assert response.status_code == 429
        assert b"{" not in response.data[:1], "raw JSON returned to a form submission"
        assert b"Too many" in response.data or b"too many" in response.data

    def test_limits_are_stored_in_redis_when_configured(self):
        """In-memory limits would reset on every restart and not span workers."""
        from App.config import Settings

        settings = Settings(
            _env_file=None,
            debug=False,
            flask_secret_key="x" * 48,
            jwt_secret_key="y" * 48,
            redis_url="rediss://default:pw@example.upstash.io:6379",
            cors_origins="https://example.onrender.com",
        )
        assert settings.rate_limit_storage_uri == settings.redis_url

    def test_falls_back_to_in_memory_without_redis(self):
        from App.config import Settings

        settings = Settings(_env_file=None, debug=True)
        assert settings.rate_limit_storage_uri == "memory://"


class TestSignupFailureHandling:
    """A8 — a duplicate username reported success and left the session dirty."""

    def test_duplicate_username_does_not_report_success(self, client):
        response = client.post(
            "/signup",
            data={"username": "bob", "email": "other@example.com", "password": "pw"},
            follow_redirects=True,
        )
        assert b"Username already exists" in response.data
        assert b"Account created" not in response.data, (
            "a failed signup told the user their account was created"
        )

    def test_duplicate_username_creates_no_second_account(self, client):
        with app.app_context():
            before = User.query.filter_by(username="bob").count()
        client.post(
            "/signup",
            data={"username": "bob", "email": "other@example.com", "password": "pw"},
            follow_redirects=True,
        )
        with app.app_context():
            assert User.query.filter_by(username="bob").count() == before

    def test_session_is_usable_after_a_failed_signup(self, client):
        """Without a rollback the session stays poisoned and later writes fail."""
        client.post(
            "/signup",
            data={"username": "bob", "email": "other@example.com", "password": "pw"},
            follow_redirects=True,
        )
        response = client.post(
            "/signup",
            data={"username": "brandnew", "email": "new@example.com", "password": "pw"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert User.query.filter_by(username="brandnew").first() is not None

    def test_duplicate_email_is_handled_too(self, client):
        response = client.post(
            "/signup",
            data={"username": "notbob", "email": "bob@mail.com", "password": "pw"},
            follow_redirects=True,
        )
        assert b"Account created" not in response.data


class TestErrorResponsesDoNotLeakInternals:
    """A10 — handlers returned str(e), exposing SQL, paths and stack context."""

    # Our own exception type carries hand-authored, user-facing messages
    # ("Pokemon 'X' not found"). Echoing those back is intentional; echoing an
    # arbitrary exception is not.
    DOMAIN_EXCEPTIONS = {"PokemonAnalyticsError", "ValueError", "KeyError"}

    def test_broad_handlers_do_not_return_raw_exception_text(self):
        """Static guard: only typed domain errors may reach the client verbatim."""
        offenders = []
        for root, _dirs, files in os.walk("App/blueprints"):
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(root, filename)
                with open(path, encoding="utf8") as handle:
                    tree = ast.parse(handle.read(), filename=path)

                for handler in (
                    node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
                ):
                    caught = handler.type
                    names = set()
                    if isinstance(caught, ast.Name):
                        names.add(caught.id)
                    elif isinstance(caught, ast.Tuple):
                        names |= {e.id for e in caught.elts if isinstance(e, ast.Name)}
                    if names & self.DOMAIN_EXCEPTIONS:
                        continue  # deliberate, safe domain message

                    bound = handler.name
                    if bound is None:
                        continue
                    for node in ast.walk(handler):
                        if (
                            isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "str"
                            and node.args
                            and isinstance(node.args[0], ast.Name)
                            and node.args[0].id == bound
                        ):
                            offenders.append(f"{path}:{node.lineno}")

        assert offenders == [], (
            f"broad exception handlers leak raw exception text at: {offenders}"
        )

    def test_unexpected_failure_returns_a_generic_message(self, auth_client, monkeypatch):
        """Force an internal error and confirm the detail stays server-side."""
        from App.blueprints import analytics as analytics_module

        secret = "SECRET-INTERNAL-DETAIL-/etc/passwd"

        class Exploding:
            def get_descriptive_stats(self):
                raise RuntimeError(secret)

        monkeypatch.setattr(analytics_module.state, "instance", Exploding())
        monkeypatch.setattr(analytics_module.state, "ready", True)

        response = auth_client.get("/api/pokemon-analytics/stats")
        assert response.status_code == 500
        assert secret.encode() not in response.data, (
            f"internal error text leaked to the client: {response.data[:200]}"
        )


class TestCorsIsRestricted:
    """A6 — CORS defaulted to '*' while authentication rides in cookies."""

    def test_production_rejects_a_wildcard_origin(self):
        from App.config import Settings

        with pytest.raises(Exception) as excinfo:
            Settings(
                _env_file=None,
                debug=False,
                flask_secret_key="x" * 48,
                jwt_secret_key="y" * 48,
                redis_url="rediss://default:pw@example.upstash.io:6379",
                cors_origins="*",
            )
        assert "CORS_ORIGINS" in str(excinfo.value)

    def test_production_accepts_an_explicit_origin(self):
        from App.config import Settings

        settings = Settings(
            _env_file=None,
            debug=False,
            flask_secret_key="x" * 48,
            jwt_secret_key="y" * 48,
            redis_url="rediss://default:pw@example.upstash.io:6379",
            cors_origins="https://pokemon-dashboard.onrender.com",
        )
        assert settings.cors_origin_list == ["https://pokemon-dashboard.onrender.com"]

    def test_multiple_origins_are_parsed(self):
        from App.config import Settings

        settings = Settings(
            _env_file=None,
            debug=True,
            cors_origins="https://a.example.com, https://b.example.com",
        )
        assert settings.cors_origin_list == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_debug_mode_still_allows_the_wildcard(self):
        from App.config import Settings

        assert Settings(_env_file=None, debug=True, cors_origins="*").cors_origin_list == ["*"]


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
