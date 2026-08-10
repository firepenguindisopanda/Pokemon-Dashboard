"""Analytics blueprint — ML API endpoints and analytics dashboard pages."""

import logging
import threading
from functools import wraps
from flask import Blueprint, request, render_template, jsonify
from flask_jwt_extended import jwt_required
from App.models import db, Pokemon
from App.lib import PokemonAnalytics, PokemonAnalyticsError
from App.config import get_settings
from App.constants import TYPE_COLORS
import pandas as pd

logger = logging.getLogger(__name__)

analytics_bp = Blueprint("analytics", __name__, template_folder="../templates")


def internal_error(context, status=500):
    """Log the active exception and return a message safe to show a client.

    Raw exception text has leaked SQL fragments, file paths and stack context
    to whoever triggered the error. The detail belongs in the log.

    Args:
        context: Short description of what failed, used in the log and as the
            client-facing message.
        status: HTTP status to return.

    Returns:
        A (response, status) tuple.
    """
    logger.exception(context)
    return jsonify({"error": context}), status


# ── Analytics state ──


class AnalyticsState:
    """Holds the trained analytics instance and its readiness.

    Previously three module globals mutated from several call sites with
    `global` statements, including from a background thread — readers could
    observe `ready` before the instance was actually assigned. Keeping them
    together behind one lock makes the transition atomic.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.instance = None
        self.ready = False
        self.error = None
        # Training metrics captured once, so serving them never retrains.
        self.metrics = {}

    @property
    def lock(self):
        """The mutex guarding initialization."""
        return self._lock

    def mark_ready(self, instance, metrics=None):
        """Publish a trained instance. Assign before flipping the flag.

        Args:
            instance: The trained PokemonAnalytics object.
            metrics: Training results, served to clients without retraining.
        """
        self.instance = instance
        self.metrics = metrics or {}
        self.error = None
        self.ready = True

    def mark_failed(self, message):
        """Record a client-safe failure message."""
        self.ready = False
        self.error = message

    def reset(self):
        """Return to the untrained state (used by tests)."""
        self.instance = None
        self.metrics = {}
        self.ready = False
        self.error = None

    def as_status(self):
        """The payload shape the frontend polls. Do not change casually."""
        return {
            "ready": self.ready,
            "error": self.error,
            "initializing": self.instance is not None and not self.ready,
        }


state = AnalyticsState()


def initialize_pokemon_analytics():
    """Build and publish the analytics instance (blocking).

    Returns:
        True when analytics are ready to serve.
    """
    try:
        all_pokemon = Pokemon.query.all()

        pokemon_data = []
        for pkmn in all_pokemon:
            abilities_count = (
                len(pkmn.abilities.split(",")) if pkmn.abilities else 1
            )
            pokemon_data.append(
                {
                    "name": pkmn.name,
                    "pokedex_number": pkmn.pokedex_number,
                    "hp": pkmn.hp,
                    "attack": pkmn.attack,
                    "defense": pkmn.defense,
                    "sp_attack": pkmn.sp_attack,
                    "sp_defense": pkmn.sp_defense,
                    "speed": pkmn.speed,
                    "base_total": pkmn.base_total,
                    "type1": pkmn.type1,
                    "type2": pkmn.type2 if pkmn.type2 else "None",
                    "generation": pkmn.generation,
                    "height_m": pkmn.height if pkmn.height else 1.0,
                    "weight_kg": pkmn.weight if pkmn.weight else 10.0,
                    "classification": pkmn.classification,
                    "abilities": pkmn.abilities,
                    "capture_rate": pkmn.capture_rate,
                    "is_legendary": pkmn.is_legendary,
                    "percentage_male": pkmn.percentage_male,
                    "base_egg_steps": pkmn.base_egg_steps or 0,
                    "base_happiness": pkmn.base_happiness or 0,
                    "experience_growth": pkmn.experience_growth or 0,
                    "num_abilities": abilities_count,
                }
            )

        df = pd.DataFrame(pokemon_data)

        analytics = PokemonAnalytics()
        analytics.load_data(data=df)
        analytics.clean_data()
        metrics = analytics.train_predictive_models()
        # Publish only once fully built, so no request can observe a
        # half-initialised instance. Metrics are captured here so serving
        # them later never triggers another training run.
        state.mark_ready(analytics, metrics)
        logger.info("Analytics initialized successfully")
        return True
    except Exception as exc:
        # This value is surfaced to clients via /api/pokemon-analytics/status,
        # so it must stay generic — the underlying error has included raw SQL
        # such as "no such table: pokemon". Detail goes to the log only.
        state.mark_failed("Analytics are currently unavailable.")
        # Warning level because this can happen on first startup before the DB
        # is initialized. ensure_analytics() will retry when needed.
        logger.warning("Analytics initialization deferred: %s", exc)
        return False


def ensure_analytics():
    """Make analytics available, training once if necessary.

    Guarded by a lock: without it, concurrent requests each start their own
    training run, and several gunicorn threads would race on the module
    globals. Whoever wins does the work; the rest wait and observe the result.

    Set ANALYTICS_AUTO_INITIALIZE=false to require `flask train` instead, so a
    request can never pay for a cold model cache.

    Returns:
        True when analytics are ready to serve.
    """
    if state.ready:
        return True

    if not get_settings().analytics_auto_initialize:
        logger.warning(
            "Analytics are not initialized and auto-initialization is disabled. "
            "Run `flask train`."
        )
        return False

    with state.lock:
        # Another thread may have finished while this one waited.
        if state.ready:
            return True
        logger.info("Analytics requested but not ready — training now.")
        return initialize_pokemon_analytics()


def with_analytics(f):
    """Decorator: ensures analytics are available before calling the route."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ensure_analytics():
            if state.error:
                return jsonify({"error": state.error}), 500
            return jsonify(
                {"error": "Analytics still initializing, try again in a moment"}
            ), 503
        return f(*args, **kwargs)

    return wrapper


