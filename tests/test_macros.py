"""T20 — shared Jinja components.

The Pokémon card, type badge, stat bar, section header and loading skeleton
were each re-implemented per template, with small drifts between copies. These
tests pin the macro output exactly, because the acceptance criterion is that
the *rendered output is unchanged* — a macro that emits nearly-right markup is
worse than the duplication it replaces.

The end-to-end proof that nothing moved is `tasks/page-baseline.py`, which
diffs the full rendered HTML of all 9 pages. These tests cover the pieces.
"""

from pathlib import Path

import pytest

from App.app import app as flask_app

MACROS = Path("App/templates/_macros.html")
COMPONENTS_CSS = Path("App/static/css/components.css")

# Templates expected to consume the macros rather than hand-rolling the markup.
CONSUMING_TEMPLATES = [
    "App/templates/home.html",
    "App/templates/pokemon_area.html",
    "App/templates/pokemon_area_details.html",
    "App/templates/login.html",
    "App/templates/signup.html",
]


def render_macro(call, **context):
    """Render a single macro call in isolation, exactly as a template would."""
    source = '{% from "_macros.html" import ' + call["import"] + " %}" + call["body"]
    with flask_app.app_context():
        return flask_app.jinja_env.from_string(source).render(**context)


class TestMacroFileExists:
    def test_macros_template_exists(self):
        assert MACROS.exists(), f"{MACROS} was never created"

    @pytest.mark.parametrize(
        "name",
        ["pokemon_card", "type_badge", "stat_bar", "section_header", "skeleton"],
    )
    def test_every_required_macro_is_defined(self, name):
        content = MACROS.read_text(encoding="utf8")
        assert f"macro {name}(" in content, f"_macros.html defines no {name} macro"

    def test_components_stylesheet_exists(self):
        assert COMPONENTS_CSS.exists(), f"{COMPONENTS_CSS} was never created"

    def test_components_stylesheet_is_linked_by_the_layout(self):
        layout = Path("App/templates/layout.html").read_text(encoding="utf8")
        assert "components.css" in layout, (
            "components.css exists but no page loads it"
        )


class TestComponentStylesDoNotLeak:
    """components.css loads after styles.css, so a bare selector wins globally.

    `.stat-label` and `.stat-value` exist in both files with different values:
    the stat bar wants a 0.8rem grey label, the analytics dashboard's stat
    cards want 0.9rem uppercase. Moving the stat-bar rules out of the detail
    page's inline <style> and into a late-loading global sheet silently
    restyled the dashboard until they were scoped under `.stat-row`.
    """

    def _top_level_selectors(self, path):
        import re

        css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf8"), flags=re.S)
        return {
            part.strip()
            for block in re.findall(r"([^{}]+)\{", css)
            for part in block.split(",")
            if part.strip() and not part.strip().startswith("@")
        }

    def test_no_selector_is_shared_with_the_global_stylesheet(self):
        styles = self._top_level_selectors(Path("App/static/css/styles.css"))
        components = self._top_level_selectors(COMPONENTS_CSS)
        collisions = styles & components
        assert not collisions, (
            "components.css redefines selectors that styles.css also defines, "
            f"and loads afterwards so it wins everywhere: {sorted(collisions)}. "
            "Scope them under their component's root class."
        )

    def test_stat_bar_rules_are_scoped_to_their_component(self):
        """Matches a selector standing alone at the start of a line.

        A substring check would also match `.stat-row .stat-label {`, which is
        the scoped form this test exists to require.
        """
        import re

        css = COMPONENTS_CSS.read_text(encoding="utf8")
        for bare in (".stat-label", ".stat-value"):
            unscoped = re.search(rf"^{re.escape(bare)}\s*\{{", css, re.M)
            assert unscoped is None, (
                f"{bare} is defined unscoped in components.css; the dashboard's "
                "stat cards use that class for something else"
            )


