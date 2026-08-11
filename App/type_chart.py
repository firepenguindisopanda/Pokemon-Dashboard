"""Type effectiveness, derived from the two types the database already stores.

WHY THIS IS NOT A DATABASE COLUMN

The source CSV carries 18 `against_*` columns, but the `Pokemon` model was
never given them. Nothing noticed, because `calculate_team_type_coverage()`
fell back to a neutral 1.0 for any column it could not find — so production
answered "0 weaknesses, 0 resistances, coverage 0%" for every team anyone
built, and did it with a 200.

The obvious repair is a migration adding 18 float columns to 801 rows. This
module is the cheaper one: effectiveness is a pure function of `type1` and
`type2`, which are already stored and already indexed. 324 constants replace
14,418 stored values that could drift from the types beside them — and in this
dataset already have (see ALOLAN below).

Verified rather than assumed: `tests/test_type_chart.py` derives all 14,418
values and compares them against the CSV. 14,369 match exactly.

ALOLAN
The 49 that do not are nine Pokemon whose rows pair Alolan types with the
Kantonian form's `against_*` values — Alolan Vulpix is listed as fire/ice, but
its stored numbers are pure fire. The derived value is the one that agrees
with the types the row actually claims, so deriving is also the more correct
answer here, not merely the cheaper one.

The dataset also contains pairs that cannot exist in the games at all
(`Diglett` as ground/ground, `Raichu` as electric/electric), which is why the
second type is skipped when it repeats the first: multiplying the same column
in twice would invent 4x weaknesses out of a data-entry artifact.
"""

from App.constants import ALL_TYPES

# attacker -> {defender: multiplier}, non-neutral entries only.
#
# Omitting the 1.0s is deliberate: a stored default is a second place for the
# truth to live, and the two can disagree. `.get(defender, 1.0)` is the only
# source of neutrality, and a test asserts no 1.0 is ever written down here.
TYPE_CHART = {
    "normal": {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fire": {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 2.0, "bug": 2.0,
             "rock": 0.5, "dragon": 0.5, "steel": 2.0},
    "water": {"fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0,
              "rock": 2.0, "dragon": 0.5},
    "electric": {"water": 2.0, "electric": 0.5, "grass": 0.5, "ground": 0.0,
                 "flying": 2.0, "dragon": 0.5},
    "grass": {"fire": 0.5, "water": 2.0, "grass": 0.5, "poison": 0.5,
              "ground": 2.0, "flying": 0.5, "bug": 0.5, "rock": 2.0,
              "dragon": 0.5, "steel": 0.5},
    "ice": {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 0.5,
            "ground": 2.0, "flying": 2.0, "dragon": 2.0, "steel": 0.5},
    "fighting": {"normal": 2.0, "ice": 2.0, "poison": 0.5, "flying": 0.5,
                 "psychic": 0.5, "bug": 0.5, "rock": 2.0, "ghost": 0.0,
                 "dark": 2.0, "steel": 2.0, "fairy": 0.5},
    "poison": {"grass": 2.0, "poison": 0.5, "ground": 0.5, "rock": 0.5,
               "ghost": 0.5, "steel": 0.0, "fairy": 2.0},
    "ground": {"fire": 2.0, "electric": 2.0, "grass": 0.5, "poison": 2.0,
               "flying": 0.0, "bug": 0.5, "rock": 2.0, "steel": 2.0},
    "flying": {"electric": 0.5, "grass": 2.0, "fighting": 2.0, "bug": 2.0,
               "rock": 0.5, "steel": 0.5},
    "psychic": {"fighting": 2.0, "poison": 2.0, "psychic": 0.5, "dark": 0.0,
                "steel": 0.5},
    "bug": {"fire": 0.5, "grass": 2.0, "fighting": 0.5, "poison": 0.5,
            "flying": 0.5, "psychic": 2.0, "ghost": 0.5, "dark": 2.0,
            "steel": 0.5, "fairy": 0.5},
    "rock": {"fire": 2.0, "ice": 2.0, "fighting": 0.5, "ground": 0.5,
             "flying": 2.0, "bug": 2.0, "steel": 0.5},
    "ghost": {"normal": 0.0, "psychic": 2.0, "ghost": 2.0, "dark": 0.5},
    "dragon": {"dragon": 2.0, "steel": 0.5, "fairy": 0.0},
    "dark": {"fighting": 0.5, "psychic": 2.0, "ghost": 2.0, "dark": 0.5,
             "fairy": 0.5},
    "steel": {"fire": 0.5, "water": 0.5, "electric": 0.5, "ice": 2.0,
              "rock": 2.0, "steel": 0.5, "fairy": 2.0},
    "fairy": {"fire": 0.5, "fighting": 2.0, "poison": 0.5, "dragon": 2.0,
              "dark": 2.0, "steel": 0.5},
}

