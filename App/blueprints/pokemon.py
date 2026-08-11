"""Pokemon CRUD blueprint — capture, release, rename, search, and browsing."""

import logging
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_jwt_extended import jwt_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from App.models import db, Pokemon, UserPokemon
from App.type_chart import describe_matchups

logger = logging.getLogger(__name__)

pokemon_bp = Blueprint("pokemon", __name__, template_folder="../templates")


# How many Pokemon one browse page shows. Big enough that paging feels rare,
# small enough that the page stays a page: 48 rows is ~60 KB of HTML against
# the 1,005 KB the unpaginated version shipped.
POKEMON_PER_PAGE = 48


# ── Helper Functions ──


def _int_arg(name):
    """A query-string integer, or None if it is absent or not one.

    `int(request.args.get(...))` raises on `?page=notanumber`, which is a
    500 for a URL a user can type or a crawler can invent.
    """
    raw = request.args.get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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



def _back(default="pokemon.home_page"):
    """Where to send the browser after a form action.

    NOT bare `request.referrer`. It is None whenever no Referer header is sent,
    and `redirect(None)` emits `Location: None` — a relative path literally
    named "None" — so the browser fetches /None and gets a 404 for an action
    that actually succeeded. Found on the live deploy: a capture returned 404
    while the Pokemon was caught, and retrying then said "You already captured
    this Pokemon!".

    Browsers usually do send Referer on a same-origin form POST, which is why
    this survived: every existing test passes `headers={'Referer': '/'}`. But
    `Referrer-Policy: no-referrer`, privacy extensions and some proxies strip
    it, and none of those are unusual.
    """
    return request.referrer or url_for(default)


@pokemon_bp.route("/search", methods=["GET"])
@jwt_required()
def search_pokemon():
    """Legacy search URL — redirects to the browse page.

    This rendered `pokemon_area.html` itself, with only `list_of_pokemon` in
    context: no generation list, no pagination, no current query. So a search
    produced a page missing its own filter controls, and every result was
    rendered at once. One browse surface, one set of context.
    """
    query = request.args.get("query", "").strip()
    return redirect(url_for("pokemon.pokemon_area", query=query or None))


@pokemon_bp.route("/app", methods=["GET"])
@jwt_required()
def home_page():
    """The trainer hub: status, the arena, and your collection.

    Deliberately does NOT list the dex. It used to, and that made it a second,
    worse copy of /pokemon-area — same 801 rows, same search box, but with no
    generation filter. Measured cost of that duplication: 801 `get_json()`
    calls per request, 484 KB of HTML, 4,612 DOM nodes, on a page whose real
    job is answering "what do I do now?".

    Browsing now belongs to /pokemon-area alone, and the one canonical detail
    view is /pokemon-area/pokemon-details/<id>.

    `count()` rather than `len(get_pokemon_list())`: the dex total is one
    number and the database can produce it without hydrating a single row.
    """
    dex_total = db.session.query(func.count(Pokemon.id)).scalar() or 0

    # joinedload, because `UserPokemon.get_json()` reads `self.pokemon.name`
    # for the species. Lazily that is one `SELECT ... WHERE pokemon.id = ?`
    # per caught Pokemon — invisible with two demo catches, one query per row
    # for a real collection.
    user_pokemons = (
        UserPokemon.query.options(joinedload(UserPokemon.pokemon))
        .filter_by(user_id=current_user.id)
        .all()
    )

    return render_template(
        "home.html",
        usr_pkmons=[up.get_json() for up in user_pokemons],
        pokeballs=getattr(current_user, "pokeballs", 0),
        dex_total=dex_total,
    )


@pokemon_bp.route("/app/<int:pokemon_id>", methods=["GET"])
@jwt_required()
def home_pokemon_redirect(pokemon_id):
    """Old per-Pokemon home URL — now one canonical detail view.

    `/app/<id>` rendered a centre panel that overlapped the real details page
    without matching it: different fields, different layout, and no type
    matchups. Two views of one Pokemon is a maintenance trap, so this keeps
    every existing link and bookmark working while pointing at the survivor.
    """
    return redirect(
        url_for("pokemon.pokemon_area_details", pokemon_id=pokemon_id)
    )


@pokemon_bp.route("/pokemon-area", methods=["GET"])
@jwt_required()
def pokemon_area():
    """Browse all Pokemon with search and generation filter."""
    query = request.args.get("query", "").strip()
    selected_generation = _int_arg("generation")
    page = max(1, _int_arg("page") or 1)

    # Filters compose, rather than the old if/elif chain where a search
    # silently discarded the generation filter the user had also set.
    rows = Pokemon.query
    if query:
        rows = rows.filter(Pokemon.name.ilike(f"%{query}%"))
    if selected_generation:
        rows = rows.filter(Pokemon.generation == selected_generation)

    # error_out=False: an out-of-range ?page= is a bad link, not a 404 — it
    # yields an empty page that still renders its navigation.
    pagination = rows.order_by(Pokemon.pokedex_number).paginate(
        page=page, per_page=POKEMON_PER_PAGE, error_out=False
    )

    # DISTINCT in the database. This was `sorted(set(...))` over a SECOND full
    # load of all 801 rows — the page hydrated the entire table twice per
    # request to populate a dropdown with seven numbers.
    unique_generations = [
        row[0]
        for row in db.session.query(Pokemon.generation)
        .distinct()
        .order_by(Pokemon.generation)
        .all()
    ]

    return render_template(
        "pokemon_area.html",
        list_of_pokemon=[p.get_json() for p in pagination.items],
        pagination=pagination,
        unique_generations_list=unique_generations,
        selected_generation=selected_generation,
        query=query,
    )


@pokemon_bp.route("/pokemon-area/pokemon-details/<int:pokemon_id>", methods=["GET", "POST"])
@jwt_required()
def pokemon_area_details(pokemon_id=None):
    """View detailed information about a single Pokemon."""
    pokemon = db.session.get(Pokemon, pokemon_id).get_json()
    # Derived here rather than stored: the `Pokemon` table has no `against_*`
    # columns, so a grid built from the database alone would be uniformly
    # neutral — which is exactly what /pokemon-ml shipped. Computing it from
    # the same type1/type2 the header renders means the two cannot disagree.
    return render_template(
        "pokemon_area_details.html",
        current_user=current_user,
        pokemon=pokemon,
        matchups=describe_matchups(pokemon["type1"], pokemon["type2"]),
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

    return redirect(_back())


@pokemon_bp.route("/rename-pokemon/<int:pokemon_id>", methods=["POST"])
@jwt_required()
def rename_action(pokemon_id):
    """Rename a captured Pokemon."""
    form_id = "new_name_" + str(pokemon_id)
    new_name = request.form.get(form_id)
    user_pokemon = db.session.get(UserPokemon, pokemon_id)

    # A bad id in the URL is a 404, not an AttributeError on `.id` below.
    if user_pokemon is None:
        flash("Error renaming your Pokemon. Please check the name and try again.")
        return redirect(_back())

    if current_user.rename_pokemon(user_pokemon.id, new_name):
        species = user_pokemon.get_json()["species"]
        flash(f"Your Pokemon {species} has been given a new name successfully!")
    else:
        flash(
            "Error renaming your Pokemon. Please check the name and try again."
        )

    return redirect(_back())


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

    return redirect(_back())
