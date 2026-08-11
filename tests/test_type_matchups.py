"""The weakness grid on the Pokemon details page.

Live testing found the feature this replaces reporting "0 weaknesses, 0%
coverage" for every team, because the analytics dataframe is built from the
`Pokemon` model and that model has no `against_*` columns. The grid here is
computed from `type1`/`type2` instead, so it cannot drift from the types shown
in the header two inches above it.

The ordering and second-type tests are the ones that matter: the /pokemon-ml
grid this borrows from rendered 18 identical rows for a year because its cell
value ignored one of its two loop variables, and nothing failed.
"""

import re
import pytest

from App.app import app as flask_app, db
from App.models import Pokemon
from App.type_chart import ALL_TYPES

GRID = re.compile(r'id="type-matchups".*?<!-- /type-matchups -->', re.S)


def pokemon_id(name):
    with flask_app.app_context():
        row = db.session.query(Pokemon).filter_by(name=name).first()
        assert row is not None, f"{name} is not in the seeded dataset"
        return row.id


def grid_html(auth_client, name):
    page = auth_client.get(f"/pokemon-area/pokemon-details/{pokemon_id(name)}")
    assert page.status_code == 200, f"details page for {name} returned {page.status_code}"
    body = page.get_data(as_text=True)
    match = GRID.search(body)
    assert match, "the details page renders no #type-matchups region"
    return match.group(0)


class TestTheGridRenders:
    def test_the_details_page_has_a_matchup_region(self, auth_client):
        assert grid_html(auth_client, "Charizard")

    def test_all_eighteen_attacking_types_appear(self, auth_client):
        html = grid_html(auth_client, "Charizard")
        missing = [t for t in ALL_TYPES
                   if not re.search(rf'data-type="{t}"', html)]
        assert not missing, f"the grid omits {missing}"

    def test_the_region_has_an_accessible_name(self, auth_client):
        html = grid_html(auth_client, "Charizard")
        assert re.search(r'aria-label="[^"]+"', html), (
            "the matchup region has no accessible name, so a screen-reader "
            "user reaches a wall of multipliers with no idea what they are"
        )


class TestTheNumbersAreRight:
    def test_charizard_is_quadruple_weak_to_rock(self, auth_client):
        html = grid_html(auth_client, "Charizard")
        cell = re.search(r'data-type="rock"[^>]*data-multiplier="([\d.]+)"', html)
        assert cell, "no rock cell in Charizard's grid"
        assert float(cell.group(1)) == 4.0

    def test_charizard_is_immune_to_ground(self, auth_client):
        html = grid_html(auth_client, "Charizard")
        cell = re.search(r'data-type="ground"[^>]*data-multiplier="([\d.]+)"', html)
        assert float(cell.group(1)) == 0.0

    def test_the_second_type_actually_changes_the_grid(self, auth_client):
        """The exact bug that made the /pokemon-ml matrix meaningless.

        Charmeleon is pure fire; Charizard is fire/flying. If the second type
        were dropped — or the cell value computed from the wrong variable —
        these two grids would be identical and both would be wrong.
        """
        mono = grid_html(auth_client, "Charmeleon")
        dual = grid_html(auth_client, "Charizard")

        def rock(html):
            return re.search(r'data-type="rock"[^>]*data-multiplier="([\d.]+)"',
                             html).group(1)

        assert float(rock(mono)) == 2.0, "pure fire should take 2x from rock"
        assert float(rock(dual)) == 4.0, "fire/flying should take 4x from rock"

    @pytest.mark.parametrize("name,attacker,expected", [
        ("Blastoise", "electric", 2.0),     # pure water
        ("Venusaur", "flying", 2.0),        # grass/poison
        ("Gengar", "normal", 0.0),          # ghost/poison is immune
        ("Snorlax", "fighting", 2.0),       # pure normal
        ("Pikachu", "ground", 2.0),         # pure electric
    ])
    def test_known_matchups_render(self, auth_client, name, attacker, expected):
        html = grid_html(auth_client, name)
        cell = re.search(
            rf'data-type="{attacker}"[^>]*data-multiplier="([\d.]+)"', html)
        assert cell, f"no {attacker} cell for {name}"
        assert float(cell.group(1)) == expected