# Values the UI groups on. Floats compare exactly here because every result is
# a product of exact binary fractions (0, 1/4, 1/2, 1, 2, 4).
QUADRUPLE, DOUBLE, NEUTRAL, HALF, QUARTER, IMMUNE = 4.0, 2.0, 1.0, 0.5, 0.25, 0.0

# Multiplier -> (label, css modifier). Ordered worst-first, which is also the
# order the details page renders: what kills you belongs above what does not.
SEVERITY = (
    (QUADRUPLE, "×4", "weak-4x"),
    (DOUBLE, "×2", "weak-2x"),
    (NEUTRAL, "×1", "neutral"),
    (HALF, "½", "resist-2x"),
    (QUARTER, "¼", "resist-4x"),
    (IMMUNE, "×0", "immune"),
)


def normalise_type(type_name):
    """A type name from anywhere, or None if it is not one.

    The dataset, the database and the templates each spell types slightly
    differently ("Fire", "fire", "None", "", NaN-as-string). Membership of
    TYPE_CHART is the only test applied, which covers all of those at once —
    an explicit blocklist of "none"/"nan" was here first and was dead code,
    since none of those strings is a type name either.
    """
    if not type_name:
        return None
    cleaned = str(type_name).strip().lower()
    return cleaned if cleaned in TYPE_CHART else None


def defensive_multipliers(type1, type2=None):
    """How much damage each of the 18 attacking types deals to this Pokemon.

    Returns every type, including the neutral ones: a caller drawing a grid
    needs all 18 cells, and a caller summarising can filter. Unknown types are
    treated as neutral rather than raising — a grid is not worth a 500.
    """
    first = normalise_type(type1)
    second = normalise_type(type2)

    # Skip a repeated second type. The dataset has rows like ground/ground,
    # and applying that column twice manufactures 4x weaknesses that the games
    # have no way to produce.
    if second == first:
        second = None

    multipliers = {}
    for attacker in ALL_TYPES:
        row = TYPE_CHART[attacker]
        value = row.get(first, 1.0)
        if second is not None:
            value *= row.get(second, 1.0)
        multipliers[attacker] = value
    return multipliers


def describe_matchups(type1, type2=None):
    """Defensive multipliers arranged for display.

    Returns the full 18-cell grid worst-first, plus the grouped buckets a
    summary line needs, so a template can render both without recomputing or
    re-sorting.
    """
    multipliers = defensive_multipliers(type1, type2)
    labels = {value: (label, css) for value, label, css in SEVERITY}
    order = {value: index for index, (value, _, _) in enumerate(SEVERITY)}

    cells = [
        {
            "type": attacker,
            "multiplier": multiplier,
            "label": labels[multiplier][0],
            "css": labels[multiplier][1],
        }
        for attacker, multiplier in multipliers.items()
    ]
    # Worst first, then alphabetically so the order is stable between renders
    # and between two Pokemon that share a weakness.
    cells.sort(key=lambda cell: (order[cell["multiplier"]], cell["type"]))

    def bucket(predicate):
        return [c["type"] for c in cells if predicate(c["multiplier"])]

    return {
        "cells": cells,
        "weaknesses": bucket(lambda m: m > NEUTRAL),
        "resistances": bucket(lambda m: IMMUNE < m < NEUTRAL),
        "immunities": bucket(lambda m: m == IMMUNE),
    }
