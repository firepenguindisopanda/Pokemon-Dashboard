"""T21 — de-inline the heavy templates.

`pokemon_ml.html` was 836 lines, 525 of them a single inline `<script>`.
`pokemon_dashboard.html` carried another 106. Four templates held page CSS in
an inline `{% block styles %}`. None of it was cacheable, lintable, or
reachable from more than one page.

The risk this task carries is not "did the JS move" — it is the one T20 hit:
**an inline `<style>` block is implicitly scoped to one page; a shared
stylesheet is not.** Moving `.chart-container` out of `pokemon_piechart.html`
verbatim would have restyled all four charts on `/pokemon-stats`, because
`styles.css:451` defines that class too and a later sheet wins.

So the guards here are mostly about scoping, and the structural one is
`TestPageStylesCannotLeak`: every selector in `pages.css` must begin with a
page-root class. That makes the leak impossible to reintroduce by accident
rather than merely absent today.

The end-to-end proof is `tasks/page-baseline.py` plus computed styles checked
in a browser on the pages this task did *not* mean to touch. Neither of those
can live in pytest; these tests cover the pieces that can.
"""

import json
import re
from pathlib import Path

import pytest

TEMPLATES = Path("App/templates")
STATIC = Path("App/static")

STYLES_CSS = STATIC / "css/styles.css"
COMPONENTS_CSS = STATIC / "css/components.css"
PAGES_CSS = STATIC / "css/pages.css"

ML_JS = STATIC / "js/ml_playground.js"
DASHBOARD_CHARTS_JS = STATIC / "js/dashboard_charts.js"
PIECHART_JS = STATIC / "js/piechart.js"

# The acceptance criterion is "no template holds >~20 lines of inline CSS".
MAX_INLINE_CSS_LINES = 20

# Templates whose inline <script> bodies T21 extracts. home.html (168 lines),
# signup.html (105) and quiz.html (65) also carry inline JS, but they are not
# in this task's scope and are deliberately left alone.
DE_INLINED_TEMPLATES = [
    "pokemon_ml.html",
    "pokemon_dashboard.html",
    "pokemon_piechart.html",
]


def read(path):
    return Path(path).read_text(encoding="utf8")


def inline_style_lines(template):
    """Non-blank lines inside a template's `{% block styles %}`."""
    block = re.search(r"{% block styles %}(.*?){% endblock %}", read(template), re.S)
    if not block:
        return []
    return [line for line in block.group(1).splitlines() if line.strip()]


def inline_script_bodies(template):
    """Bodies of every `<script>` in a template that is not a `src=` include.

    `type="application/json"` blocks are excluded: they are data, not code, and
    server-rendered data is the one thing that genuinely cannot move into a
    static file.
    """
    bodies = []
    for tag, body in re.findall(r"<script([^>]*)>(.*?)</script>", read(template), re.S):
        if "src=" in tag or "application/json" in tag:
            continue
        bodies.append(body)
    return bodies


def _strip_keyframes(css):
    """Remove whole `@keyframes` blocks, braces and all.

    Their `0%` / `100%` steps sit in selector position but are not selectors,
    and both stylesheets define animations — so left in, every sheet "collides"
    with every other on `0%` and the leak check is worthless. Brace matching is
    manual because the block nests one level.
    """
    out, i = [], 0
    for match in re.finditer(r"@keyframes[^{]*\{", css):
        if match.start() < i:
            continue
        out.append(css[i:match.start()])
        depth, j = 1, match.end()
        while depth and j < len(css):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        i = j
    out.append(css[i:])
    return "".join(out)


def top_level_selectors(path):
    """Every comma-separated selector at the head of a rule block.

    Comments go first, so a selector named in prose is not read as a
    definition. `@media` heads are dropped but their contents kept: a `.foo`
    inside a media query still targets `.foo` and can still leak.
    """
    css = re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)
    css = _strip_keyframes(css)
    css = re.sub(r"@media[^{]*\{", "", css)
    return {
        part.strip()
        for block in re.findall(r"([^{}]+)\{", css)
        for part in block.split(",")
        if part.strip() and not part.strip().startswith("@")
    }


