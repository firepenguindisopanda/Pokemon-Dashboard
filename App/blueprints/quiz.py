"""Quiz blueprint — Pokemon trivia questions to earn pokeballs."""

import random
import logging
from flask import Blueprint, jsonify, render_template, request, session
from flask_jwt_extended import jwt_required
from App.auth_helpers import current_user_id
from App.models import db, User, Pokemon

logger = logging.getLogger(__name__)
quiz_bp = Blueprint("quiz", __name__, template_folder="../templates")

STAT_NAMES = {
    "hp": "HP", "attack": "Attack", "defense": "Defense",
    "sp_attack": "Sp. Attack", "sp_defense": "Sp. Defense", "speed": "Speed",
}

QUESTION_TYPES = [
    "stat", "type", "generation",
]


def generate_question():
    pokemon = random.choice(Pokemon.query.all())
    qtype = random.choice(QUESTION_TYPES)

    question = None
    correct = None
    wrong = []

    if qtype == "stat":
        stat = random.choice(list(STAT_NAMES.keys()))
        question = f"What is {pokemon.name}'s base {STAT_NAMES[stat]}?"
        correct = str(getattr(pokemon, stat))
        for _ in range(3):
            wrong.append(str(random.randint(1, 255)))

    elif qtype == "type":
        question = f"What is {pokemon.name}'s primary type?"
        correct = pokemon.type1
        all_types = db.session.query(Pokemon.type1).distinct().all()
        all_types = [t[0] for t in all_types if t[0] != pokemon.type1]
        random.shuffle(all_types)
        wrong = all_types[:3]

    elif qtype == "generation":
        question = f"Which generation does {pokemon.name} belong to?"
        correct = str(pokemon.generation)
        all_gens = db.session.query(Pokemon.generation).distinct().all()
        all_gens = [str(g[0]) for g in all_gens if g[0] != pokemon.generation]
        random.shuffle(all_gens)
        wrong = all_gens[:3]

    options = [correct] + wrong
    random.shuffle(options)

    return {
        "question": question,
        "options": options,
        "correct_answer": correct,
        "pokemon_name": pokemon.name,
        "pokemon_sprite": f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pokemon.id}.png",
    }


@quiz_bp.route("/quiz")
@jwt_required()
def quiz_page():
    user_id = current_user_id()
    user = db.session.get(User, user_id)
    return render_template("quiz.html", user=user)


@quiz_bp.route("/api/quiz/question")
@jwt_required()
def get_question():
    user_id = current_user_id()
    user = db.session.get(User, user_id)

    if user.quiz_questions_answered >= 10:
        earned = session.pop("quiz_correct_count", 0)
        user.quiz_questions_answered = 0
        db.session.commit()
        return jsonify({"complete": True, "pokeballs_earned": earned, "pokeballs": user.pokeballs})

    if user.quiz_questions_answered == 0:
        session["quiz_correct_count"] = 0

    q = generate_question()
    session["quiz_current_answer"] = q["correct_answer"]

    return jsonify({
        "question_number": user.quiz_questions_answered + 1,
        "total_questions": 10,
        "question": q["question"],
        "options": q["options"],
        "pokemon_sprite": q["pokemon_sprite"],
        "pokemon_name": q["pokemon_name"],
    })


@quiz_bp.route("/api/quiz/answer", methods=["POST"])
@jwt_required()
def submit_answer():
    user_id = current_user_id()
    user = db.session.get(User, user_id)

    data = request.get_json()
    correct = data.get("answer") == session.get("quiz_current_answer")

    if correct:
        user.pokeballs += 1
        session["quiz_correct_count"] = session.get("quiz_correct_count", 0) + 1

    user.quiz_questions_answered += 1

    if user.quiz_questions_answered >= 10:
        earned = session.pop("quiz_correct_count", 0)
        user.quiz_questions_answered = 0
        db.session.commit()
        return jsonify({
            "correct": correct,
            "complete": True,
            "pokeballs": user.pokeballs,
            "pokeballs_earned": earned,
        })

    db.session.commit()
    return jsonify({
        "correct": correct,
        "complete": False,
        "pokeballs": user.pokeballs,
    })
