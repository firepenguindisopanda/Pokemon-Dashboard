"""Tests for the Pokemon Arena capture game."""

import os
import tempfile
import pytest
from flask import url_for
from sqlalchemy import create_engine

from App.app import app, db
from App.blueprints.auth import initialize_db
from App.models import User, Pokemon
from App.blueprints.arena import calculate_catch_chance


@pytest.fixture(autouse=True)
def _use_sqlite():
    db_fd, db_path = tempfile.mkstemp()
    sqlite_uri = f"sqlite:///{db_path}"
    orig_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    app.config['SQLALCHEMY_DATABASE_URI'] = sqlite_uri
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

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


def test_calculate_catch_chance_full_hp_high_rate():
    """Pidgey (capture_rate=255) at full HP -> 10% base chance"""
    chance = calculate_catch_chance(current_hp=40, max_hp=40, capture_rate=255)
    assert chance == pytest.approx(0.10, abs=0.01)


def test_calculate_catch_chance_low_hp_low_rate():
    """Mewtwo (capture_rate=3) at low HP -> very low"""
    chance = calculate_catch_chance(current_hp=10, max_hp=106, capture_rate=3)
    assert chance == pytest.approx(0.021, abs=0.01)


def test_calculate_catch_chance_half_hp():
    """Charmander (capture_rate=45) at half HP"""
    chance = calculate_catch_chance(current_hp=19, max_hp=39, capture_rate=45)
    assert chance == pytest.approx(0.190, abs=0.01)


def test_calculate_catch_chance_zero_hp():
    """0 HP should return 0.0"""
    assert calculate_catch_chance(current_hp=0, max_hp=100, capture_rate=255) == 0.0


def test_arena_encounter_requires_auth(client):
    """Unauthenticated requests should be rejected."""
    resp = client.get('/arena/encounter')
    assert resp.status_code in (302, 401)


def test_arena_encounter_returns_pokemon(client):
    """Logged-in user should get a random Pokemon."""
    login(client, 'bob', 'bobpass')
    resp = client.get('/arena/encounter')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'id' in data
    assert 'name' in data
    assert 'max_hp' in data
    assert 'current_hp' in data
    assert data['current_hp'] == data['max_hp']


def test_arena_attack_reduces_hp(client):
    """Attack should reduce HP by 10-29."""
    login(client, 'bob', 'bobpass')
    encounter = client.get('/arena/encounter')
    enc_data = encounter.get_json()

    with client.session_transaction() as sess:
        pokemon_id = sess.get('arena_pokemon_id')
        assert pokemon_id is not None

    resp = client.post(f'/arena/attack/{pokemon_id}')
    data = resp.get_json()
    assert 'damage' in data
    assert 10 <= data['damage'] <= 29
    assert 'current_hp' in data
    assert data['current_hp'] == enc_data['max_hp'] - data['damage']


def test_arena_catch_without_pokeballs(client):
    """User with 0 pokeballs should get an error + redirect."""
    login(client, 'bob', 'bobpass')

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        user.pokeballs = 0
        db.session.commit()

    client.get('/arena/encounter')
    with client.session_transaction() as sess:
        pokemon_id = sess.get('arena_pokemon_id')

    resp = client.post(f'/arena/catch/{pokemon_id}')
    assert resp.status_code == 400
    data = resp.get_json()
    assert data.get('redirect') == '/quiz'


def test_arena_run_clears_encounter(client):
    """Running should clear the encounter and return success."""
    login(client, 'bob', 'bobpass')
    client.get('/arena/encounter')

    resp = client.post('/arena/run')
    data = resp.get_json()
    assert data['success'] is True

    with client.session_transaction() as sess:
        assert sess.get('arena_pokemon_id') is None
