"""Type effectiveness, derived rather than stored.

WHY THIS MODULE EXISTS AT ALL
The dataset ships 18 `against_*` columns, but the `Pokemon` DB model never got
them — so every deployment computed type coverage from a fallback of 1.0 and
reported "0 weaknesses, 0% coverage" for every team ever built. Rather than
migrate 18 columns onto 801 rows, effectiveness is derived from `type1`/`type2`,
which the database already stores.

That trade is only sound if derivation actually reproduces the dataset, so
`TestTheChartAgreesWithTheDataset` checks all 14,418 values rather than a
sample.
"""

import csv
import pytest

from App.type_chart import TYPE_CHART, ALL_TYPES, defensive_multipliers

# Every multiplier reachable by stacking two single-type values.
LEGAL = {0.0, 0.25, 0.5, 1.0, 2.0, 4.0}

# Rows where the dataset contradicts itself: `type1`/`type2` describe the
# Alolan form while the `against_*` columns still describe the Kantonian one.
# Listed by name so the exclusion cannot quietly grow — a tenth entry fails
# `test_the_only_disagreements_are_the_known_alolan_rows`.
ALOLAN_CONTRADICTIONS = {
    "Marowak", "Meowth", "Ninetales", "Persian", "Raticate",
    "Rattata", "Sandshrew", "Sandslash", "Vulpix",
}


class TestKnownMatchups:
    """Values any player could check by hand."""

    def test_charizard_is_quadruple_weak_to_rock(self):
        # fire/flying: rock is 2x against both halves. The single most famous
        # 4x weakness in the franchise, and the one a stacking bug destroys.
        assert defensive_multipliers("fire", "flying")["rock"] == 4.0

    def test_charizard_is_immune_to_ground(self):
        # flying zeroes ground even though fire alone takes 2x from it, so
        # this only passes if 0 propagates through the multiply.
        assert defensive_multipliers("fire", "flying")["ground"] == 0.0

    def test_charizard_quarter_resists_grass_and_bug(self):
        m = defensive_multipliers("fire", "flying")
        assert m["grass"] == 0.25
        assert m["bug"] == 0.25

    def test_a_single_type_uses_only_its_own_column(self):
        assert defensive_multipliers("water", None)["electric"] == 2.0
        assert defensive_multipliers("water", None)["fire"] == 0.5

    def test_normal_is_immune_to_ghost(self):
        assert defensive_multipliers("normal", None)["ghost"] == 0.0


class TestTheDatasetsMalformedRows:
    """The CSV contains type pairs that cannot exist in the games."""

    def test_a_duplicated_second_type_is_not_applied_twice(self):
        # The dataset stores Diglett as ground/ground. Multiplying the ground
        # column in twice would report a 4x grass weakness for a Pokemon that
        # is only 2x weak, and would turn its 0x electric into 0x by accident
        # rather than by rule.
        assert defensive_multipliers("ground", "ground") == \
               defensive_multipliers("ground", None)

    def test_a_duplicated_second_type_keeps_multipliers_legal(self):
        for value in defensive_multipliers("electric", "electric").values():
            assert value in LEGAL

    @pytest.mark.parametrize("blank", [None, "", "None", "none", "  "])
    def test_absent_second_types_are_all_treated_the_same(self, blank):
        assert defensive_multipliers("water", blank) == \
               defensive_multipliers("water", None)

    def test_type_names_are_matched_case_insensitively(self):
        assert defensive_multipliers("Fire", "Flying") == \
               defensive_multipliers("fire", "flying")

    def test_an_unrecognised_type_is_neutral_rather_than_an_error(self):
        # A future generation, a typo, or a renamed row must not 500 a page
        # that is only trying to draw a grid.
        assert defensive_multipliers("fire", "cosmic")["rock"] == \
               defensive_multipliers("fire", None)["rock"]


class TestShape:
    def test_every_call_returns_all_eighteen_attacking_types(self):
        assert set(defensive_multipliers("fire", "flying")) == set(ALL_TYPES)
        assert len(ALL_TYPES) == 18

    def test_every_multiplier_is_a_legal_value(self):
        for t1 in ALL_TYPES:
            for t2 in [None] + list(ALL_TYPES):
                for value in defensive_multipliers(t1, t2).values():
                    assert value in LEGAL, f"{t1}/{t2} produced {value}"

    def test_the_chart_defines_every_attacking_type(self):
        assert set(TYPE_CHART) == set(ALL_TYPES)

    def test_the_chart_never_references_a_type_that_does_not_exist(self):
        for attacker, row in TYPE_CHART.items():
            unknown = set(row) - set(ALL_TYPES)
            assert not unknown, f"{attacker} attacks unknown {unknown}"

    def test_the_chart_stores_only_non_neutral_entries(self):
        # A stored 1.0 is dead weight that invites disagreement with the
        # default, so the table omits them by construction.
        for attacker, row in TYPE_CHART.items():
            assert 1.0 not in row.values(), f"{attacker} stores a redundant 1.0"


class TestTheChartAgreesWithTheDataset:
    """The whole justification for deriving instead of storing."""

    @staticmethod
    def _rows():
        with open("pokemon.csv", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_derived_values_match_every_against_column_in_the_csv(self):
        disagreed = set()
        compared = 0
        for row in self._rows():
            if row["name"] in ALOLAN_CONTRADICTIONS:
                continue
            derived = defensive_multipliers(row["type1"], row["type2"])
            for attacker in ALL_TYPES:
                column = "against_" + ("fight" if attacker == "fighting"
                                       else attacker)
                compared += 1
                if abs(float(row[column]) - derived[attacker]) > 1e-9:
                    disagreed.add(row["name"])
        assert compared > 14000, f"only compared {compared} values"
        assert not disagreed, f"derivation disagrees with the CSV: {disagreed}"

    def test_the_only_disagreements_are_the_known_alolan_rows(self):
        # Guards the exclusion list above: if derivation breaks for anything
        # else, the excluded set stops being an explanation and this fails.
        actually_disagree = set()
        for row in self._rows():
            derived = defensive_multipliers(row["type1"], row["type2"])
            for attacker in ALL_TYPES:
                column = "against_" + ("fight" if attacker == "fighting"
                                       else attacker)
                if abs(float(row[column]) - derived[attacker]) > 1e-9:
                    actually_disagree.add(row["name"])
                    break
        assert actually_disagree == ALOLAN_CONTRADICTIONS
