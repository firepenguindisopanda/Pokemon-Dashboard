from App import app
from App.blueprints.auth import initialize_db


@app.cli.command("init", help="Seed the database with Pokemon data and default users")
def initialize():
    """Seed the database.

    Requires the schema to exist — run `flask db upgrade` first.
    """
    initialize_db()
    print("database seeded")
