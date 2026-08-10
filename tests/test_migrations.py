"""Migration tests.

The schema is now owned by Alembic rather than `create_all()`. The risk that
introduces is drift: a model changes, no migration is written, and the mismatch
only surfaces on the production database. These tests build a database purely
from migrations and compare it against the models.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade
from sqlalchemy import create_engine, inspect

from App.app import app as flask_app, db
from App.blueprints.auth import initialize_db
from App.models import Pokemon, User, UserPokemon


def _upgraded_engine(sqlite_uri):
    """Run migrations against an empty database and return its engine."""
    with flask_app.app_context():
        upgrade()
    return create_engine(sqlite_uri)


class TestMigrationsBuildTheSchema:
    """`flask db upgrade` must produce a complete, usable schema from empty."""

    def test_upgrade_creates_every_table(self, sqlite_db):
        engine = _upgraded_engine(sqlite_db)
        tables = set(inspect(engine).get_table_names())
        assert {"pokemon", "user", "user_pokemon", "message"} <= tables
        engine.dispose()

    def test_upgrade_creates_every_index(self, sqlite_db):
        """The models declare 9 indexes; losing them silently is a perf cliff."""
        engine = _upgraded_engine(sqlite_db)
        inspector = inspect(engine)
        found = {
            index["name"]
            for table in ("pokemon", "user_pokemon")
            for index in inspector.get_indexes(table)
        }
        expected = {
            "ix_pokemon_name",
            "ix_pokemon_type1",
            "ix_pokemon_type2",
            "ix_pokemon_generation",
            "ix_pokemon_is_legendary",
            "ix_pokemon_type1_generation",
            "ix_pokemon_type1_type2",
            "ix_user_pokemon_user_id",
            "ix_user_pokemon_pokemon_id",
        }
        assert expected <= found, f"missing indexes: {sorted(expected - found)}"
        engine.dispose()

    def test_password_column_is_255_in_the_migration(self, sqlite_db):
        """T7's widening must be carried by the migration, not just the model.

        This is the column that breaks on Postgres if it stays at 120.
        """
        engine = _upgraded_engine(sqlite_db)
        column = next(
            c for c in inspect(engine).get_columns("user") if c["name"] == "password"
        )
        assert column["type"].length == 255
        engine.dispose()


class TestMigrationsMatchTheModels:
    """Guard against a model change landing without a migration."""

    def test_no_drift_between_migrations_and_models(self, sqlite_db):
        engine = _upgraded_engine(sqlite_db)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            differences = compare_metadata(context, db.metadata)
        engine.dispose()

        assert differences == [], (
            "migrations and models disagree — run `flask db migrate` and commit "
            f"the result. Differences: {differences}"
        )


class TestSeedingIsSeparateFromSchema:
    """`initialize_db` handles rows only; `flask db upgrade` handles tables."""

    def test_seeding_populates_a_migrated_database(self, sqlite_db):
        _upgraded_engine(sqlite_db).dispose()
        with flask_app.app_context():
            initialize_db()
            assert Pokemon.query.count() == 801
            assert User.query.count() == 2

    def test_seeding_twice_does_not_violate_unique_constraints(self, sqlite_db):
        """Without drop_all, a repeat seed must still be safe to run."""
        _upgraded_engine(sqlite_db).dispose()
        with flask_app.app_context():
            initialize_db()
            initialize_db()
            assert Pokemon.query.count() == 801
            assert User.query.count() == 2

    def test_reseeding_keeps_pokemon_ids_stable(self, sqlite_db):
        """Ids are pinned by the seeder, not left to the database.

        Postgres does not reset a SERIAL sequence on DELETE, so a second
        `flask init` renumbered every Pokemon (observed on Neon: 1603-2403).
        That broke the fixed ids used for the demo catches and any bookmarked
        /app/<id> link. SQLite reuses rowids, which is why this only ever
        showed up in production.
        """
        _upgraded_engine(sqlite_db).dispose()
        with flask_app.app_context():
            initialize_db()
            initialize_db()

            first = Pokemon.query.order_by(Pokemon.id).first()
            assert first.id == 1, f"ids drifted after a reseed: first id is {first.id}"
            assert first.name == "Bulbasaur"
            assert Pokemon.query.count() == 801

    def test_demo_catches_survive_a_reseed(self, sqlite_db):
        """catch_pokemon() silently no-ops on a missing id — so assert the rows."""
        _upgraded_engine(sqlite_db).dispose()
        with flask_app.app_context():
            initialize_db()
            initialize_db()
            assert UserPokemon.query.count() == 3, (
                "the seeded demo catches vanished — they reference fixed Pokemon "
                "ids, and catch_pokemon() returns None instead of raising when "
                "the id does not exist"
            )

    def test_seeding_does_not_create_tables(self, sqlite_db):
        """Seeding an un-migrated database must fail, not silently build one."""
        with flask_app.app_context():
            assert inspect(db.engine).get_table_names() == []
            try:
                initialize_db()
            except Exception:
                pass
            assert inspect(db.engine).get_table_names() == [], (
                "initialize_db created tables — schema creation belongs to Alembic"
            )


class TestPagesSurviveAnUnseededDatabase:
    """A missing Pokemon must not take a whole page down."""

    def test_home_page_does_not_crash_without_seed_data(self, auth_client):
        from App.blueprints.auth import clear_seed_data
        from App.models import User

        with flask_app.app_context():
            # Keep the logged-in user, drop the Pokemon rows.
            UserPokemon.query.delete()
            Pokemon.query.delete()
            db.session.commit()
            assert User.query.count() > 0

        response = auth_client.get("/app", follow_redirects=True)
        assert response.status_code == 200, (
            "the home page 500s when no Pokemon exist — it assumed id 1 was "
            "always present"
        )
        assert b"flask init" in response.data or b"No Pokemon data" in response.data

        with flask_app.app_context():
            clear_seed_data()
            initialize_db()
