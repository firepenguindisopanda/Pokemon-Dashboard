import click

from App import app
from App.blueprints.auth import initialize_db


@app.cli.command("init", help="Seed the database with Pokemon data and default users")
def initialize():
    """Seed the database.

    Requires the schema to exist — run `flask db upgrade` first.
    """
    initialize_db()
    print("database seeded")


@app.cli.command("train", help="Train and cache the analytics models")
@click.option(
    "--force",
    is_flag=True,
    help="Retrain even when a cached model matches the current data.",
)
def train(force):
    """Build the ML models and persist them to the model cache.

    Training is an operator action rather than something that happens on
    import or on a user's first request. Run this after seeding, and again
    whenever the Pokemon data changes.
    """
    from App.blueprints.analytics import initialize_pokemon_analytics
    from App.lib import get_analytics_instance  # noqa: F401 — import check

    if force:
        from App.ml_utils import clear_cache

        removed = clear_cache()
        print(f"cleared {removed} cached artifact(s)")

    if initialize_pokemon_analytics():
        print("analytics models trained and cached")
    else:
        raise SystemExit(
            "training failed — check the log. Has the database been seeded "
            "with `flask init`?"
        )
