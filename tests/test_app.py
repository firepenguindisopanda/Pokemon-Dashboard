from App.app import app, db
from App.models import User, UserPokemon, Pokemon
from tests.helpers import auth_cookies, login, signup



def test_user_model_password_hashing():
    """Ensure set_password hashes and check_password verifies correctly."""
    u = User(username='tester', email='t@example.com', password='plainpw')
    # raw password should not be stored
    assert u.password != 'plainpw'
    assert u.check_password('plainpw') is True
    assert u.check_password('wrong') is False

def test_user_catch_release_rename_model_methods(client):
    """Directly test the UserPokemon model operations."""
    with app.app_context():
        # create a fresh user
        user = User(username='u1', email='u1@u.com', password='pass')
        db.session.add(user)
        db.session.commit()

        # ensure Pokemon with id 1 exists (from initialize_db)
        assert db.session.get(Pokemon, 1) is not None

        # catch
        up = user.catch_pokemon(1, 'MyPoke')
        assert isinstance(up, UserPokemon)
        assert up.name == 'MyPoke'
        assert up.user_id == user.id

        # rename
        assert user.rename_pokemon(up.id, 'NewName') is True
        assert db.session.get(UserPokemon, up.id).name == 'NewName'

        # release
        assert user.release_pokemon(up.id) is True
        assert db.session.get(UserPokemon, up.id) is None

def test_signup_and_login_flow(client):
    """Test that signup creates a user and login issues BOTH cookies."""
    # signup a new user
    rv = signup(client, 'newuser', 'n@u.com', 'pw123')
    assert b'Account created' in rv.data

    # Verify both cookies are set after signup
    cookies = auth_cookies(client)
    assert 'access_token' in cookies
    assert 'refresh_token' in cookies, "Signup should set refresh_token cookie"

    # login with that user
    rv2 = login(client, 'newuser', 'pw123')
    assert b'Logged in successfully.' in rv2.data

    # Verify refresh token is set after login too
    cookies2 = auth_cookies(client)
    assert 'access_token' in cookies2
    assert 'refresh_token' in cookies2, "Login should set refresh_token cookie"

    # protected route should now be accessible
    rv3 = client.get('/pokemon-area', follow_redirects=True)
    assert rv3.status_code == 200
    assert b'pokemon-area' in rv3.data

def test_capture_release_via_client(auth_client):
    """Full end-to-end through capture and release form submissions."""
    client = auth_client

    # capture pokemon id=2 with nickname
    rv = client.post(
        '/pokemon/2',
        data={'nickname': 'TestNick'},
        headers={'Referer': '/'},
        follow_redirects=True
    )
    assert b'Successfully captured the Pok' in rv.data

    # now check bob's list contains our nickname
    rv2 = client.get('/app', follow_redirects=True)
    assert b'TestNick' in rv2.data

    # release the pokemon (bob has 2 seeded Pokémon + model test consumes id=3)
    rv3 = client.post(
        '/release-pokemon/4',
        headers={'Referer': '/'},
        follow_redirects=True
    )
    assert b'Successfully released' in rv3.data

    # verify it's gone
    rv4 = client.get('/app', follow_redirects=True)
    assert b'TestNick' not in rv4.data


class TestRedirectsSurviveAMissingReferer:
    """`redirect(request.referrer)` sends the browser to a page named "None".

    Found on the live deploy: a capture POST returned 404 while the Pokemon was
    actually caught. `request.referrer` is None when no Referer header is sent,
    and Flask's `redirect(None)` happily emits `Location: None` — a relative
    path literally called "None" — so the client fetches /None and gets a 404.
    The write already succeeded, so the user sees a failure page for an action
    that worked, and a retry then reports "You already captured this Pokemon!".

    Every existing test of these routes passes `headers={'Referer': '/'}`, so
    the suite only ever exercised the happy path. Browsers usually do send it —
    but `Referrer-Policy: no-referrer`, privacy extensions and some proxies
    strip it, and none of that is exotic.
    """

    def _catch(self, client, pokemon_id, headers=None):
        csrf = client.get_cookie("csrf_access_token")
        data = {"nickname": "NoRefTest"}
        if csrf:
            data["csrf_token"] = csrf.value
        return client.post(
            f"/pokemon/{pokemon_id}",
            data=data,
            headers=headers or {},
            follow_redirects=False,
        )

    def test_capture_without_a_referer_does_not_redirect_to_none(self, auth_client):
        response = self._catch(auth_client, 1)
        location = response.headers.get("Location")
        assert location not in (None, "None"), (
            f"capture redirected to {location!r} — the browser will request a "
            "page called 'None' and get a 404, even though the catch worked"
        )

    def test_rename_without_a_referer_does_not_redirect_to_none(self, auth_client):
        self._catch(auth_client, 2, headers={"Referer": "/app"})
        from App.models import UserPokemon

        from App.app import app as flask_app

        with flask_app.app_context():
            owned = UserPokemon.query.first()
        csrf = auth_client.get_cookie("csrf_access_token")
        response = auth_client.post(
            f"/rename-pokemon/{owned.id}",
            data={f"new_name_{owned.id}": "Renamed", "csrf_token": csrf.value},
            follow_redirects=False,
        )
        assert response.headers.get("Location") not in (None, "None")

    def test_release_without_a_referer_does_not_redirect_to_none(self, auth_client):
        self._catch(auth_client, 3, headers={"Referer": "/app"})
        from App.models import UserPokemon

        from App.app import app as flask_app

        with flask_app.app_context():
            owned = UserPokemon.query.first()
        csrf = auth_client.get_cookie("csrf_access_token")
        response = auth_client.post(
            f"/release-pokemon/{owned.id}",
            data={"csrf_token": csrf.value},
            follow_redirects=False,
        )
        assert response.headers.get("Location") not in (None, "None")

    def test_a_referer_is_still_honoured(self, auth_client):
        """The fallback must not break the normal case.

        Returning users to the page they acted from is the whole point.
        """
        response = self._catch(auth_client, 4, headers={"Referer": "/pokemon-area"})
        assert "/pokemon-area" in (response.headers.get("Location") or "")
