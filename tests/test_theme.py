"""T19 — design tokens and a theme toggle that works on every page.

`styles.css` carries two separate palettes: the Pokédex-red one used by the
layout and most pages, and a second glassmorphism set used by the analytics
dashboard and the ML playground. Only the second has dark values, and the
toggle that switches it exists on only 2 of the 9 pages. Enable dark mode and
the other 7 stay light.

These tests pin the three things that were actually broken: tokens exist in
both themes, the toggle is reachable everywhere, and the stored theme is
applied before the first paint rather than after DOMContentLoaded.
"""

import re
from pathlib import Path

import pytest

TOKENS_CSS = Path("App/static/css/tokens.css")
LAYOUT = Path("App/templates/layout.html")

# Every route that renders a full HTML page. `/pokemon-stats-v1` is excluded:
# it is a known 500 and is slated for deletion, not repair.
AUTHENTICATED_PAGES = [
    "/app",
    "/pokemon-area",
    "/pokemon-area/pokemon-details/1",
    "/quiz",
    "/pokemon-stats",
    "/pokemon-ml",
    "/pokemon-piechart",
]
PUBLIC_PAGES = ["/", "/signup"]
ALL_PAGES = PUBLIC_PAGES + AUTHENTICATED_PAGES


def head_of(html):
    """The <head> section only — where a no-flash script has to live."""
    match = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.S | re.I)
    return match.group(1) if match else ""


class TestTokensFile:
    """A token layer is what lets both palettes respond to one switch."""

    def test_tokens_css_exists(self):
        assert TOKENS_CSS.exists(), f"{TOKENS_CSS} was never created"

    @pytest.mark.parametrize(
        "family,probe",
        [
            ("color", "--color-"),
            ("spacing", "--space-"),
            ("type", "--font-"),
            ("radius", "--radius-"),
            ("shadow", "--shadow-"),
        ],
    )
    def test_every_token_family_is_defined(self, family, probe):
        content = TOKENS_CSS.read_text(encoding="utf8")
        assert probe in content, f"no {family} tokens ({probe}*) in tokens.css"

    def test_dark_values_are_defined_for_the_tokens(self):
        content = TOKENS_CSS.read_text(encoding="utf8")
        assert '[data-theme="dark"]' in content, (
            "tokens.css defines no dark theme, so the toggle has nothing to switch"
        )

    def test_the_pokedex_palette_has_dark_values_too(self):
        """The bug being fixed: only the second palette responded to the toggle.

        `--pokemon-bg` is the layout's page background, applied inline on
        <body>. Without a dark value the nav, footer and body of seven pages
        stay light no matter what the toggle says.
        """
        content = TOKENS_CSS.read_text(encoding="utf8")
        dark_block = content.split('[data-theme="dark"]', 1)[-1]
        assert "--pokemon-bg" in dark_block, (
            "--pokemon-bg has no dark value, so the layout stays light in dark mode"
        )


class TestTogglePresentOnEveryPage:
    def test_the_nine_pages_are_all_covered(self):
        """Guard against the list silently shrinking."""
        assert len(ALL_PAGES) == 9

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_public_pages_have_the_toggle(self, client, path):
        html = client.get(path).get_data(as_text=True)
        assert 'id="theme-toggle"' in html, f"no theme toggle on {path}"

    @pytest.mark.parametrize("path", AUTHENTICATED_PAGES)
    def test_authenticated_pages_have_the_toggle(self, auth_client, path):
        response = auth_client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
        html = response.get_data(as_text=True)
        assert 'id="theme-toggle"' in html, f"no theme toggle on {path}"

    @pytest.mark.parametrize("path", ["/pokemon-stats", "/pokemon-ml"])
    def test_pages_that_had_their_own_toggle_now_have_exactly_one(self, auth_client, path):
        """The layout supplies it now; a leftover would give two on one page."""
        html = auth_client.get(path).get_data(as_text=True)
        assert html.count('id="theme-toggle"') == 1, (
            f"{path} has {html.count('id=\"theme-toggle\"')} theme toggles — the "
            "per-page markup should have been removed when the layout gained one"
        )

    @pytest.mark.parametrize("path", ALL_PAGES)
    def test_every_page_links_the_token_stylesheet(self, auth_client, path):
        html = auth_client.get(path).get_data(as_text=True)
        assert "tokens.css" in html, f"{path} does not load tokens.css"


class TestNoFlashOfLightMode:
    """The stored theme must be applied before the browser paints.

    Both pages that had a toggle applied it from a `DOMContentLoaded` handler
    in a deferred script, which runs after first paint — so a user with dark
    stored saw a white flash on every navigation.
    """

    def test_layout_sets_the_theme_from_an_inline_head_script(self):
        head = head_of(LAYOUT.read_text(encoding="utf8"))
        assert "localStorage" in head and "data-theme" in head, (
            "the <head> has no inline script applying the stored theme, so the "
            "page paints light before the theme is applied"
        )

    def test_the_no_flash_script_is_not_deferred_or_async(self):
        head = head_of(LAYOUT.read_text(encoding="utf8"))
        theme_scripts = [
            block
            for block in re.findall(r"<script\b[^>]*>.*?</script>", head, re.S | re.I)
            if "data-theme" in block
        ]
        assert theme_scripts, "no theme script found in <head>"
        for block in theme_scripts:
            opening = block[: block.index(">") + 1]
            assert "defer" not in opening and "async" not in opening, (
                "the no-flash script must run synchronously; defer/async both "
                f"delay it past first paint: {opening}"
            )

    @pytest.mark.parametrize("path", ALL_PAGES)
    def test_the_theme_script_runs_before_any_page_content(self, auth_client, path):
        html = auth_client.get(path).get_data(as_text=True)
        head = head_of(html)
        assert "data-theme" in head, (
            f"{path} applies its theme after <head>, which means after first paint"
        )


class TestPerPageToggleMarkupIsGone:
    """Acceptance criterion: the duplicated markup is removed, not just hidden."""

    @pytest.mark.parametrize(
        "template",
        ["App/templates/pokemon_dashboard.html", "App/templates/pokemon_ml.html"],
    )
    def test_template_no_longer_declares_its_own_toggle(self, template):
        content = Path(template).read_text(encoding="utf8")
        assert 'id="theme-toggle"' not in content, (
            f"{template} still declares its own theme toggle; the layout provides it"
        )

    @pytest.mark.parametrize(
        "template",
        ["App/templates/pokemon_dashboard.html", "App/templates/pokemon_ml.html"],
    )
    def test_template_no_longer_wires_its_own_theme_init(self, template):
        content = Path(template).read_text(encoding="utf8")
        assert "localStorage.getItem('pokemon-theme')" not in content, (
            f"{template} still initialises the theme itself, which duplicates the "
            "layout's handler and reintroduces the post-paint flash"
        )