class TestTemplatesAreDeInlined:
    def test_pokemon_ml_is_under_300_lines(self):
        actual = len(read(TEMPLATES / "pokemon_ml.html").splitlines())
        assert actual < 300, (
            f"pokemon_ml.html is {actual} lines; T21's budget is under 300"
        )

    @pytest.mark.parametrize(
        "name", sorted(p.name for p in TEMPLATES.glob("*.html"))
    )
    def test_no_template_holds_more_than_20_lines_of_inline_css(self, name):
        lines = inline_style_lines(TEMPLATES / name)
        assert len(lines) <= MAX_INLINE_CSS_LINES, (
            f"{name} holds {len(lines)} lines of inline CSS; move them into a "
            f"stylesheet, scoped under the page's root class"
        )

    @pytest.mark.parametrize("name", DE_INLINED_TEMPLATES)
    def test_de_inlined_templates_hold_no_inline_script_code(self, name):
        bodies = inline_script_bodies(TEMPLATES / name)
        code = [b for b in bodies if b.strip()]
        assert not code, (
            f"{name} still holds {len(code)} inline <script> block(s) totalling "
            f"{sum(len(b.splitlines()) for b in code)} lines. Server-rendered "
            f'data belongs in a <script type="application/json"> island; the '
            f"code that reads it belongs in static/js/."
        )

    @pytest.mark.parametrize(
        "script,template",
        [
            (ML_JS, "pokemon_ml.html"),
            (DASHBOARD_CHARTS_JS, "pokemon_dashboard.html"),
            (PIECHART_JS, "pokemon_piechart.html"),
        ],
    )
    def test_each_extracted_script_exists_and_is_loaded(self, script, template):
        assert script.exists(), f"{script} was never created"
        assert script.name in read(TEMPLATES / template), (
            f"{script.name} exists but {template} never loads it"
        )


class TestServerDataIsPassedAsJson:
    """The one thing that cannot move into a static file is Jinja output.

    Each de-inlined page emits its server data as a JSON island and the
    external script parses it, so the template holds data and no logic.
    """

    @pytest.mark.parametrize(
        "template,element_id",
        [
            ("pokemon_ml.html", "type-colors-data"),
            ("pokemon_dashboard.html", "type-colors-data"),
            ("pokemon_piechart.html", "piechart-data"),
        ],
    )
    def test_page_emits_a_json_island(self, template, element_id):
        html = read(TEMPLATES / template)
        island = re.search(
            rf'<script[^>]*id="{element_id}"[^>]*type="application/json"[^>]*>',
            html,
        )
        assert island, f"{template} emits no #{element_id} JSON island"

    @pytest.mark.parametrize(
        "template", ["pokemon_ml.html", "pokemon_dashboard.html", "pokemon_piechart.html"]
    )
    def test_json_islands_use_tojson(self, template):
        """`| tojson` escapes `<` to `\\u003c`, so a value cannot close the tag.

        Interpolating a Python dict with `{{ x }}` would emit `'` quotes and
        raw `<`, which is both invalid JSON and a way out of the script block.
        """
        for body in re.findall(
            r"<script[^>]*application/json[^>]*>(.*?)</script>",
            read(TEMPLATES / template),
            re.S,
        ):
            assert "tojson" in body, (
                f"{template} has a JSON island that does not use | tojson: {body.strip()[:80]}"
            )