class TestTypeBadgeOutput:
    """Six call sites across two templates, in four different shapes."""

    def test_plain_badge(self):
        html = render_macro(
            {"import": "type_badge", "body": "{{ type_badge('Grass') }}"}
        )
        assert html == '<span class="type-badge type-grass">Grass</span>'

    def test_badge_with_extra_classes(self):
        html = render_macro(
            {"import": "type_badge", "body": "{{ type_badge('Poison', extra='ms-1') }}"}
        )
        assert html == '<span class="type-badge type-poison ms-1">Poison</span>'

    def test_small_badge(self):
        html = render_macro(
            {"import": "type_badge", "body": "{{ type_badge('Fire', small=true) }}"}
        )
        assert html == '<span class="type-badge type-badge-sm type-fire">Fire</span>'

    def test_small_badge_with_extra_classes(self):
        html = render_macro(
            {
                "import": "type_badge",
                "body": "{{ type_badge('Water', small=true, extra='ms-1') }}",
            }
        )
        assert html == (
            '<span class="type-badge type-badge-sm type-water ms-1">Water</span>'
        )

    def test_the_type_class_is_lowercased(self):
        """The CSS selectors are lowercase; the display name is not."""
        html = render_macro(
            {"import": "type_badge", "body": "{{ type_badge('DRAGON') }}"}
        )
        assert 'type-dragon"' in html and ">DRAGON<" in html


class TestStatBarOutput:
    def test_stat_bar_matches_the_existing_markup(self):
        html = render_macro(
            {
                "import": "stat_bar",
                "body": "{{ stat_bar('HP', 45, 255, '#78C850') }}",
            }
        )
        assert '<span class="stat-label">HP</span>' in html
        assert '<div class="stat-bar-track">' in html
        # 18.0, not 18: Jinja's `round` returns a float and the original markup
        # interpolated it directly. Reproducing the float is the point — this
        # refactor is not allowed to change a single character of output.
        assert "width:18.0%" in html, f"percentage wrong: {html}"
        assert "background:#78C850" in html
        # T22 split fill colour from text colour. The bar keeps the vivid type
        # colour (correct behind an 8px bar); the number no longer takes it,
        # because #78C850 on white is 2.06:1. With no `type_name` the value
        # inherits the surrounding text colour rather than an unreadable one.
        assert '<span class="stat-value">45</span>' in html
        assert 'style="color:#78C850' not in html

    def test_stat_bar_colours_the_value_with_the_accessible_token(self):
        """T22: `type_name` opts the number into the AA-darkened token."""
        html = render_macro(
            {
                "import": "stat_bar",
                "body": "{{ stat_bar('HP', 45, 255, '#78C850', type_name='grass') }}",
            }
        )
        assert '<span class="stat-value type-text-grass">45</span>' in html
        # The fill is unchanged — only the text moved.
        assert "background:#78C850" in html

    def test_stat_bar_supports_the_dimmed_total_row(self):
        html = render_macro(
            {
                "import": "stat_bar",
                "body": (
                    "{{ stat_bar('TOTAL', 318, 1530, '#78C850',"
                    " opacity=0.6, label_width='50px') }}"
                ),
            }
        )
        assert "opacity:0.6" in html
        assert 'style="min-width:50px;"' in html

    def test_stat_bar_row_classes_are_overridable(self):
        """The TOTAL row carries a top border the per-stat rows do not."""
        html = render_macro(
            {
                "import": "stat_bar",
                "body": (
                    "{{ stat_bar('TOTAL', 1, 2, '#000',"
                    " row_classes='stat-row border-top pt-2 mt-2') }}"
                ),
            }
        )
        assert html.startswith('<div class="stat-row border-top pt-2 mt-2">')

    def test_percentage_is_rounded_the_same_way_as_before(self):
        """Pinned against the real captured output, not against what looks right.

        `tasks/page-baseline/before1/pokemon-area-details.html` contains
        `width:19.0%` for Bulbasaur's attack, so that is the contract.
        """
        html = render_macro(
            {"import": "stat_bar", "body": "{{ stat_bar('ATK', 49, 255, '#000') }}"}
        )
        assert "width:19.0%" in html, html


