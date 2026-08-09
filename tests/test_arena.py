"""Tests for the Pokemon Arena capture game."""

import pytest

from App.app import app, db
from App.models import User
from App.blueprints.arena import calculate_catch_chance


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


def test_arena_encounter_returns_pokemon(auth_client):
    """Logged-in user should get a random Pokemon."""
    resp = auth_client.get('/arena/encounter')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'id' in data
    assert 'name' in data
    assert 'max_hp' in data
    assert 'current_hp' in data
    assert data['current_hp'] == data['max_hp']


def test_arena_attack_reduces_hp(auth_client):
    """Attack should reduce HP by 10-29."""
    encounter = auth_client.get('/arena/encounter')
    enc_data = encounter.get_json()

    with auth_client.session_transaction() as sess:
        pokemon_id = sess.get('arena_pokemon_id')
        assert pokemon_id is not None

    resp = auth_client.post(f'/arena/attack/{pokemon_id}')
    data = resp.get_json()
    assert 'damage' in data
    assert 10 <= data['damage'] <= 29
    assert 'current_hp' in data
    assert data['current_hp'] == enc_data['max_hp'] - data['damage']


def test_arena_catch_without_pokeballs(auth_client):
    """User with 0 pokeballs should get an error + redirect."""

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        user.pokeballs = 0
        db.session.commit()

    auth_client.get('/arena/encounter')
    with auth_client.session_transaction() as sess:
        pokemon_id = sess.get('arena_pokemon_id')

    resp = auth_client.post(f'/arena/catch/{pokemon_id}')
    assert resp.status_code == 400
    data = resp.get_json()
    assert data.get('redirect') == '/quiz'


def test_arena_run_clears_encounter(auth_client):
    """Running should clear the encounter and return success."""
    auth_client.get('/arena/encounter')

    resp = auth_client.post('/arena/run')
    data = resp.get_json()
    assert data['success'] is True

    with auth_client.session_transaction() as sess:
        assert sess.get('arena_pokemon_id') is None
