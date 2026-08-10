"""Tests for refresh token endpoint and DB-resilient user lookup."""

import pytest
from flask_jwt_extended import decode_token
from werkzeug.security import generate_password_hash

from App.app import app, db, MinimalUser
from App.models import User
from tests.helpers import auth_cookies, login



class TestRefreshToken:
    """Tests for the refresh token endpoint."""

    def test_successful_refresh_returns_new_access_token(self, auth_client):
        """Login, then use refresh token to get a new access token."""
        client = auth_client

        # Check refresh token cookie exists
        assert 'refresh_token' in auth_cookies(client)

        # Call refresh endpoint
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['status'] == 'success'

        # Verify new access_token cookie was set
        cookies = auth_cookies(client)
        assert 'access_token' in cookies

    def test_refresh_without_token_returns_401(self, client):
        """No cookies — refresh should fail."""
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 401
        data = rv.get_json()
        assert data['code'] == 'NO_TOKEN'

    def test_refresh_with_expired_token_returns_401(self, client):
        """Test with an intentionally invalid refresh token."""
        client.set_cookie('refresh_token', 'expired-fake-token')
        client.set_cookie('csrf_refresh_token', 'any-csrf-value')
        rv = client.post(
            '/api/auth/refresh', headers={'X-CSRF-TOKEN': 'any-csrf-value'}
        )
        assert rv.status_code == 401
        data = rv.get_json()
        assert data['code'] == 'INVALID_TOKEN'

    def test_protected_page_after_logout_redirects(self, client):
        """After logout, protected pages should redirect to login."""
        login(client, 'bob', 'bobpass')

        # Logout clears both tokens
        rv = client.get('/logout', follow_redirects=True)
        assert b'Logged out' in rv.data

        # Refreshing should now fail (no refresh token)
        rv2 = client.post('/api/auth/refresh')
        assert rv2.status_code == 401


class TestUserLookupResilience:
    """Tests for the DB-resilient user lookup callback."""

    def test_user_lookup_normal(self, client):
        """Normal case: DB is up, user exists."""
        login(client, 'bob', 'bobpass')
        rv = client.get('/app', follow_redirects=True)
        assert rv.status_code == 200
        # Should show bob's username in the page
        assert b'bob' in rv.data or b'Bob' in rv.data or b'Bobby' in rv.data

    def test_user_lookup_fallback_to_minimal_user(self, client):
        """Simulate DB failure: user_lookup_callback should fallback to claims."""
        login(client, 'bob', 'bobpass')

        with app.app_context():
            jwt_data = {
                "sub": "1",
                "claims": {"username": "bob", "email": "bob@mail.com"}
            }
            from App.app import user_lookup_callback
            from sqlalchemy.exc import OperationalError

            # Mock the db.session.get to raise an error
            original_get = db.session.get

            def mock_get(model, ident):
                raise OperationalError("mock", "mock", "mock")

            db.session.get = mock_get
            try:
                result = user_lookup_callback(None, jwt_data)
                assert result is not None
                assert isinstance(result, MinimalUser)
                assert result.username == "bob"
                assert result.email == "bob@mail.com"
            finally:
                db.session.get = original_get


class TestTokenClaims:
    """Tests that additional claims are properly embedded in tokens."""

    def test_login_sets_both_cookies(self, client):
        """Login should set both access_token and refresh_token cookies."""
        login(client, 'bob', 'bobpass')

        cookies = auth_cookies(client)
        assert 'access_token' in cookies
        assert 'refresh_token' in cookies

    def test_signup_sets_both_cookies(self, client):
        """Signup should set both access_token and refresh_token cookies."""
        client.post(
            '/signup',
            data={'username': 'freshuser', 'email': 'fresh@example.com', 'password': 'freshpass'},
            follow_redirects=True
        )

        cookies = auth_cookies(client)
        assert 'access_token' in cookies
        assert 'refresh_token' in cookies

    def test_access_token_contains_username_claim(self, client):
        """Login and decode the access token to check username claim."""
        login(client, 'bob', 'bobpass')

        cookies = auth_cookies(client)
        access_token = cookies.get('access_token')
        assert access_token is not None

        with app.app_context():
            decoded = decode_token(access_token)
            # Additional claims are at top level (not nested under "claims")
            assert decoded.get('username') == 'bob'
            assert decoded.get('email') == 'bob@mail.com'


class TestPasswordStorage:
    """A3 — hashing moved from a single SHA-256 pass to Werkzeug's scrypt default.

    A scrypt hash is 162 characters. SQLite ignores VARCHAR limits, so an
    undersized column is invisible locally and only fails once the app runs on
    Postgres — which is exactly the cutover in T10. These tests assert the
    declared column width so the mismatch cannot reach production.
    """

    def test_password_column_is_wide_enough_for_a_scrypt_hash(self):
        """The declared width must hold a real hash, with room to spare."""
        declared = User.__table__.c.password.type.length
        sample = generate_password_hash('any-password')
        assert declared >= len(sample), (
            f"User.password is String({declared}) but a scrypt hash is "
            f"{len(sample)} characters — Postgres will reject this on insert"
        )

    def test_password_column_has_headroom_for_stronger_future_defaults(self):
        """Werkzeug's default may get more expensive; leave margin."""
        assert User.__table__.c.password.type.length >= 255

    def test_new_hashes_use_scrypt_and_round_trip(self):
        user = User(username='hashcheck', email='h@example.com', password='correct horse')
        assert user.password.startswith('scrypt:'), user.password[:32]
        assert user.check_password('correct horse') is True
        assert user.check_password('wrong horse') is False

    def test_hash_is_salted_so_equal_passwords_differ(self):
        a = User(username='a', email='a@example.com', password='same-password')
        b = User(username='b', email='b@example.com', password='same-password')
        assert a.password != b.password, "identical passwords produced identical hashes"

    def test_sha256_is_no_longer_reachable(self):
        """Werkzeug 3 removed it; assert we never reintroduce the pin."""
        with pytest.raises(ValueError):
            generate_password_hash('x', method='sha256')

    def test_seeded_users_are_stored_with_scrypt(self, client):
        """`flask init` must produce hashes that fit and verify."""
        with app.app_context():
            declared = User.__table__.c.password.type.length
            for username, password in (('bob', 'bobpass'), ('nick', 'nickpass')):
                user = User.query.filter_by(username=username).first()
                assert user is not None
                assert user.password.startswith('scrypt:')
                assert len(user.password) <= declared
                assert user.check_password(password) is True
