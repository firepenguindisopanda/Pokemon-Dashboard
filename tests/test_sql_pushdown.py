"""T18 — row selection and aggregation belong in the database, not in Python.

`quiz.generate_question()` called `random.choice(Pokemon.query.all())` to pick
one row, hydrating the entire 801-row Pokemon table to produce a single value.

The risk in fixing it is a silent behaviour change — a different distribution
of quiz subjects. These tests pin the observable behaviour first and the query
shape second.

T18 also pushed the analytics type-distribution helper into a `GROUP BY`, and
those tests lived here too. Both the helper and its tests were deleted after
T21 removed `/pokemon-stats-v1`, its only caller.
"""

import random

import pytest
from sqlalchemy import event

from App.app import app as flask_app, db
from App.blueprints.quiz import generate_question
from App.models import Pokemon


class SqlRecorder:
    """Records every statement a block of code sends to the database."""

    def __init__(self):
        self.statements = []

    def __enter__(self):
        self._engine = db.engine

        def listener(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(" ".join(statement.split()))

        self._listener = listener
        event.listen(self._engine, "after_cursor_execute", listener)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "after_cursor_execute", self._listener)
        return False

    @property
    def full_entity_scans(self):
        """Statements that hydrate whole Pokemon rows without a row limit.

        SQLAlchemy emits every mapped column when it loads an entity, so the
        presence of `pokemon.name` distinguishes "fetch the rows themselves"
        from an aggregate like `count(pokemon.id)` or a `DISTINCT pokemon.type1`
        lookup, which return at most a handful of rows and are not the problem
        this task is about.
        """
        return [
            s
            for s in self.statements
            if "pokemon.name" in s and "FROM pokemon" in s and "LIMIT" not in s.upper()
        ]


class TestQuizPicksOneRowInTheDatabase:
    def test_question_generation_does_not_hydrate_the_whole_table(self, auth_client):
        with flask_app.app_context():
            with SqlRecorder() as sql:
                generate_question()

        assert sql.full_entity_scans == [], (
            "question generation still loads every Pokemon row to pick one:\n  "
            + "\n  ".join(sql.full_entity_scans)
        )

    @pytest.mark.parametrize("offset,expected_id", [(0, 1), (400, 401), (800, 801)])
    def test_every_row_is_reachable_and_the_offset_maps_exactly(
        self, auth_client, monkeypatch, offset, expected_id
    ):
        """A uniform pick over 801 rows must reach the first and the last.

        Driving the choice directly rather than sampling keeps this
        deterministic; an off-by-one that made row 801 unreachable, or that
        shifted every pick by one, would show up here as a wrong id.
        """
        monkeypatch.setattr(random, "randrange", lambda n: offset)

        with flask_app.app_context():
            question = generate_question()
            expected_name = db.session.get(Pokemon, expected_id).name

        assert question["pokemon_name"] == expected_name

    def test_the_last_row_is_never_skipped(self, auth_client, monkeypatch):
        """`randrange(count)` yields count-1 at most — that must be a real row."""
        with flask_app.app_context():
            count = Pokemon.query.count()
            monkeypatch.setattr(random, "randrange", lambda n: n - 1)
            question = generate_question()
            last_name = db.session.get(Pokemon, count).name

        assert question["pokemon_name"] == last_name

    def test_question_shape_is_unchanged(self, auth_client):
        with flask_app.app_context():
            question = generate_question()

        assert set(question) == {
            "question",
            "options",
            "correct_answer",
            "pokemon_name",
            "pokemon_sprite",
        }
        assert len(question["options"]) == 4
        assert question["correct_answer"] in question["options"]


def reference_type_distribution():
    """The original Python implementation, kept as the oracle.

    Counts each Pokemon's type1, then its type2 when that is truthy — note
    *truthy*, so both NULL and an empty string are skipped. Dict order is
    first-appearance order scanning by id, type1 before type2.
    """
    counts = {}
    for pkmn in Pokemon.query.order_by(Pokemon.id).all():
        types = [pkmn.type1]
        if pkmn.type2:
            types.append(pkmn.type2)
        for type_ in types:
            counts[type_] = counts.get(type_, 0) + 1
    return counts
