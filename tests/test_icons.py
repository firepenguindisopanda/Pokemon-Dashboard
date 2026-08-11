"""Every icon the app asks for must actually exist.

Four of them did not. `fa-pokeball`, `fa-analytics`, `fa-chart-radar` and
`fa-chart-scatter` are Font Awesome **Pro** names, and layout.html loads the
**free** build — so each rendered as nothing at all. No console error, no
fallback glyph, no layout shift worth noticing: just an empty space where the
brand mark should be, on every page, including the login screen.

That is why this is a test and not a fix. A missing icon is invisible to axe
(it has no accessible name to check — they are all `aria-hidden`), invisible to
the template tests, and invisible to a person who has never seen the intended
design. The only thing that catches it is comparing the names used against the
names that exist.

`fa-pokeball` has no free equivalent, so the brand mark is an inline SVG in
`_macros.html` instead.
"""

import re
from pathlib import Path

import pytest

FIXTURE = Path("tests/fixtures/fontawesome-6.4.0-free-icons.txt")
LAYOUT = Path("App/templates/layout.html")
SOURCES = sorted(
    list(Path("App/templates").rglob("*.html")) + list(Path("App/static/js").rglob("*.js"))
)

# `fa-` prefixed classes that size, style or animate rather than name a glyph.
NOT_GLYPHS = {
    "solid", "regular", "brands", "fw", "border", "inverse", "stack", "ul", "li",
    "pull-left", "pull-right", "spin", "pulse", "spin-pulse", "spin-reverse",
    "beat", "fade", "bounce", "flip", "shake", "beat-fade",
    "rotate-90", "rotate-180", "rotate-270", "rotate-by",
    "flip-horizontal", "flip-vertical", "flip-both",
    "2xs", "xs", "sm", "lg", "xl", "2xl",
    "1x", "2x", "3x", "4x", "5x", "6x", "7x", "8x", "9x", "10x",
}


def available():
    lines = FIXTURE.read_text(encoding="utf8").splitlines()
    return {line.strip() for line in lines
            if line.strip() and not line.startswith("#")}


def icons_used():
    """{icon name: [files]} for every `fa-<name>` in a template or script."""
    found = {}
    for path in SOURCES:
        text = path.read_text(encoding="utf8")
        # Comments explaining a removed icon must not count as a usage — the
        # same trap that has now caught three guards in this project.
        text = re.sub(r"{#.*?#}", "", text, flags=re.S)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for name in re.findall(r"\bfa-([a-z0-9-]+)\b", text):
            if name in NOT_GLYPHS:
                continue
            found.setdefault(name, []).append(path.name)
    return found


class TestTheIconSetIsReal:
    def test_the_fixture_matches_the_pinned_version(self):
        """The fixture is only meaningful for the build actually loaded."""
        version = re.search(r"font-awesome/([\d.]+)/css/all\.min\.css",
                            LAYOUT.read_text(encoding="utf8"))
        assert version, "layout.html no longer loads Font Awesome from cdnjs"
        assert version.group(1) in FIXTURE.name, (
            f"layout.html pins Font Awesome {version.group(1)} but the fixture "
            f"is {FIXTURE.name}. Regenerate it — the header says how."
        )

    def test_the_fixture_looks_sane(self):
        icons = available()
        assert len(icons) > 2000, f"only {len(icons)} icons in the fixture"
        for known in ("circle", "user", "chart-line"):
            assert known in icons, f"fa-{known} missing — fixture is malformed"

    def test_every_icon_used_exists_in_the_free_build(self):
        icons, missing = available(), {}
        for name, files in icons_used().items():
            if name not in icons:
                missing[name] = sorted(set(files))
        assert not missing, (
            "these icon names have no glyph in Font Awesome 6.4.0 free and "
            "render as nothing:\n  "
            + "\n  ".join(f"fa-{n} — used in {', '.join(f)}"
                          for n, f in sorted(missing.items()))
        )

    @pytest.mark.parametrize("pro_only", [
        "pokeball", "analytics", "chart-radar", "chart-scatter",
    ])
    def test_the_four_pro_names_are_gone(self, pro_only):
        """Named individually so a regression says which one came back."""
        assert pro_only not in icons_used(), (
            f"fa-{pro_only} is a Font Awesome Pro icon; the free build renders "
            f"it as an empty box"
        )


class TestTheBrandMarkIsAnInlineSvg:
    """`fa-pokeball` has no free equivalent, so it is drawn rather than fetched."""

    def test_the_macro_exists(self):
        macros = Path("App/templates/_macros.html").read_text(encoding="utf8")
        assert "macro pokeball_icon" in macros, "no pokeball_icon macro"
        assert "<svg" in macros, "the macro emits no SVG"

    def test_it_inherits_the_surrounding_colour(self):
        """`currentColor` is what makes it safe everywhere.

        The mark sits on the red navbar, on a white card and inside a red
        circle. A hard-coded fill would need a contrast decision per site;
        inheriting the text colour needs none, because the text beside it has
        already been checked.
        """
        macros = Path("App/templates/_macros.html").read_text(encoding="utf8")
        block = re.search(r"{%-? macro pokeball_icon.*?{%-? endmacro -?%}", macros, re.S)
        assert block, "could not isolate the pokeball_icon macro"
        body = block.group(0)
        assert "currentColor" in body, "the icon does not inherit currentColor"
        hard_coded = re.findall(r"(?:fill|stroke)=\"(#[0-9a-fA-F]{3,6})\"", body)
        assert not hard_coded, (
            f"the icon hard-codes {hard_coded}; it must inherit currentColor so "
            f"it cannot fail contrast on a background it was not designed for"
        )

    def test_it_is_hidden_from_assistive_technology(self):
        """Decorative in both places it appears — the brand text says the name."""
        macros = Path("App/templates/_macros.html").read_text(encoding="utf8")
        block = re.search(r"{%-? macro pokeball_icon.*?{%-? endmacro -?%}", macros, re.S)
        assert 'aria-hidden="true"' in block.group(0)
        assert 'focusable="false"' in block.group(0), (
            "IE/older Edge put SVGs in the tab order without focusable=false"
        )

    @pytest.mark.parametrize("template", ["layout.html", "login.html"])
    def test_both_old_sites_now_use_the_macro(self, template):
        html = Path(f"App/templates/{template}").read_text(encoding="utf8")
        assert "pokeball_icon(" in html, (
            f"{template} does not render the pokeball macro"
        )