class TestItIsReadableWithoutColour:
    def test_every_cell_carries_its_multiplier_as_text(self, auth_client):
        """Colour-coding is reinforcement, never the only signal.

        Tags are stripped before the check, and the check looks only for the
        multiplier glyphs. Searching the raw markup would pass on class names
        alone — `weak-4x` contains a 4 — which would make this assert nothing.
        """
        html = grid_html(auth_client, "Charizard")
        tiles = re.findall(r"<li[^>]*data-type=\"\w+\".*?</li>", html, re.S)
        assert len(tiles) == 18, f"expected 18 tiles, parsed {len(tiles)}"
        unlabelled = [t for t in tiles
                      if not re.search(r"[¼½×]", re.sub(r"<[^>]+>", " ", t))]
        assert not unlabelled, (
            f"{len(unlabelled)} tiles convey their value by colour alone"
        )

    def test_worst_matchups_come_first(self, auth_client):
        """A weakness the player has to plan around belongs above a resistance."""
        html = grid_html(auth_client, "Charizard")
        order = re.findall(r'data-type="(\w+)"', html)
        assert order.index("rock") < order.index("grass"), (
            "the x4 rock weakness renders after a resistance"
        )
        assert order.index("water") < order.index("ground"), (
            "a x2 weakness renders after an immunity"
        )


class TestTheMlPlaygroundGridIsARealMatrix:
    """Static guards for the two defects that made the /pokemon-ml grid lie.

    Neither was catchable by the Python suite — the bugs lived in a renderer
    that only runs after a team is generated in a browser — so these assert on
    the source. Crude, but the alternative was another year of an 18x18 grid
    displaying 18 numbers.
    """

    @staticmethod
    def source():
        """The file's CODE, with comments removed.

        Scanning the raw text does not work, and this project has now been
        bitten by it three times: T21's "the handler is deleted" check and
        T25's orphan check both failed on the comments that explained the very
        bug they guarded. The comments below describe `defenses[defType]` and
        `|| 1.0` by name, so a raw scan reports them as still present.
        """
        from pathlib import Path
        text = Path("App/static/js/ml_playground.js").read_text(encoding="utf8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith(("//", "*"))
        )

    def test_rows_come_from_per_member_data(self):
        js = self.source()
        assert "data.members" in js, (
            "the matrix no longer iterates team members, so every row is "
            "whatever the worst case is — the original bug"
        )
        assert "member.multipliers[" in js, (
            "cell values are not looked up per member; if the lookup is keyed "
            "by column alone, all rows render identically"
        )

    def test_the_column_only_lookup_is_gone(self):
        js = self.source()
        assert "defenses[defType]" not in js, (
            "the column-keyed cell lookup is back — this is what made the row "
            "variable decorative"
        )

    def test_immunities_are_not_defaulted_away(self):
        """`worst_case[t] || 1.0` maps a real 0 to neutral.

        Zero is falsy, so the guard meant to supply a default for missing data
        also erased every immunity the API reported.
        """
        js = self.source()
        assert "|| 1.0" not in js, (
            "a `|| 1.0` default is back; it reclassifies every immunity (0) "
            "as neutral, because 0 is falsy"
        )

    def test_the_worst_case_row_is_still_shown(self):
        js = self.source()
        assert "worst_case" in js and "Worst case" in js, (
            "the per-member rows replaced the team's actual exposure instead "
            "of adding to it"
        )

    def test_the_table_has_header_semantics(self):
        js = self.source()
        assert 'scope="col"' in js and 'scope="row"' in js, (
            "an 18-column table without scoped headers is unreadable to a "
            "screen reader"
        )
