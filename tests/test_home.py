"""The homepage as a trainer hub, and the payload cost of browsing.

WHAT THIS REPLACED

`/app` was a second, worse copy of `/pokemon-area`: both listed all 801 Pokemon
with a search box, and the same Pokemon had two different detail views with
different information — the type-matchup grid existed on one and was
unreachable from the other.

It also rendered the entire national dex on every request. Measured before the
change: 801 list items, 4,612 DOM nodes, 484 KB of HTML, and 801 `get_json()`
calls per page load. Gzip hid the bandwidth (24 KB on the wire) but not the
parse, layout, or server-render cost. `/pokemon-area` was worse at 1,005 KB.

The homepage now answers "what do I do now?" — status, the Arena, and your
collection — and browsing belongs to `/pokemon-area`, which does it properly.
"""

import re

from App.app import app as flask_app
from tests.test_sql_pushdown import SqlRecorder

# Measured on the old page, kept as the thing these numbers are compared to.
OLD_HTML_BYTES = 484 * 1024
OLD_LIST_ITEMS = 801


def html_of(client, path="/app"):
    response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
    return response.get_data(as_text=True)


def row_loads(sql):
    """Entity scans that actually transfer Pokemon rows.

    `SqlRecorder.full_entity_scans` keys on the presence of `pokemon.name` in
    an unlimited SELECT. `paginate()` emits
    `SELECT count(*) FROM (SELECT <every column> FROM pokemon)`, which mentions
    `pokemon.name` inside the subquery but returns exactly one integer and
    transfers no rows — a false positive for that heuristic.

    Filtering only the aggregate keeps the assertion sharp: a genuine
    `SELECT pokemon.* FROM pokemon` with no LIMIT still fails.
    """
    return [s for s in sql.full_entity_scans
            if not s.upper().lstrip().startswith("SELECT COUNT(")]


class TestTheHomePageNoLongerShipsTheWholeDex:
    def test_it_does_not_hydrate_every_pokemon_row(self, auth_client):
        """The server cost, which gzip does nothing for.

        `get_pokemon_list()` called `get_json()` on all 801 rows for a page
        that only needed a handful of counts.
        """
        with flask_app.app_context():
            with SqlRecorder() as sql:
                auth_client.get("/app")
        scans = row_loads(sql)
        assert not scans, (
            "the home page still hydrates the whole Pokemon table:\n  "
            + "\n  ".join(s[:120] for s in scans)
        )

    def test_the_html_is_a_fraction_of_what_it_was(self, auth_client):
        size = len(html_of(auth_client))
        assert size < 60_000, (
            f"home page is {size // 1024} KB; it was {OLD_HTML_BYTES // 1024} KB "
            f"and the whole point was to stop shipping the dex"
        )

    def test_it_renders_no_dex_list(self, auth_client):
        html = html_of(auth_client)
        items = html.count("list-group-item-action")
        assert items == 0, (
            f"{items} dex list items on the home page (was {OLD_LIST_ITEMS}); "
            f"browsing belongs to /pokemon-area now"
        )


class TestTheTrainerHubShowsWhatMatters:
    def test_it_shows_the_pokeball_count(self, auth_client):
        html = html_of(auth_client)
        assert 'id="pokeball-value"' in html, (
            "no pokeball counter — the arena spends them and the quiz earns "
            "them, so the number has to be visible"
        )

    def test_it_shows_how_many_pokemon_are_caught(self, auth_client):
        html = html_of(auth_client)
        assert 'id="caught-count"' in html

    def test_the_search_box_delegates_to_the_browse_page(self, auth_client):
        """The homepage stopped being a dex browser; its search must not pretend
        otherwise by filtering a list that is no longer there."""
        html = html_of(auth_client)
        form = re.search(r"<form[^>]*>", html)
        assert form, "no form on the home page"
        assert "/pokemon-area" in form.group(0), (
            f"the search form does not target /pokemon-area: {form.group(0)}"
        )

    def test_the_arena_offers_a_way_to_start(self, auth_client):
        """The arena used to render as a 69px header with no content.

        It is the only actual game in the application and it was invisible on
        arrival, under 900px of empty column.
        """
        html = html_of(auth_client)
        assert 'id="arena-no-encounter"' in html
        assert "findWildPokemon" in html, "no way to begin an encounter"

    def test_the_collection_is_shown_as_cards_not_a_table(self, auth_client):
        html = html_of(auth_client)
        assert 'id="user-pokemon-grid"' in html, "no collection grid"
        assert "<table" not in html, (
            "the collection is still a table; at 360px its rename input and "
            "two buttons could not fit, which is what forced .table-responsive"
        )

    def test_the_seeded_catches_appear(self, auth_client):
        html = html_of(auth_client)
        for nickname in ("Benny", "Saul"):
            assert nickname in html, f"{nickname} is missing from the collection"

    def test_running_out_of_pokeballs_points_at_the_quiz(self, auth_client):
        html = html_of(auth_client)
        assert "/quiz" in html, (
            "nothing links to the quiz, so a trainer with no pokeballs has no "
            "visible way to earn more"
        )