# ── Helper ──


def get_combined_type_distribution():
    """Count type occurrences across both type1 and type2 columns.

    A `GROUP BY` over the two type columns unioned together, rather than
    hydrating all 801 rows to do the counting in Python.

    Two details are load-bearing:

    * The old code tested `if pkmn.type2:`, which skipped an empty string as
      well as NULL. `IS NOT NULL` alone would start counting "" as a type.
    * The caller builds its chart labels from `.keys()`, so key order is part
      of the output. The old dict came out in order of first appearance while
      scanning by id, type1 before type2 — reproduced here by ordering on the
      smallest `ordinal`, which interleaves the two columns exactly that way.
    """
    primary = db.session.query(
        Pokemon.type1.label("type_name"),
        (Pokemon.id * 2).label("ordinal"),
    )
    secondary = db.session.query(
        Pokemon.type2.label("type_name"),
        (Pokemon.id * 2 + 1).label("ordinal"),
    ).filter(Pokemon.type2.isnot(None), Pokemon.type2 != "")

    both = primary.union_all(secondary).subquery()
    rows = (
        db.session.query(both.c.type_name, db.func.count().label("count"))
        .group_by(both.c.type_name)
        .order_by(db.func.min(both.c.ordinal))
        .all()
    )
    return {row.type_name: row.count for row in rows}


# ── Analytics API Routes ──


@analytics_bp.route("/api/pokemon-analytics/stats", methods=["GET"])
@jwt_required()
@with_analytics
def get_pokemon_descriptive_stats():
    """Get comprehensive descriptive statistics."""
    try:
        stats = state.instance.get_descriptive_stats()
        return jsonify(stats)
    except Exception:
        return internal_error("Request failed. Please try again.")


@analytics_bp.route("/api/pokemon-analytics/diagnostics", methods=["GET"])
@jwt_required()
@with_analytics
def get_pokemon_diagnostics():
    """Get diagnostic analysis and correlations."""
    try:
        diagnostics = state.instance.diagnostic_analysis()
        return jsonify(diagnostics)
    except Exception:
        return internal_error("Request failed. Please try again.")


@analytics_bp.route("/api/pokemon-analytics/clustering", methods=["GET"])
@jwt_required()
@with_analytics
def get_pokemon_clustering():
    """Perform clustering analysis (KMeans) with optional PCA/t-SNE viz."""
    try:
        n_clusters = request.args.get("clusters", 5, type=int)
        viz_method = request.args.get("viz", None)
        if viz_method in ("pca", "tsne"):
            clustering_results = state.instance.perform_clustering_with_viz(
                n_clusters, viz_method
            )
        else:
            clustering_results = state.instance.perform_clustering(n_clusters)
        return jsonify(clustering_results)
    except Exception:
        return internal_error("Request failed. Please try again.")


