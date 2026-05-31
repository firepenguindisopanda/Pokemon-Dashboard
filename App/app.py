"""Pokemon Dashboard — Flask Application Factory.

Creates and configures the Flask app, initializes extensions,
registers blueprints, and starts background analytics training.
"""

import datetime
import logging
import concurrent.futures
from collections import namedtuple
from flask import Flask, current_app, g, redirect, request, flash
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, current_user,
    create_access_token,
    set_access_cookies,
    unset_jwt_cookies,
    unset_refresh_cookies,
)
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError, TimeoutError
from App.models import db, User
from App.config import get_settings
from App.blueprints.auth import auth_bp, initialize_db
from App.blueprints.pokemon import pokemon_bp
from App.blueprints.analytics import analytics_bp, background_init_analytics, initialize_pokemon_analytics
from App.blueprints.arena import arena_bp
from App.blueprints.quiz import quiz_bp

MinimalUser = namedtuple("MinimalUser", ["id", "username", "email"])

logger = logging.getLogger(__name__)


# ── Module-level JWT Callbacks (importable for tests) ──


def add_claims_to_access_token(user):
    """Embed username and email as JWT claims for DB-resilient fallback."""
    return {"username": user.username, "email": user.email}


def user_identity_lookup(user):
    return user.id


def user_lookup_callback(_jwt_header, jwt_data):
    """Resilient user loader with DB-failure fallback to token claims.

    If the DB is unreachable (NeonDB connection drop), falls back to
    creating a MinimalUser from the token claims so the request can
    still resolve current_user without crashing.
    """
    identity = jwt_data["sub"]

    # Check request-scoped cache first
    if hasattr(g, "cached_user"):
        return g.cached_user

    try:
        user = db.session.get(User, identity)
        if user:
            g.cached_user = user
            return user
    except (OperationalError, TimeoutError) as exc:
        current_app.logger.warning(
            "DB lookup failed for user %s, using token claims: %s",
            identity, exc,
        )

    # Fallback: reconstruct a minimal user from JWT claims
    claims = jwt_data.get("claims", {})
    fallback = MinimalUser(
        id=identity,
        username=claims.get("username", "unknown"),
        email=claims.get("email", "unknown"),
    )
    g.cached_user = fallback
    return fallback


def create_app():
    """Create and configure the Flask application."""
    settings = get_settings()

    app = Flask(__name__)

    # ── Flask Config ──
    app.config["SECRET_KEY"] = settings.flask_secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = settings.sqlalchemy_database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = settings.sqlalchemy_track_modifications
    app.config["DEBUG"] = settings.debug

    # ── JWT Config ──
    app.config["JWT_SECRET_KEY"] = settings.jwt_secret_key
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = settings.jwt_access_expires_timedelta
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = settings.jwt_refresh_expires_timedelta
    app.config["JWT_TOKEN_LEEWAY"] = settings.jwt_token_leeway_seconds
    app.config["JWT_TOKEN_LOCATION"] = settings.jwt_token_location
    app.config["JWT_HEADER_NAME"] = settings.jwt_header_name
    app.config["JWT_ACCESS_COOKIE_NAME"] = settings.jwt_access_cookie_name
    app.config["JWT_REFRESH_COOKIE_NAME"] = settings.jwt_refresh_cookie_name
    app.config["JWT_COOKIE_SECURE"] = settings.jwt_cookie_secure
    app.config["JWT_COOKIE_CSRF_PROTECT"] = settings.jwt_cookie_csrf_protect

    # ── Initialize Extensions ──
    db.init_app(app)
    CORS(app, origins=settings.cors_origins)

    # ── Auto-initialize empty database ──
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.get_table_names():
            logger.info("Database is empty — creating tables and seeding...")
            initialize_db()
            logger.info("Database initialized and seeded with Pokemon data.")
        else:
            logger.info(
                "Database already initialized (%d tables).",
                len(inspector.get_table_names()),
            )

    jwt = JWTManager(app)

    # ── Register JWT Callbacks ──
    jwt.additional_claims_loader(add_claims_to_access_token)
    jwt.user_identity_loader(user_identity_lookup)
    jwt.user_lookup_loader(user_lookup_callback)

    # ── Token Error Handlers ──

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_data):
        """Handle expired access tokens by attempting silent refresh.

        For server-rendered pages: checks the refresh_token cookie, if valid,
        creates a new access token and redirects to the same URL.
        If refresh token is also expired, redirects to login.
        """
        refresh_cookie_name = current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token")
        refresh_token_str = request.cookies.get(refresh_cookie_name)

        if not refresh_token_str:
            response = redirect("/")
            unset_jwt_cookies(response)
            unset_refresh_cookies(response)
            flash("Session expired. Please log in again.")
            return response

        try:
            from flask_jwt_extended import decode_token
            refresh_data = decode_token(refresh_token_str)
            identity = refresh_data["sub"]

            user = db.session.get(User, identity)
            if user is None:
                response = redirect("/")
                unset_jwt_cookies(response)
                unset_refresh_cookies(response)
                flash("Account not found. Please log in again.")
                return response

            new_access_token = create_access_token(identity=user)
            response = redirect(request.url or "/app")
            set_access_cookies(response, new_access_token)
            return response

        except Exception as exc:
            current_app.logger.warning("Token refresh failed: %s", exc)
            response = redirect("/")
            unset_jwt_cookies(response)
            unset_refresh_cookies(response)
            flash("Session expired. Please log in again.")
            return response

    @jwt.invalid_token_loader
    def invalid_token_callback(error_string):
        """Handle invalid (malformed/signed) tokens — return our JSON format."""
        return {
            "status": "error",
            "code": "INVALID_TOKEN",
            "message": "Invalid token. Please log in again.",
            "redirect": "/",
        }, 401

    @jwt.unauthorized_loader
    def unauthorized_callback(error_string):
        """Handle missing tokens — return our JSON format."""
        return {
            "status": "error",
            "code": "NO_TOKEN",
            "message": "No active session. Please log in.",
            "redirect": "/",
        }, 401

    @app.context_processor
    def inject_context():
        try:
            return dict(current_user=current_user, now=datetime.datetime.now)
        except Exception:
            return dict(current_user=None, now=datetime.datetime.now)

    # ── Register Blueprints ──
    app.register_blueprint(auth_bp)
    app.register_blueprint(pokemon_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(arena_bp)
    app.register_blueprint(quiz_bp)

    logger.info(f"App configured with DB: {settings.sqlalchemy_database_uri[:50]}...")
    logger.info(f"Debug mode: {settings.debug}")

    return app


# ── Global app instance ──

app = create_app()

# ── Start background analytics initialization ──
with app.app_context():
    background_thread = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    background_thread.submit(background_init_analytics, app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
