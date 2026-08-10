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
from App.models import Pokemon, User


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
