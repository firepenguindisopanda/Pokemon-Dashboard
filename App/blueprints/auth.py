"""Authentication blueprint — login, signup, logout, and app initialization."""

import ast
import csv
import logging
from flask import Blueprint, request, redirect, render_template, url_for, flash, jsonify
from sqlalchemy.exc import IntegrityError
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    current_user,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
    unset_refresh_cookies,
)
from App.config import get_settings
from App.extensions import limiter
from App.models import db, User, Pokemon, UserPokemon, Message

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, template_folder="../templates")


# ── Initialization ──


def _parse_abilities(raw, pokemon_name, line_number):
    """Parse the abilities column, which stores a Python list literal.

    Uses ast.literal_eval rather than eval: the CSV is data, and a crafted
    cell must never be able to execute code during seeding.

    Args:
        raw: Raw cell value, e.g. "['Overgrow', 'Chlorophyll']".
        pokemon_name: Name from the same row, used in error messages.
        line_number: Line in the CSV, used in error messages.

    Returns:
        List of ability name strings.

    Raises:
        ValueError: If the cell is not a well-formed list of strings.
    """
    try:
        abilities = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(
            f"Invalid 'abilities' value on line {line_number} "
            f"(pokemon={pokemon_name!r}): {raw!r}"
        ) from exc

    if not isinstance(abilities, list) or not all(isinstance(a, str) for a in abilities):
        raise ValueError(
            f"Invalid 'abilities' value on line {line_number} "
            f"(pokemon={pokemon_name!r}): expected a list of strings, got {abilities!r}"
        )

    return abilities


def clear_seed_data():
    """Delete every row this seeder owns, in foreign-key-safe order.

    Rows only — the schema belongs to `flask db upgrade`. Without this,
    re-running the seeder would violate the unique constraint on username.
    """
    for model in (UserPokemon, Message, User, Pokemon):
        db.session.query(model).delete()
    db.session.commit()


