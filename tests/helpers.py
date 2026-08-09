"""Shared helpers for the test suite.

These drive the real form endpoints rather than forging cookies, so the tests
exercise the same auth path a browser does.
"""


def login(client, username, password):
    """Log in via the login form, setting access and refresh cookies."""
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def signup(client, username, email, password):
    """Register via the signup form, setting access and refresh cookies."""
    return client.post(
        "/signup",
        data={"username": username, "email": email, "password": password},
        follow_redirects=True,
    )