class TestThereIsOneCanonicalDetailView:
    def test_app_with_an_id_redirects_to_the_details_page(self, auth_client):
        """Two detail views for one Pokemon is the redundancy being removed."""
        response = auth_client.get("/app/6", follow_redirects=False)
        assert response.status_code in (301, 302), (
            f"/app/6 returned {response.status_code}, not a redirect"
        )
        assert response.headers["Location"].endswith(
            "/pokemon-area/pokemon-details/6"
        ), response.headers["Location"]

    def test_the_redirect_target_actually_renders(self, auth_client):
        response = auth_client.get("/app/6", follow_redirects=True)
        assert response.status_code == 200
        assert "Charizard" in response.get_data(as_text=True)


class TestTheArenaActionsFitOnAPhone:
    def test_the_action_row_wraps(self):
        """Measured: three buttons in a nowrap flex row pushed the page 31px
        wide at 375px — the only true horizontal overflow on the page."""
        from pathlib import Path
        html = Path("App/templates/home.html").read_text(encoding="utf8")
        row = re.search(r'<div[^>]*id="arena-actions"[^>]*>', html)
        assert row, "no #arena-actions row"
        assert "flex-wrap" in row.group(0), (
            f"#arena-actions does not wrap, so it overflows at 375px: {row.group(0)}"
        )


class TestBrowsingIsPaginated:
    """/pokemon-area was 1,005 KB decoded — the larger of the two payloads,
    and the page users land on now that the homepage delegates to it."""

    def test_it_does_not_hydrate_every_row(self, auth_client):
        with flask_app.app_context():
            with SqlRecorder() as sql:
                auth_client.get("/pokemon-area")
        scans = row_loads(sql)
        assert not scans, (
            "/pokemon-area still loads all 801 rows:\n  "
            + "\n  ".join(s[:120] for s in scans)
        )

    def test_a_page_holds_a_bounded_number_of_pokemon(self, auth_client):
        html = html_of(auth_client, "/pokemon-area")
        cards = html.count('href="/pokemon-area/pokemon-details/')
        assert 0 < cards <= 60, f"{cards} Pokemon on one page; expected a page-sized slice"

    def test_the_pages_are_navigable(self, auth_client):
        html = html_of(auth_client, "/pokemon-area")
        assert "page=2" in html, "no way to reach the second page"

    def test_later_pages_show_different_pokemon(self, auth_client):
        first = html_of(auth_client, "/pokemon-area?page=1")
        second = html_of(auth_client, "/pokemon-area?page=2")
        assert "Bulbasaur" in first
        assert "Bulbasaur" not in second, "page 2 repeats page 1"

    def test_search_still_works_and_stays_bounded(self, auth_client):
        html = html_of(auth_client, "/pokemon-area?query=char")
        assert "Charizard" in html
        assert "Bulbasaur" not in html

    def test_the_generation_filter_still_works(self, auth_client):
        html = html_of(auth_client, "/pokemon-area?generation=1")
        assert "Bulbasaur" in html
        assert "Chikorita" not in html, "generation 2 leaked into a generation 1 filter"

    def test_a_filter_survives_paging(self, auth_client):
        """Losing the filter on page 2 is the classic pagination bug."""
        html = html_of(auth_client, "/pokemon-area?generation=1&page=2")
        assert "generation=1" in html, (
            "the generation filter is not carried in the page links, so page 2 "
            "silently drops it"
        )

    def test_an_out_of_range_page_does_not_500(self, auth_client):
        response = auth_client.get("/pokemon-area?page=9999")
        assert response.status_code == 200

    def test_a_nonsense_page_does_not_500(self, auth_client):
        response = auth_client.get("/pokemon-area?page=notanumber")
        assert response.status_code == 200


