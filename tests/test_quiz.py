"""Tests for the Pokemon Quiz system."""

from App.app import app
from App.models import User


def test_quiz_page_requires_auth(client):
    """Unauthenticated users should be redirected."""
    resp = client.get('/quiz')
    assert resp.status_code in (302, 401)


def test_quiz_page_loads(auth_client):
    """Authenticated user should see the quiz page."""
    resp = auth_client.get('/quiz')
    assert resp.status_code == 200
    assert b'Pokemon Quiz' in resp.data


def test_quiz_question_generation(auth_client):
    """Question endpoint should return a question with 4 options."""
    resp = auth_client.get('/api/quiz/question')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'question' in data
    assert 'options' in data
    assert len(data['options']) == 4
    assert data['question_number'] == 1
    assert data['total_questions'] == 10


def test_quiz_submit_correct_answer(auth_client):
    """Submitting the correct answer should award a pokeball."""

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        initial_balls = user.pokeballs

    auth_client.get('/api/quiz/question')
    with auth_client.session_transaction() as sess:
        correct_answer = sess.get('quiz_current_answer')

    resp = auth_client.post('/api/quiz/answer', json={'answer': correct_answer})
    data = resp.get_json()
    assert data['correct'] is True
    assert data['pokeballs'] == initial_balls + 1


def test_quiz_submit_wrong_answer(auth_client):
    """Submitting the wrong answer should NOT award a pokeball."""

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        initial_balls = user.pokeballs

    auth_client.get('/api/quiz/question')
    resp = auth_client.post('/api/quiz/answer', json={'answer': 'DefinitelyWrongAnswer'})
    data = resp.get_json()
    assert data['correct'] is False
    assert data['pokeballs'] == initial_balls


def test_quiz_completion(auth_client):
    """After 10 questions, quiz should complete and reset."""

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        initial_balls = user.pokeballs

    for i in range(10):
        auth_client.get('/api/quiz/question')
        with auth_client.session_transaction() as sess:
            correct_answer = sess.get('quiz_current_answer')
        resp = auth_client.post('/api/quiz/answer', json={'answer': correct_answer})
        data = resp.get_json()
        if i < 9:
            assert data['complete'] is False
        else:
            assert data['complete'] is True

    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        assert user.quiz_questions_answered == 0
        assert user.pokeballs == initial_balls + 10