class TestPageStylesCannotLeak:
    """T20's lesson, encoded structurally rather than case by case.

    An inline `<style>` block only ever applied to one page. `pages.css`
    applies to all nine, so every rule it holds must name the page it came
    from. `.chart-container` is the concrete reason: `styles.css:451` sizes the
    dashboard's four chart boxes, and the piechart page's inline copy set
    `width: 80%`. Moved out verbatim, it would have won on both pages.
    """

    # Root classes that scope a page's own styles. Each is on the outermost
    # element of exactly one template.
    PAGE_ROOTS = {
        ".login-page",
        ".signup-page",
        ".pokemon-area-page",
        ".pokemon-detail-page",
        ".piechart-page",
    }

    # Selectors that are deliberately not page-scoped, with the reason.
    LAYOUT_UTILITIES = {
        # Set on layout.html's <main>, which is an *ancestor* of every page
        # root, so it cannot be scoped under one. Opt-in per page via
        # {% block main_class %}.
        ".main-stretch",
    }

    def test_pages_stylesheet_exists_and_is_linked(self):
        assert PAGES_CSS.exists(), f"{PAGES_CSS} was never created"
        assert "pages.css" in read(TEMPLATES / "layout.html"), (
            "pages.css exists but no page loads it"
        )

    def test_every_selector_is_scoped_to_one_page(self):
        unscoped = sorted(
            sel
            for sel in top_level_selectors(PAGES_CSS)
            if sel not in self.LAYOUT_UTILITIES
            and not any(sel.startswith(root) for root in self.PAGE_ROOTS)
        )
        assert not unscoped, (
            "pages.css holds selectors that are not scoped to a page root "
            f"{sorted(self.PAGE_ROOTS)}: {unscoped}. Unscoped, they apply to all "
            "nine pages — which is exactly how T20 restyled /pokemon-stats."
        )

    @pytest.mark.parametrize("sheet", [COMPONENTS_CSS, PAGES_CSS])
    def test_no_bare_element_selectors(self, sheet):
        """`main {}` and `td {}` were both moved out of page style blocks.

        Neither carries a class, so in a shared sheet they restyle every page.
        `main` in particular is on all nine, via layout.html.
        """
        bare = sorted(
            sel
            for sel in top_level_selectors(sheet)
            if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]*", sel)
        )
        assert not bare, (
            f"{sheet.name} defines bare element selectors {bare}, which match "
            "on every page that loads it"
        )

    @pytest.mark.parametrize("sheet", [COMPONENTS_CSS, PAGES_CSS])
    def test_no_selector_is_shared_with_the_global_stylesheet(self, sheet):
        collisions = sorted(top_level_selectors(STYLES_CSS) & top_level_selectors(sheet))
        assert not collisions, (
            f"{sheet.name} redefines selectors that styles.css also defines, and "
            f"loads afterwards so it wins everywhere: {collisions}"
        )

    def test_components_and_pages_do_not_collide_with_each_other(self):
        collisions = sorted(
            top_level_selectors(COMPONENTS_CSS) & top_level_selectors(PAGES_CSS)
        )
        assert not collisions, (
            f"components.css and pages.css both define {collisions}; whichever "
            "loads last wins and the other is silently dead"
        )

    def test_the_dashboard_chart_container_is_not_redefined(self):
        """The specific rule that would have leaked, pinned by name.

        `.chart-container` sizes four canvases on /pokemon-stats. The piechart
        page's `width: 80%; margin: 0 auto` must reach it only through
        `.piechart-page`.
        """
        for sheet in (COMPONENTS_CSS, PAGES_CSS):
            assert ".chart-container" not in top_level_selectors(sheet), (
                f"{sheet.name} defines .chart-container unscoped; styles.css "
                "uses that class for the dashboard's chart boxes"
            )


class TestPagesStillRender:
    """Rendering catches what static analysis cannot: a template that no longer
    parses, a macro import that was dropped, or a JSON island that is not JSON.
    """

    ROUTES = [
        "/app",
        "/pokemon-area",
        "/pokemon-area/pokemon-details/1",
        "/quiz",
        "/pokemon-stats",
        "/pokemon-ml",
        "/pokemon-piechart",
    ]

    @pytest.mark.parametrize("route", ROUTES)
    def test_route_returns_200(self, auth_client, route):
        assert auth_client.get(route).status_code == 200

    @pytest.mark.parametrize(
        "route,element_id",
        [
            ("/pokemon-ml", "type-colors-data"),
            ("/pokemon-stats", "type-colors-data"),
            ("/pokemon-piechart", "piechart-data"),
        ],
    )
    def test_json_island_renders_valid_json(self, auth_client, route, element_id):
        html = auth_client.get(route).get_data(as_text=True)
        island = re.search(
            rf'<script[^>]*id="{element_id}"[^>]*>(.*?)</script>', html, re.S
        )
        assert island, f"{route} rendered no #{element_id} island"
        payload = json.loads(island.group(1))
        assert payload, f"{route} rendered an empty #{element_id} island"


