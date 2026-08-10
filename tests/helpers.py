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


def auth_cookies(client):
    """Return the client's auth cookies as {name: value}.

    Werkzeug 3 removed ``client.cookie_jar``; ``get_cookie`` is the supported
    replacement, so this reads the cookies the auth flow actually sets.

    Args:
        client: A Flask test client.

    Returns:
        Mapping of cookie name to value, omitting cookies that are not set.
    """
    names = (
        "access_token",
        "refresh_token",
        "csrf_access_token",
        "csrf_refresh_token",
        "session",
    )
    found = {}
    for name in names:
        cookie = client.get_cookie(name)
        if cookie is not None:
            found[name] = cookie.value
    return found


def signup(client, username, email, password):
    """Register via the signup form, setting access and refresh cookies."""
    return client.post(
        "/signup",
        data={"username": username, "email": email, "password": password},
        follow_redirects=True,
    )