class TestRenamingActuallyRenames:
    """Found in a browser: renaming reported success and destroyed the name.

    The rename `<input>` sat outside its `<form>` and carried no `form`
    attribute, so `input.form` was null and the browser submitted only the CSRF
    token. `rename_pokemon()` then set `name = None`, committed, and returned
    True — so the handler flashed "has been given a new name successfully!"
    over a row whose nickname it had just wiped. Verified against the database:
    bob's "Benny" became NULL.

    Both halves are covered here, because either alone would have prevented it:
    the markup that failed to submit the field, and the model that accepted its
    absence. The existing tests only ever called `rename_pokemon(id, 'NewName')`
    with a real name.
    """

    @staticmethod
    def _owned():
        from App.models import User, UserPokemon
        with flask_app.app_context():
            user = User.query.filter_by(username="bob").first()
            row = UserPokemon.query.filter_by(user_id=user.id).first()
            return row.id, row.name

    @staticmethod
    def _name_of(poke_id):
        from App.app import db
        from App.models import UserPokemon
        with flask_app.app_context():
            return db.session.get(UserPokemon, poke_id).name

    def test_the_rename_field_is_submitted_with_its_form(self):
        from pathlib import Path
        html = Path("App/templates/home.html").read_text(encoding="utf8")
        field = re.search(r'<input[^>]*name="new_name_[^"]*"[^>]*>', html)
        assert field, "no rename field in home.html"
        tag = field.group(0)
        if 'form="' in tag:
            return
        before = html[: field.start()]
        assert before.rfind("<form") > before.rfind("</form>"), (
            "the rename input is outside its form and has no form= attribute, "
            f"so the browser submits nothing for it: {tag}"
        )

    def test_a_missing_name_does_not_wipe_the_nickname(self, auth_client):
        poke_id, original = self._owned()
        assert original, "fixture precondition: the seeded catch has a nickname"
        auth_client.post(
            f"/rename-pokemon/{poke_id}", data={}, headers={"Referer": "/app"}
        )
        assert self._name_of(poke_id) == original, (
            "a rename with no name submitted destroyed the existing nickname"
        )

    def test_a_blank_name_does_not_wipe_the_nickname(self, auth_client):
        poke_id, original = self._owned()
        auth_client.post(
            f"/rename-pokemon/{poke_id}",
            data={f"new_name_{poke_id}": "   "},
            headers={"Referer": "/app"},
        )
        assert self._name_of(poke_id) == original

    def test_a_failed_rename_does_not_claim_success(self, auth_client):
        poke_id, _ = self._owned()
        page = auth_client.post(
            f"/rename-pokemon/{poke_id}", data={}, headers={"Referer": "/app"},
            follow_redirects=True,
        ).get_data(as_text=True)
        assert "successfully" not in page.lower(), (
            "the handler reported success for a rename that changed nothing"
        )

    def test_a_real_rename_still_works(self, auth_client):
        poke_id, _ = self._owned()
        auth_client.post(
            f"/rename-pokemon/{poke_id}",
            data={f"new_name_{poke_id}": "Sparky"},
            headers={"Referer": "/app"},
        )
        assert self._name_of(poke_id) == "Sparky"

    def test_the_model_refuses_to_blank_a_name(self, auth_client):
        """The second half. Fixing only the markup leaves the model able to do
        this to any caller — the arena, a future API, a console session."""
        from App.models import User
        poke_id, original = self._owned()
        with flask_app.app_context():
            user = User.query.filter_by(username="bob").first()
            for empty in (None, "", "   "):
                assert user.rename_pokemon(poke_id, empty) is not True, (
                    f"rename_pokemon accepted {empty!r} and reported success"
                )
        assert self._name_of(poke_id) == original