class TestSectionHeaderOutput:
    def test_header_with_icon_and_accent(self):
        """T22 replaced the `color` hex argument with a `color_class`.

        A raw type colour as heading text is 2.06:1 at worst. The class resolves
        to the same hue darkened to AA, and moving it out of a style attribute
        means a caller can no longer pass an arbitrary failing colour.
        """
        html = render_macro(
            {
                "import": "section_header",
                "body": "{{ section_header('About', icon='fa-info-circle', "
                        "color_class='type-text-grass') }}",
            }
        )
        assert html == (
            '<h5 class="fw-bold mb-3 type-text-grass">'
            '<i class="fas fa-info-circle me-2"></i>About</h5>'
        )

    def test_header_without_icon_or_accent(self):
        """login.html and signup.html use the bare form."""
        html = render_macro(
            {
                "import": "section_header",
                "body": "{{ section_header('Welcome Back!', level='h4', classes='fw-bold mb-1') }}",
            }
        )
        assert html == '<h4 class="fw-bold mb-1">Welcome Back!</h4>'


class TestSkeletonOutput:
    def test_skeleton_uses_the_existing_css_classes(self):
        """`.skeleton-*` already exists in styles.css with no markup using it."""
        html = render_macro({"import": "skeleton", "body": "{{ skeleton() }}"})
        assert 'class="skeleton-card"' in html
        assert 'class="skeleton-img"' in html
        assert "skeleton-text" in html


class TestPokemonCardOutput:
    def test_card_renders_sprite_name_number_and_badges(self):
        html = render_macro(
            {
                "import": "pokemon_card",
                "body": "{{ pokemon_card(p, '#78C850') }}",
            },
            p={
                "pokemon_id": 1,
                "name": "Bulbasaur",
                "pokedex_number": 1,
                "type1": "Grass",
                "type2": "Poison",
            },
        )
        assert "sprites/pokemon/1.png" in html
        assert "Bulbasaur" in html
        assert "#001" in html
        assert 'class="type-badge type-grass"' in html
        assert 'class="type-badge type-poison ms-1"' in html
        assert "/pokemon-area/pokemon-details/1" in html

    def test_card_omits_the_second_badge_when_there_is_no_second_type(self):
        html = render_macro(
            {"import": "pokemon_card", "body": "{{ pokemon_card(p, '#F08030') }}"},
            p={
                "pokemon_id": 4,
                "name": "Charmander",
                "pokedex_number": 4,
                "type1": "Fire",
                "type2": None,
            },
        )
        assert html.count("type-badge") == 1, "a missing type2 must not render a badge"


class TestTemplatesActuallyConsumeTheMacros:
    """Importing the macros but keeping the old markup would defeat the point."""

    def test_at_least_four_templates_import_the_macros(self):
        importers = [
            path
            for path in Path("App/templates").glob("*.html")
            if "_macros.html" in path.read_text(encoding="utf8")
        ]
        assert len(importers) >= 4, (
            f"only {len(importers)} templates import _macros.html: "
            f"{[p.name for p in importers]}"
        )

    @pytest.mark.parametrize("template", CONSUMING_TEMPLATES)
    def test_template_imports_the_macros(self, template):
        content = Path(template).read_text(encoding="utf8")
        assert "_macros.html" in content, f"{template} does not import the macros"

    @pytest.mark.parametrize(
        "template",
        ["App/templates/home.html", "App/templates/pokemon_area.html"],
    )
    def test_no_hand_rolled_type_badge_markup_remains(self, template):
        """The duplication has to be gone, not merely joined by a macro."""
        content = Path(template).read_text(encoding="utf8")
        assert 'class="type-badge type-{{' not in content, (
            f"{template} still builds type badges by hand"
        )

    def test_stat_bar_markup_is_no_longer_hand_rolled(self):
        content = Path("App/templates/pokemon_area_details.html").read_text(
            encoding="utf8"
        )
        assert '<div class="stat-bar-fill"' not in content, (
            "pokemon_area_details.html still builds stat bars by hand"
        )