@analytics_bp.route("/api/pokemon-analytics/similar", methods=["POST"])
@jwt_required()
@with_analytics
def find_similar_pokemon():
    """Find Pokemon similar to a given one by stat profile."""
    try:
        data = request.json
        if not data or "name" not in data:
            return jsonify({"error": "Missing 'name' in request body"}), 400

        n_similar = data.get("n_similar", 5)
        results = state.instance.find_similar_pokemon(
            pokemon_name=data["name"],
            n_similar=n_similar,
        )
        return jsonify(
            {
                "query": data["name"],
                "n_similar": n_similar,
                "results": results,
            }
        )
    except PokemonAnalyticsError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        return internal_error("Similarity search failed.")


@analytics_bp.route("/api/pokemon-analytics/reverse-search", methods=["POST"])
@jwt_required()
@with_analytics
def find_closest_pokemon():
    """Find the closest real Pokemon matching a stat profile."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        stats = {
            "hp": data.get("hp", 85),
            "attack": data.get("attack", 85),
            "defense": data.get("defense", 85),
            "sp_attack": data.get("sp_attack", 85),
            "sp_defense": data.get("sp_defense", 85),
            "speed": data.get("speed", 85),
        }
        type1 = data.get("type1", "normal")
        type2 = data.get("type2")
        n_results = data.get("n_results", 5)

        results = state.instance.find_closest_pokemon(
            stats=stats, type1=type1, type2=type2, n_results=n_results
        )
        return jsonify(
            {
                "query_stats": stats,
                "type1": type1,
                "type2": type2,
                "results": results,
            }
        )
    except PokemonAnalyticsError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        return internal_error("Reverse search failed.")


@analytics_bp.route("/api/pokemon-analytics/predict", methods=["POST"])
@jwt_required()
@with_analytics
def predict_pokemon_performance():
    """Predict Pokemon stats and legendary status."""
    try:
        pokemon_data = request.json

        required_fields = {
            "hp": 50,
            "attack": 50,
            "defense": 50,
            "sp_attack": 50,
            "sp_defense": 50,
            "speed": 50,
            "type1": "normal",
            "type2": "None",
            "generation": 1,
            "height_m": 1.0,
            "weight_kg": 10.0,
            "capture_rate": 45,
            "num_abilities": 1,
        }

        for field, default in required_fields.items():
            if field not in pokemon_data:
                pokemon_data[field] = default

        pokemon_data["base_total"] = (
            pokemon_data["hp"]
            + pokemon_data["attack"]
            + pokemon_data["defense"]
            + pokemon_data["sp_attack"]
            + pokemon_data["sp_defense"]
            + pokemon_data["speed"]
        )

        predictions = state.instance.predict_pokemon_stats(pokemon_data)
        return jsonify(predictions)
    except Exception:
        return internal_error("Request failed. Please try again.")


@analytics_bp.route("/api/pokemon-analytics/optimize", methods=["GET"])
@jwt_required()
@with_analytics
def optimize_pokemon_build():
    """Get optimal Pokemon build recommendations."""
    try:
        optimal_build = state.instance.optimize_pokemon_build()
        return jsonify(optimal_build)
    except Exception:
        return internal_error("Request failed. Please try again.")


@analytics_bp.route("/api/pokemon-analytics/team-recommend", methods=["POST"])
@jwt_required()
@with_analytics
def recommend_pokemon_team():
    """Get team recommendations based on user preferences."""
    try:
        preferences = request.json if request.json else {}
        if not isinstance(preferences, dict):
            return jsonify({"error": "Invalid preferences format"}), 400
        team = state.instance.recommend_team(preferences)
        return jsonify(team)
    except ValueError:
        return jsonify({"error": "Invalid team preferences."}), 400
    except Exception:
        return internal_error("Failed to generate a team recommendation.")


@analytics_bp.route("/api/pokemon-analytics/model-performance", methods=["GET"])
@jwt_required()
@with_analytics
def get_model_performance():
    """Return the metrics recorded when the models were trained.

    Deliberately never retrains. This endpoint used to accept `?retrain=true`,
    which let any logged-in user start a full training run — and with a single
    synchronous gunicorn worker that blocks the whole application. Retraining
    is an operator action: `flask train --force`.
    """
    return jsonify(state.metrics)


@analytics_bp.route("/api/pokemon-analytics/model-comparison", methods=["GET"])
@jwt_required()
@with_analytics
def get_model_comparison():
    """Compare the trained ensemble models, from recorded metrics only."""
    return jsonify(
        {
            "ensemble_comparison": state.metrics.get("ensemble_comparison", {}),
            "cached": state.metrics.get("cached", False),
        }
    )


@analytics_bp.route("/api/pokemon-analytics/type-coverage", methods=["POST"])
@jwt_required()
@with_analytics
def get_type_coverage():
    """Get type defense coverage for a team."""
    try:
        data = request.json
        if not data or "team" not in data:
            return jsonify({"error": "Missing 'team' list in request body"}), 400
        result = state.instance.calculate_team_type_coverage(data["team"])
        return jsonify(result)
    except PokemonAnalyticsError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        return internal_error("Type coverage calculation failed.")


# ── Dashboard Page Routes ──


@analytics_bp.route("/pokemon-stats", methods=["GET"])
@jwt_required()
def pokemon_analytics_dashboard():
    """Render the analytics dashboard page.

    NEVER blocks — page renders instantly. The frontend polls
    /api/pokemon-analytics/status and loads sections asynchronously
    once analytics are ready.
    """
    return render_template("pokemon_dashboard.html", type_colors=TYPE_COLORS)


@analytics_bp.route("/pokemon-ml", methods=["GET"])
@jwt_required()
def pokemon_ml_playground():
    """Render the ML playground page.

    NEVER blocks — page renders instantly. Widgets load asynchronously
    from /api/pokemon-analytics/* endpoints with per-widget loading states.
    """
    return render_template("pokemon_ml.html", type_colors=TYPE_COLORS)


@analytics_bp.route("/api/pokemon-analytics/status", methods=["GET"])
@jwt_required()
def analytics_status():
    """Return analytics initialization status for frontend polling."""
    return jsonify(state.as_status())


@analytics_bp.route("/pokemon-stats-v1", methods=["GET"])
@jwt_required()
def pokemon_stats():
    """Legacy stats page with bar and pie charts."""
    pokemon_data = db.session.query(
        Pokemon.type1,
        db.func.count(Pokemon.id).label("count"),
    ).group_by(Pokemon.type1).all()

    chart_data = {
        "labels": [d.type1 for d in pokemon_data],
        "values": [d.count for d in pokemon_data],
        "colors": [TYPE_COLORS.get(d.type1, "#FFFFFF") for d in pokemon_data],
    }

    total_pokemon = sum(chart_data["values"])
    pie_chart_data = {
        "labels": chart_data["labels"],
        "values": [(count / total_pokemon) * 100 for count in chart_data["values"]],
    }

    avg_stats = db.session.query(
        db.func.avg(Pokemon.hp).label("hp"),
        db.func.avg(Pokemon.attack).label("attack"),
        db.func.avg(Pokemon.defense).label("defense"),
        db.func.avg(Pokemon.sp_attack).label("sp_attack"),
        db.func.avg(Pokemon.sp_defense).label("sp_defense"),
        db.func.avg(Pokemon.speed).label("speed"),
    ).first()

    combined_type_data = get_combined_type_distribution()
    combined_chart_data = {
        "labels": list(combined_type_data.keys()),
        "values": list(combined_type_data.values()),
        "colors": [
            TYPE_COLORS.get(type_, "#FFFFFF") for type_ in combined_type_data.keys()
        ],
    }

    return render_template(
        "pokemon_dashboard.html",
        chart_data=chart_data,
        pie_chart_data=pie_chart_data,
        combined_chart_data=combined_chart_data,
        total_pokemon=total_pokemon,
        avg_stats=avg_stats._asdict(),
    )


@analytics_bp.route("/pokemon-piechart", methods=["GET"])
@jwt_required()
def pokemon_piechart():
    """Legacy simple pie chart page."""
    pokemon_data = db.session.query(
        Pokemon.type1,
        db.func.count(Pokemon.id).label("count"),
    ).group_by(Pokemon.type1).all()

    chart_data = {
        "labels": [d.type1 for d in pokemon_data],
        "values": [d.count for d in pokemon_data],
    }

    return render_template("pokemon_piechart.html", chart_data=chart_data)
