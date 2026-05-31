"""Tests for the Pokemon Quiz system."""

import os
import tempfile
import pytest
from sqlalchemy import create_engine

from App.app import app, db
from App.blueprints.auth import initialize_db
from App.models import User


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


def test_quiz_page_requires_auth(client):
    """Unauthenticated users should be redirected."""
    resp = client.get('/quiz')
    assert resp.status_code in (302, 401)


def test_quiz_page_loads(client):
    """Authenticated user should see the quiz page."""
    login(client, 'bob', 'bobpass')
    resp = client.get('/quiz')
    assert resp.status_code == 200
    assert b'Pokemon Quiz' in resp.data


def test_quiz_question_generation(client):
    """Question endpoint should return a question with 4 options."""
    login(client, 'bob', 'bobpass')
    resp = client.get('/api/quiz/question')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'question' in data
    assert 'options' in data
    assert len(data['options']) == 4
    assert data['question_number'] == 1
    assert data['total_questions'] == 10


def test_quiz_submit_correct_answer(client):
    """Submitting the correct answer should award a pokeball."""
    login(client, 'bob', 'bobpass')

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        initial_balls = user.pokeballs

    client.get('/api/quiz/question')
    with client.session_transaction() as sess:
        correct_answer = sess.get('quiz_current_answer')

    resp = client.post('/api/quiz/answer', json={'answer': correct_answer})
    data = resp.get_json()
    assert data['correct'] is True
    assert data['pokeballs'] == initial_balls + 1


def test_quiz_submit_wrong_answer(client):
    """Submitting the wrong answer should NOT award a pokeball."""
    login(client, 'bob', 'bobpass')

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        initial_balls = user.pokeballs

    client.get('/api/quiz/question')
    resp = client.post('/api/quiz/answer', json={'answer': 'DefinitelyWrongAnswer'})
    data = resp.get_json()
    assert data['correct'] is False
    assert data['pokeballs'] == initial_balls


def test_quiz_completion(client):
    """After 10 questions, quiz should complete and reset."""
    login(client, 'bob', 'bobpass')

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        initial_balls = user.pokeballs

    for i in range(10):
        client.get('/api/quiz/question')
        with client.session_transaction() as sess:
            correct_answer = sess.get('quiz_current_answer')
        resp = client.post('/api/quiz/answer', json={'answer': correct_answer})
        data = resp.get_json()
        if i < 9:
            assert data['complete'] is False
        else:
            assert data['complete'] is True

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        assert user.quiz_questions_answered == 0
        assert user.pokeballs == initial_balls + 10
