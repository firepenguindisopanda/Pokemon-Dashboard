"""T23 — the 375px pass.

Measured in Chrome at 375x667 before any change. The headline result was a
surprise and is worth recording, because it changed what this task is:

    horizontal body overflow on all 9 routes: ZERO

T19–T22 had already fixed that by accident — `.matchup-grid-wrapper` scrolls
its own 18x18 table, the chart containers are sized in relative units, and the
dashboard grid is `repeat(auto-fit, minmax(300px, 1fr))`, which collapses to one
column on its own. The instrument was validated before that result was believed:
a 600px canary element injected into a live page produced 240px of overflow and
was reported, and the same canary inside an `overflow-x: auto` parent was
correctly ignored.

So the three real defects were the *other* two criteria:

    1. the navbar wraps to two rows on every page — the brand and the theme
       toggle overlap by 9px at 360px, making the bar 96px instead of ~56px
    2. the caught-Pokémon table is squashed to fit, not scrolled: the release
       button ends up 22px wide, under the 24px minimum target size
    3. `.password-toggle` on the auth screens is 18x17

These tests are static guards. Layout cannot be measured in pytest, so the
browser pass is the proof and these stop the specific regressions.

Note on the viewport: an iframe or desktop Chrome at 375px reports a 360px
client width, because the 15px scrollbar is not an overlay. Everything here was
therefore verified at **360px**, which is stricter than the 375px target.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = Path("App/templates")
STYLES_CSS = Path("App/static/css/styles.css")
COMPONENTS_CSS = Path("App/static/css/components.css")
HOME_JS_TABLE = "user-pokemon-tbody"
HOME_JS = Path("App/static/js/home.js")

# WCAG 2.2 Target Size (Minimum), 2.5.8. The project's axe run targets 2.1 AA,
# so this is above the contractual bar — but a 22px control on a phone is the
# thing a 375px pass exists to catch.
MIN_TARGET_PX = 24


def read(path):
    return Path(path).read_text(encoding="utf8")


def css_all():
    return read(STYLES_CSS) + read(COMPONENTS_CSS)


class TestNoTableSquashesToFit:
    """Acceptance: tables scroll in their own container, not the body.

    The caught-Pokémon table passed the "no body overflow" check by being
    *compressed* — 334px wide inside a 360px viewport, with the Name column at
    61px and the rename input at 98px. Nothing overflowed because Bootstrap
    shrank the columns until it fit, which is the failure this criterion is
    about, not a pass.
    """

    def test_every_server_rendered_table_is_wrapped(self):
        for name in sorted(p.name for p in TEMPLATES.glob("*.html")):
            html = read(TEMPLATES / name)
            for match in re.finditer(r"<table\b", html):
                before = html[max(0, match.start() - 400):match.start()]
                assert "table-responsive" in before, (
                    f"{name} has a <table> with no .table-responsive wrapper "
                    f"within 400 chars above it; at 375px it will squash "
                    f"instead of scrolling"
                )

    def test_the_collection_is_not_a_table_at_all(self):
        """The caught-Pokémon table is gone; the homepage redesign made it a grid.

        This test used to assert that the JS-rebuilt copy of that table carried
        `.table-responsive`, because the server-rendered fix vanished the moment
        a user caught their first Pokémon and the script replaced the markup.

        A wrapping grid removes the whole category: nothing to squash, nothing
        to scroll, and no second copy of the markup in JS to keep in step. The
        assertion is kept — pointed at the property that replaced it — so the
        table cannot quietly come back.
        """
        html = read(TEMPLATES / "home.html")
        assert "<table" not in html, (
            "the collection is a table again; at 360px its rename input and "
            "two buttons squash below the minimum target size"
        )
        assert HOME_JS_TABLE not in read(HOME_JS), (
            f"home.js still rebuilds a {HOME_JS_TABLE} table after a catch"
        )
        assert "collection-grid" in html, "no collection grid in home.html"

    def test_the_js_built_collection_card_matches_the_template(self):
        """The JS copy of a collection card must keep the template's fixes.

        This is the same trap the old table had: markup exists twice, once in
        Jinja and once as a JS string, and only one gets fixed. The rename
        input being *inside* its form is the fix that matters here — outside
        it, the browser submits no name and the server writes NULL over the
        nickname.
        """
        js = read(HOME_JS)
        card = re.search(r"item\.innerHTML\s*=(.*?);\n", js, re.S)
        assert card, "could not find the JS-built collection card in home.js"
        markup = card.group(1)
        form_at = markup.find("collection-rename")
        input_at = markup.find("new_name_")
        assert form_at != -1 and input_at > form_at, (
            "the JS-built rename input is not inside its form, so a card added "
            "after a catch reproduces the nickname-wiping bug"
        )


class TestNavbarFitsOnOneRow:
    """Acceptance: nav collapses cleanly with the toggle reachable.

    The toggle *was* reachable — T19 deliberately put it outside the collapse.
    What it was not was clean: at 360px the brand ran to x=218 and the toggle
    started at x=208, so the flex row wrapped and the bar became 96px tall on
    every page, with the brand on one line and the controls on another.
    """

    def test_a_small_screen_rule_constrains_the_brand(self):
        css = css_all()
        assert re.search(
            r"@media[^{]*max-width:\s*[3-5][0-9]{2}(\.\d+)?px[^{]*\{"
            r"(?:[^{}]|\{[^{}]*\})*?\.navbar-brand",
            css,
            re.S,
        ), (
            "no small-screen rule targets .navbar-brand; at 360px it is 206px "
            "wide and collides with the theme toggle"
        )

    def test_the_theme_toggle_has_a_compact_form(self):
        """The toggle carries an icon and a text label ('Dark'/'Light').

        On a 360px bar the label is what pushes it into the brand. It stays in
        the DOM for screen readers; only the visual label is dropped.
        """
        css = css_all()
        assert re.search(r"#theme-label|\.theme-toggle-nav\s+span", css), (
            "nothing targets the toggle's text label, so it cannot be hidden "
            "on a narrow bar"
        )

    def test_the_toggle_label_is_hidden_accessibly_not_removed(self):
        """`display: none` would take it from the accessibility tree too.

        The button would then announce as "Dark"-less — an icon with no name
        beyond its aria-label. Keep the text, hide it visually.
        """
        css = css_all()
        label_rules = re.findall(r"#theme-label\s*\{([^}]*)\}", css)
        assert label_rules, "no rule targets #theme-label"
        for body in label_rules:
            if re.search(r"display:\s*none", body):
                pytest.fail(
                    "#theme-label uses display:none, which removes it from the "
                    "accessibility tree; use a visually-hidden pattern instead"
                )


class TestTouchTargets:
    """A 22px control is a 375px problem specifically.

    At desktop widths these are fine; the table only squeezes them below the
    minimum once the viewport is narrow. Measured at 360px:
        release button   22 x 40
        rename button    24 x 40   (exactly at the limit, no margin)
        .password-toggle 18 x 17
    """

    def test_icon_only_table_actions_have_a_minimum_size(self):
        css = css_all()
        assert re.search(
            r"\.pokemon-actions[^{]*\{[^}]*min-width:\s*(2[4-9]|[3-9][0-9])px", css, re.S
        ) or re.search(
            r"\.table[^{]*\bbtn\b[^{]*\{[^}]*min-width:\s*(2[4-9]|[3-9][0-9])px", css, re.S
        ), (
            "no rule gives the rename/release buttons a minimum width; at 360px "
            "the release button measures 22px"
        )

    def test_password_toggle_has_a_minimum_size(self):
        block = re.search(r"\.password-toggle\s*\{([^}]*)\}", read("App/static/css/pages.css"))
        assert block, "no .password-toggle rule found"
        body = block.group(1)
        w = re.search(r"min-width:\s*(\d+)px", body)
        h = re.search(r"min-height:\s*(\d+)px", body)
        assert w and int(w.group(1)) >= MIN_TARGET_PX, (
            f".password-toggle has no min-width >= {MIN_TARGET_PX}px (measured 18px)"
        )
        assert h and int(h.group(1)) >= MIN_TARGET_PX, (
            f".password-toggle has no min-height >= {MIN_TARGET_PX}px (measured 17px)"
        )


class TestMobileOnlyControlsAreAccessible:
    """Controls that only exist on a narrow viewport.

    This is the class of defect a desktop-only audit cannot see. T22 swept axe
    over 8 routes x 2 themes and reported zero violations — at 1280px, where
    `.navbar-toggler` is `display: none`. Re-running the identical sweep at
    375px surfaced a **critical** `button-name` on every single page: the burger
    is an empty <button> whose icon is a background image on a bare <span>.

    The lesson generalises past this one button, so the test does.
    """

    def test_the_navbar_toggler_has_an_accessible_name(self):
        html = read(TEMPLATES / "layout.html")
        button = re.search(r"<button[^>]*navbar-toggler[^>]*>", html)
        assert button, "no .navbar-toggler in layout.html"
        assert re.search(r'aria-label="[^"]+"', button.group(0)), (
            "the burger renders as an empty button — no text, only a background "
            f"image — so it announces nothing: {button.group(0)[:120]}"
        )

    def test_the_navbar_toggler_reports_its_state(self):
        """`aria-expanded` is what tells a user the menu opened."""
        html = read(TEMPLATES / "layout.html")
        button = re.search(r"<button[^>]*navbar-toggler[^>]*>", html).group(0)
        assert "aria-expanded" in button, "burger never reports open/closed state"
        assert 'aria-controls="navbarNav"' in button, (
            "burger does not say which region it controls"
        )

    def test_the_burger_icon_is_hidden_from_the_a11y_tree(self):
        """A decorative span next to a real label would be read twice."""
        html = read(TEMPLATES / "layout.html")
        icon = re.search(r"<span[^>]*navbar-toggler-icon[^>]*>", html)
        assert icon and "aria-hidden" in icon.group(0), (
            f"the burger icon is not aria-hidden: {icon.group(0) if icon else 'missing'}"
        )


class TestResponsivePrimitivesSurvive:
    """Guards on the things that were already right, so they stay right.

    These passed at baseline. They are here because T23 edits the same files,
    and a grid that stops collapsing is exactly the kind of regression that
    shows up only on a phone.
    """

    def test_the_dashboard_grid_still_auto_collapses(self):
        css = read(STYLES_CSS)
        grid = re.search(r"\.dashboard-grid\s*\{([^}]*)\}", css)
        assert grid, "no .dashboard-grid rule"
        assert "auto-fit" in grid.group(1) and "minmax" in grid.group(1), (
            "the dashboard grid no longer uses auto-fit/minmax, so it will not "
            f"collapse to one column on a phone: {grid.group(1).strip()}"
        )

    def test_the_matchup_table_keeps_its_own_scroller(self):
        """18x18 cells. Without this the body scrolls sideways."""
        css = read(STYLES_CSS)
        wrapper = re.search(r"\.matchup-grid-wrapper\s*\{([^}]*)\}", css)
        assert wrapper, "no .matchup-grid-wrapper rule"
        assert re.search(r"overflow-x:\s*(auto|scroll)", wrapper.group(1)), (
            "the 18x18 type matrix lost its horizontal scroller"
        )

    def test_the_scrollable_matrix_is_keyboard_reachable(self):
        """A scroller nobody can focus hides its overflow from keyboard users.

        axe reports `scrollable-region-focusable`. Found late, because the
        matrix only renders after a team is generated — so an audit of the
        page at rest never sees it.
        """
        js = read("App/static/js/ml_playground.js")
        wrapper = re.search(r"matchup-grid-wrapper[^']*'", js)
        assert wrapper, "the matchup wrapper is no longer built in JS"
        window = js[max(0, wrapper.start() - 200):wrapper.end() + 200]
        assert 'tabindex="0"' in window, (
            "the horizontally-scrolling type matrix is not focusable, so a "
            "keyboard user cannot scroll it"
        )

    def test_the_viewport_meta_allows_zoom(self):
        """`user-scalable=no` / `maximum-scale=1` block pinch-zoom.

        Neither is present today; this keeps it that way, because it is the
        single most common way a responsive pass breaks low-vision users.
        """
        layout = read(TEMPLATES / "layout.html")
        meta = re.search(r'<meta[^>]*name="viewport"[^>]*>', layout)
        assert meta, "layout.html has no viewport meta"
        assert "user-scalable=no" not in meta.group(0)
        assert not re.search(r"maximum-scale=\s*1", meta.group(0))
