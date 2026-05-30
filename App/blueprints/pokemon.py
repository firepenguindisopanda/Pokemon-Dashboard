"""Pokemon CRUD blueprint — capture, release, rename, search, and browsing."""

import logging
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_jwt_extended import jwt_required, current_user
from App.models import db, Pokemon, UserPokemon
from App.constants import TYPE_COLORS

logger = logging.getLogger(__name__)

pokemon_bp = Blueprint("pokemon", __name__, template_folder="../templates")


# ── Helper Functions ──


def get_pokemon_list():
    """Return JSON-serialized list of all Pokemon ordered by pokedex number."""
    all_pokemon = [p.get_json() for p in Pokemon.query.order_by(Pokemon.pokedex_number).all()]
    return all_pokemon


def search_pokemon_by_name(query):
    """Return Pokemon matching the given name (case-insensitive partial match)."""
    matching = Pokemon.query.filter(Pokemon.name.ilike(f"%{query}%")).all()
    return [p.get_json() for p in matching]


def filter_pokemon_by_generation(generation):
    """Return Pokemon filtered by generation number."""
    matching = Pokemon.query.filter_by(generation=generation).all()
    return [p.get_json() for p in matching]


# ── Routes ──


@pokemon_bp.route("/search", methods=["GET"])
@jwt_required()
def search_pokemon():
    """Search Pokemon by name and display results."""
    query = request.args.get("query", "")
    if not query:
        return redirect(url_for("pokemon.pokemon_area"))
    results = search_pokemon_by_name(query)
    return render_template("pokemon_area.html", list_of_pokemon=results)


@pokemon_bp.route("/app", methods=["GET"])
@pokemon_bp.route("/app/<int:pokemon_id>", methods=["GET"])
@jwt_required()
def home_page(pokemon_id=1):
    """Main app page with Pokemon list, detail view, and arena."""
    query = request.args.get("query", "")
    if query:
        list_of_pokemon = search_pokemon_by_name(query)
    else:
        list_of_pokemon = get_pokemon_list()

    pokemon_obj = db.session.get(Pokemon, pokemon_id)
    if pokemon_obj is None:
        pokemon_obj = db.session.get(Pokemon, 1)
    pokemon = pokemon_obj.get_json()

    user_pokemons = UserPokemon.query.filter_by(
        user_id=current_user.get_json()["id"]
    ).all()
    user_pokemons_objects = [up.get_json() for up in user_pokemons]

    # Get first captured Pokemon for Arena section
    if user_pokemons:
        first_captured = db.session.get(Pokemon, user_pokemons[0].pokemon_id)
        arena_pokemon = first_captured.get_json() if first_captured else None
    else:
        arena_pokemon = None

    return render_template(
        "home.html",
        list_of_pokemon=list_of_pokemon,
        selected_pokemon_id=pokemon_id,
        pokemon=pokemon,
        usr_pkmons=user_pokemons_objects,
        arena_pokemon=arena_pokemon,
    )


@pokemon_bp.route("/pokemon-area", methods=["GET"])
@jwt_required()
def pokemon_area():
    """Browse all Pokemon with search and generation filter."""
    query = request.args.get("query", "")
    selected_generation = request.args.get("generation", "")
    if selected_generation:
        selected_generation = int(selected_generation)

    if query:
        list_of_pokemon = search_pokemon_by_name(query)
    elif selected_generation:
        list_of_pokemon = filter_pokemon_by_generation(selected_generation)
    else:
        list_of_pokemon = get_pokemon_list()

    all_pokemon = get_pokemon_list()
    unique_generations = sorted(set(p["generation"] for p in all_pokemon))

    return render_template(
        "pokemon_area.html",
        list_of_pokemon=list_of_pokemon,
        unique_generations_list=unique_generations,
        selected_generation=selected_generation,
    )


@pokemon_bp.route("/pokemon-area/pokemon-details/<int:pokemon_id>", methods=["GET", "POST"])
@jwt_required()
def pokemon_area_details(pokemon_id=None):
    """View detailed information about a single Pokemon."""
    pokemon = db.session.get(Pokemon, pokemon_id).get_json()
    return render_template(
        "pokemon_area_details.html",
        current_user=current_user,
        pokemon=pokemon,
    )


@pokemon_bp.route("/pokemon/<int:pokemon_id>", methods=["POST"])
@jwt_required()
def capture_action(pokemon_id):
    """Capture a Pokemon and assign it a nickname."""
    current_user_id = current_user.id
    nickname = request.form.get("nickname")

    if UserPokemon.query.filter_by(
        user_id=current_user_id, pokemon_id=pokemon_id
    ).first():
        flash("You already captured this Pokemon!")
    else:
        current_user.catch_pokemon(pokemon_id, nickname)
        flash("Successfully captured the Pokemon!")

    return redirect(request.referrer)


@pokemon_bp.route("/rename-pokemon/<int:pokemon_id>", methods=["POST"])
@jwt_required()
def rename_action(pokemon_id):
    """Rename a captured Pokemon."""
    form_id = "new_name_" + str(pokemon_id)
    new_name = request.form.get(form_id)
    user_pokemon = db.session.get(UserPokemon, pokemon_id)

    if current_user.rename_pokemon(user_pokemon.id, new_name):
        species = user_pokemon.get_json()["species"]
        flash(f"Your Pokemon {species} has been given a new name successfully!")
    else:
        flash(
            "Error renaming your Pokemon. Please check the name and try again."
        )

    return redirect(request.referrer)


@pokemon_bp.route("/release-pokemon/<int:user_pokemon_id>", methods=["POST"])
@jwt_required()
def release_action(user_pokemon_id):
    """Release a captured Pokemon."""
    user_pokemon = UserPokemon.query.filter_by(
        user_id=current_user.get_json()["id"], id=user_pokemon_id
    ).first()

    if user_pokemon:
        db.session.delete(user_pokemon)
        db.session.commit()
        flash("Successfully released the Pokemon!")
    else:
        flash("Error: Pokemon not found.")

    return redirect(request.referrer)