class TestDashboardUsesTheSkeletonMacro:
    """The `skeleton` macro shipped in T20 with no call site.

    Its `.skeleton-*` rules have been in styles.css since before that, styling
    markup nothing rendered. T20 could not adopt it because doing so changes
    the dashboard's loading state and T20's contract was that nothing changes.
    T21 is where it gets wired in.
    """

    def test_template_imports_and_calls_the_macro(self):
        """Checks the import and a call, not the word.

        Substring-matching "skeleton" passed with the macro fully removed,
        because the comment explaining the change still said it.
        """
        html = read(TEMPLATES / "pokemon_dashboard.html")
        assert re.search(r"{%\s*from\s+\"_macros.html\"\s+import\s+[^%]*skeleton", html), (
            "pokemon_dashboard.html does not import the skeleton macro"
        )
        assert re.search(r"{{\s*skeleton\(", html), (
            "pokemon_dashboard.html imports the skeleton macro but never calls it"
        )

    def test_loading_state_renders_skeleton_markup(self, auth_client):
        html = auth_client.get("/pokemon-stats").get_data(as_text=True)
        assert "skeleton-card" in html, (
            "/pokemon-stats renders no skeleton markup; the loading state is "
            "still the old spinner"
        )

    def test_the_old_spinner_is_gone(self, auth_client):
        html = auth_client.get("/pokemon-stats").get_data(as_text=True)
        assert "fa-cog fa-spin fa-3x" not in html, (
            "the cog spinner is still rendered alongside the skeleton"
        )


class TestPiechartRendersItsChart:
    """Pre-existing bug, confirmed against HEAD during T19's browser pass.

    `pokemon_piechart.html` called `new Chart(...)` but never loaded Chart.js,
    so the page 200'd with an empty canvas and a `ReferenceError` in the
    console. Folded into T21 because the template is being rewritten anyway.
    """

    def test_chartjs_is_loaded(self, auth_client):
        html = auth_client.get("/pokemon-piechart").get_data(as_text=True)
        assert re.search(r'<script[^>]+src="[^"]*[Cc]hart[^"]*\.min\.js"', html), (
            "/pokemon-piechart uses Chart but never loads the library"
        )

    def test_chartjs_loads_before_the_page_script(self, auth_client):
        """Order is the whole bug. A library that loads afterwards is no fix."""
        html = auth_client.get("/pokemon-piechart").get_data(as_text=True)
        library = re.search(r'<script[^>]+src="[^"]*[Cc]hart[^"]*\.min\.js"', html)
        page = html.find("piechart.js")
        assert library.start() < page, (
            "Chart.js is loaded after piechart.js, so Chart is still undefined "
            "when the page script runs"
        )


class TestLegacyStatsRouteIsGone:
    """`/pokemon-stats-v1` 500'd with `Object of type Undefined is not JSON
    serializable`: it fed `pokemon_dashboard.html` five variables that template
    stopped reading, and not the `type_colors` it needs. Nothing linked to it
    and `/pokemon-stats` supersedes it. Deleted on the maintainer's ruling.
    """

    def test_route_returns_404(self, auth_client):
        assert auth_client.get("/pokemon-stats-v1").status_code == 404

    def test_no_url_rule_maps_to_it(self):
        from App.app import app

        rules = [str(r) for r in app.url_map.iter_rules()]
        assert "/pokemon-stats-v1" not in rules, (
            f"the route is still registered: {[r for r in rules if 'v1' in r]}"
        )

    def test_the_handler_is_deleted(self):
        """Checks for the decorator, not the name.

        A comment recording why the route went is wanted, so a bare substring
        search for `pokemon-stats-v1` would fail on the documentation.
        """
        source = read("App/blueprints/analytics.py")
        assert 'route("/pokemon-stats-v1"' not in source
        assert "def pokemon_stats(" not in source
