import os
import tempfile
import pytest
from flask import url_for
from sqlalchemy import create_engine

from App.app import app, db
from App.blueprints.auth import initialize_db
from App.models import User, UserPokemon, Pokemon


@pytest.fixture(autouse=True)
def _use_sqlite():
    """Override the database to use a temporary SQLite file for all tests.
    
    This replaces the NeonDB/PostgreSQL engine at the Flask-SQLAlchemy
    extension level so tests run locally without any external database.
    """
    db_fd, db_path = tempfile.mkstemp()
    sqlite_uri = f"sqlite:///{db_path}"
    
    # Store original config to restore later
    orig_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    app.config['SQLALCHEMY_DATABASE_URI'] = sqlite_uri
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    # Directly replace the engine at the extension level (needs app context)
    with app.app_context():
        test_engine = create_engine(sqlite_uri)
        if 'sqlalchemy' in app.extensions:
            ext = app.extensions['sqlalchemy']
            # Dispose old engines
            for key in list(ext.engines.keys()):
                ext.engines[key].dispose()
            # Register the new SQLite engine
            ext.engines[None] = test_engine
    
    yield
    
    # Cleanup
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
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    
    with app.app_context():
        db.create_all()
        initialize_db()
    
    yield client

def login(client, username, password):
    return client.post(
        '/login', 
        data={'username': username, 'password': password},
        follow_redirects=True
    )

def signup(client, username, email, password):
    return client.post(
        '/signup',
        data={'username': username, 'email': email, 'password': password},
        follow_redirects=True
    )

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
    cookies = {c.name: c.value for c in client.cookie_jar}
    assert 'access_token' in cookies
    assert 'refresh_token' in cookies, "Signup should set refresh_token cookie"

    # login with that user
    rv2 = login(client, 'newuser', 'pw123')
    assert b'Logged in successfully.' in rv2.data

    # Verify refresh token is set after login too
    cookies2 = {c.name: c.value for c in client.cookie_jar}
    assert 'access_token' in cookies2
    assert 'refresh_token' in cookies2, "Login should set refresh_token cookie"

    # protected route should now be accessible
    rv3 = client.get('/pokemon-area', follow_redirects=True)
    assert rv3.status_code == 200
    assert b'pokemon-area' in rv3.data

def test_capture_release_via_client(client):
    """Full end-to-end through capture and release form submissions."""
    # log in as bob (prepopulated)
    login(client, 'bob', 'bobpass')

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
