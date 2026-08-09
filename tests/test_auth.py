"""Tests for refresh token endpoint and DB-resilient user lookup."""

from flask_jwt_extended import decode_token

from App.app import app, db, MinimalUser
from tests.helpers import login



class TestRefreshToken:
    """Tests for the refresh token endpoint."""

    def test_successful_refresh_returns_new_access_token(self, client):
        """Login, then use refresh token to get a new access token."""
        login(client, 'bob', 'bobpass')

        # Check refresh token cookie exists
        assert 'refresh_token' in {c.name: c.value for c in client.cookie_jar}

        # Call refresh endpoint
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['status'] == 'success'

        # Verify new access_token cookie was set
        cookies = {c.name: c.value for c in client.cookie_jar}
        assert 'access_token' in cookies

    def test_refresh_without_token_returns_401(self, client):
        """No cookies — refresh should fail."""
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 401
        data = rv.get_json()
        assert data['code'] == 'NO_TOKEN'

    def test_refresh_with_expired_token_returns_401(self, client):
        """Test with an intentionally invalid refresh token."""
        client.set_cookie('localhost', 'refresh_token', 'expired-fake-token')
        rv = client.post('/api/auth/refresh')
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
                "sub": 1,
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

        cookies = {c.name: c.value for c in client.cookie_jar}
        assert 'access_token' in cookies
        assert 'refresh_token' in cookies

    def test_signup_sets_both_cookies(self, client):
        """Signup should set both access_token and refresh_token cookies."""
        client.post(
            '/signup',
            data={'username': 'freshuser', 'email': 'fresh@example.com', 'password': 'freshpass'},
            follow_redirects=True
        )

        cookies = {c.name: c.value for c in client.cookie_jar}
        assert 'access_token' in cookies
        assert 'refresh_token' in cookies

    def test_access_token_contains_username_claim(self, client):
        """Login and decode the access token to check username claim."""
        login(client, 'bob', 'bobpass')

        cookies = {c.name: c.value for c in client.cookie_jar}
        access_token = cookies.get('access_token')
        assert access_token is not None

        with app.app_context():
            decoded = decode_token(access_token)
            # Additional claims are at top level (not nested under "claims")
            assert decoded.get('username') == 'bob'
            assert decoded.get('email') == 'bob@mail.com'