def initialize_db(csv_path="pokemon.csv"):
    """Seed the database with Pokemon data and default users.

    Replaces any existing rows. This does **not** create or drop tables —
    run `flask db upgrade` first to build the schema.

    Args:
        csv_path: Path to the Pokemon seed CSV. Overridable for tests.
    """
    clear_seed_data()
    with open(csv_path, newline="", encoding="utf8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row["height_m"] == "":
                row["height_m"] = None
            if row["weight_kg"] == "":
                row["weight_kg"] = None
            if row["type2"] == "":
                row["type2"] = None

            abilities_list = _parse_abilities(
                row["abilities"], row.get("name"), reader.line_num
            )

            # Handle complex capture_rate values like "30 (Meteorite)255 (Core)"
            capture_rate_str = row["capture_rate"].strip()
            if "(" in capture_rate_str:
                capture_rate = int(capture_rate_str.split("(")[0].strip())
            else:
                capture_rate = int(capture_rate_str)

            pokemon = Pokemon(
                name=row["name"],
                pokedex_number=row["pokedex_number"],
                attack=row["attack"],
                defense=row["defense"],
                sp_attack=row["sp_attack"],
                sp_defense=row["sp_defense"],
                weight=row["weight_kg"],
                height=row["height_m"],
                hp=row["hp"],
                speed=row["speed"],
                type1=row["type1"],
                type2=row["type2"],
                generation=row["generation"],
                classification=row["classification"],
                abilities=",".join(abilities_list),
                capture_rate=capture_rate,
                is_legendary=int(row["is_legendary"]),
                percentage_male=float(row["percentage_male"])
                if row["percentage_male"]
                else 50.0,
                base_total=int(row["base_total"]),
                base_egg_steps=int(row["base_egg_steps"])
                if row["base_egg_steps"].strip()
                else 0,
                base_happiness=int(row["base_happiness"])
                if row["base_happiness"].strip()
                else 0,
                experience_growth=int(row["experience_growth"])
                if row["experience_growth"].strip()
                else 0,
            )
            db.session.add(pokemon)

        bob = User(username="bob", email="bob@mail.com", password="bobpass")
        bob.pokeballs = 10
        nick = User(username="nick", email="nick@mail.com", password="nickpass")
        nick.pokeballs = 10
        db.session.add(bob)
        db.session.add(nick)
        db.session.commit()
        bob.catch_pokemon(1, "Benny")
        bob.catch_pokemon(25, "Saul")
        nick.catch_pokemon(120, "Buddy")


def login_user(username, password):
    """Authenticate a user and return an access token."""
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        return create_access_token(identity=user)
    return None


# ── Routes ──


# NOTE: There is deliberately no HTTP route for initialize_db().
# It calls db.drop_all(), and it was previously exposed at GET /init with no
# authentication — any visitor, crawler, or browser prefetch could destroy the
# entire database. Seeding is an operator action: use the `flask init` CLI
# command (registered in wsgi.py). Do not add a route back here.


@auth_bp.route("/", methods=["GET"])
def login_page():
    """Render the login page."""
    return render_template("login.html")


@auth_bp.route("/signup", methods=["GET"])
def signup_page():
    """Render the signup page."""
    return render_template("signup.html")


@auth_bp.route("/signup", methods=["POST"])
@limiter.limit(lambda: get_settings().rate_limit_auth)
def signup_action():
    """Create a new user account and log them in with both tokens."""
    username = request.form["username"]
    email = request.form["email"]
    password = request.form["password"]

    # Check first so the user gets a message naming the actual conflict.
    if User.query.filter_by(username=username).first():
        flash("Username already exists")
        return redirect(url_for("auth.signup_page"))
    if User.query.filter_by(email=email).first():
        flash("Email already registered")
        return redirect(url_for("auth.signup_page"))

    user = User(username=username, email=email, password=password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # Backstop for the race between the checks above and this commit.
        # Without the rollback the session stays poisoned and every later
        # write in this request fails too.
        db.session.rollback()
        logger.info("Signup conflict for username=%r email=%r", username, email)
        flash("Username already exists")
        return redirect(url_for("auth.signup_page"))

    response = redirect(url_for("pokemon.home_page"))
    set_access_cookies(response, create_access_token(identity=user))
    set_refresh_cookies(response, create_refresh_token(identity=user))
    flash("Account created")
    return response


@auth_bp.route("/login", methods=["POST"])
@limiter.limit(lambda: get_settings().rate_limit_auth)
def login_action():
    """Authenticate user, issue access + refresh tokens, redirect to main app."""
    data = request.form
    username = data["username"]
    password = data["password"]
    response = None

    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        flash("Logged in successfully.")
        response = redirect(url_for("pokemon.home_page"))
        access_token = create_access_token(identity=user)
        refresh_token = create_refresh_token(identity=user)
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
    else:
        flash("Invalid username or password")
        response = redirect(url_for("auth.login_page"))

    return response


@auth_bp.route("/logout", methods=["GET"])
@jwt_required()
def logout_action():
    """Log out the current user and clear ALL JWT cookies."""
    response = redirect(url_for("auth.login_page"))
    unset_jwt_cookies(response)
    unset_refresh_cookies(response)
    flash("Logged out")
    return response


@auth_bp.route("/api/auth/refresh", methods=["POST"])
@limiter.limit(lambda: get_settings().rate_limit_auth)
@jwt_required(refresh=True)
def refresh_token():
    """Issue a new access token using the refresh token cookie.

    Used by the frontend apiFetch interceptor on 401 responses.
    Returns the new access token as both JSON and Set-Cookie header.
    """
    identity = current_user
    if identity is None:
        return jsonify({
            "status": "error",
            "code": "REFRESH_TOKEN_EXPIRED",
            "message": "Session expired. Please log in again.",
            "redirect": "/",
        }), 401

    new_access_token = create_access_token(identity=identity)
    response = jsonify({
        "status": "success",
        "message": "Access token refreshed",
    })
    set_access_cookies(response, new_access_token)
    return response
