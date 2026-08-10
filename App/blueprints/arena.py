"""Arena blueprint — wild Pokemon encounters, attacks, and capture."""

import random
import logging
from flask import Blueprint, jsonify, session
from flask_jwt_extended import jwt_required
from App.auth_helpers import current_user_id
from App.models import db, User, Pokemon, UserPokemon

logger = logging.getLogger(__name__)
arena_bp = Blueprint("arena", __name__)


def calculate_catch_chance(current_hp, max_hp, capture_rate):
    if current_hp <= 0:
        return 0.0
    hp_factor = 0.05 + 0.95 * (1.0 - (current_hp / max_hp))
    raw_chance = hp_factor * (capture_rate / 255.0)
    return min(raw_chance * 2.0, 0.90)


@arena_bp.route("/arena/encounter")
@jwt_required()
def encounter():
    user_id = current_user_id()
    user = db.session.get(User, user_id)

    owned_ids = [up.pokemon_id for up in user.pokemon] if user.pokemon else []

    if owned_ids:
        candidates = Pokemon.query.filter(~Pokemon.id.in_(owned_ids)).all()
    else:
        candidates = Pokemon.query.all()

    if not candidates:
        return jsonify({"error": "You have caught all Pokemon!"}), 400

    pokemon = random.choice(candidates)

    session["arena_pokemon_id"] = pokemon.id
    session["arena_hp"] = pokemon.hp

    return jsonify({
        "id": pokemon.id,
        "name": pokemon.name,
        "pokedex_number": pokemon.pokedex_number,
        "type1": pokemon.type1,
        "type2": pokemon.type2,
        "max_hp": pokemon.hp,
        "current_hp": pokemon.hp,
        "sprite_url": f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pokemon.id}.png",
        "pokeballs": user.pokeballs,
    })


@arena_bp.route("/arena/current")
@jwt_required()
def current_encounter():
    user_id = current_user_id()
    user = db.session.get(User, user_id)

    pokemon_id = session.get("arena_pokemon_id")
    current_hp = session.get("arena_hp")

    if not pokemon_id:
        return jsonify({"encounter": False})

    pokemon = db.session.get(Pokemon, pokemon_id)
    if not pokemon:
        return jsonify({"encounter": False})

    return jsonify({
        "encounter": True,
        "id": pokemon.id,
        "name": pokemon.name,
        "pokedex_number": pokemon.pokedex_number,
        "type1": pokemon.type1,
        "type2": pokemon.type2,
        "max_hp": pokemon.hp,
        "current_hp": current_hp,
        "sprite_url": f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pokemon.id}.png",
        "pokeballs": user.pokeballs,
    })


@arena_bp.route("/arena/attack/<int:pokemon_id>", methods=["POST"])
@jwt_required()
def attack(pokemon_id):
    stored_id = session.get("arena_pokemon_id")
    current_hp = session.get("arena_hp")

    if stored_id != pokemon_id or current_hp is None:
        return jsonify({"error": "No active encounter for this Pokemon"}), 400

    damage = random.randint(10, 29)
    current_hp = max(0, current_hp - damage)
    session["arena_hp"] = current_hp

    pokemon = db.session.get(Pokemon, pokemon_id)

    return jsonify({
        "damage": damage,
        "current_hp": current_hp,
        "max_hp": pokemon.hp if pokemon else 0,
        "fainted": current_hp == 0,
    })


@arena_bp.route("/arena/catch/<int:pokemon_id>", methods=["POST"])
@jwt_required()
def catch(pokemon_id):
    user_id = current_user_id()
    user = db.session.get(User, user_id)

    stored_id = session.get("arena_pokemon_id")
    current_hp = session.get("arena_hp")

    if stored_id != pokemon_id or current_hp is None:
        return jsonify({"error": "No active encounter for this Pokemon"}), 400

    if current_hp == 0:
        return jsonify({"error": "This Pokemon has fainted! Find another."}), 400

    if user.pokeballs <= 0:
        return jsonify({"error": "Out of pokeballs!", "redirect": "/quiz"}), 400

    pokemon = db.session.get(Pokemon, pokemon_id)
    if not pokemon:
        return jsonify({"error": "Pokemon not found"}), 404

    already_owned = UserPokemon.query.filter_by(
        user_id=user.id, pokemon_id=pokemon_id
    ).first()
    if already_owned:
        return jsonify({"error": "You already caught this Pokemon!"}), 400

    chance = calculate_catch_chance(current_hp, pokemon.hp, pokemon.capture_rate)
    user.pokeballs -= 1

    caught = random.random() < chance

    user_poke = None
    if caught:
        user_poke = user.catch_pokemon(pokemon.id, pokemon.name)
        session.pop("arena_pokemon_id", None)
        session.pop("arena_hp", None)

    db.session.commit()

    return jsonify({
        "caught": caught,
        "pokeballs_remaining": user.pokeballs,
        "pokemon_name": pokemon.name,
        "chance": round(chance, 4),
        "user_pokemon_id": user_poke.id if user_poke else None,
        "pokemon_id": pokemon.id,
        "species": pokemon.name,
    })


@arena_bp.route("/arena/run", methods=["POST"])
@jwt_required()
def run():
    session.pop("arena_pokemon_id", None)
    session.pop("arena_hp", None)
    return jsonify({"success": True, "message": "You fled from the wild Pokemon!"})
