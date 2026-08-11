"""Solid colours only, and a button palette that is one thing everywhere.

TWO MAINTAINER DECISIONS, WRITTEN DOWN AS TESTS

1. **No gradients.** Not in a stylesheet, not in a template's inline style, not
   built in JS. Solid fills only.

2. **Buttons may be restyled across all nine pages.** They had to be: a bare
   `.btn { background: var(--btn-gradient); color: white; border: none; }` in
   styles.css loaded after Bootstrap at equal specificity, so it won on order
   and *every* variant — `btn-danger`, `btn-warning`, `btn-outline-secondary` —
   rendered as the same blue. "Release" looked exactly like "Rename".

Bootstrap 5.3's own defaults do meet AA (4.50–4.69:1 with white), so letting
them through would have been safe. The palette here is chosen anyway, for two
reasons: those margins are razor-thin, and Bootstrap's blue `btn-primary` is
off-brand on a red site.
"""

import re
from pathlib import Path

import pytest

from tests.test_accessibility import contrast

CSS_FILES = sorted(Path("App/static/css").glob("*.css"))
TEMPLATES = sorted(Path("App/templates").glob("*.html"))
JS_FILES = sorted(Path("App/static/js").glob("*.js"))

WCAG_AA_NORMAL = 4.5

# The house palette. Every entry is (background, text) and every pair is
# asserted below rather than trusted — these numbers are the reason the values
# are what they are.
BUTTON_PALETTE = {
    ".btn-pokemon": ("#4361E6", "#FFFFFF"),
    ".btn-primary": ("#4361E6", "#FFFFFF"),
    ".btn-danger": ("#A32020", "#FFFFFF"),
    ".btn-success": ("#146C43", "#FFFFFF"),
    ".btn-secondary": ("#5A6169", "#FFFFFF"),
    ".btn-warning": ("#F9A825", "#212529"),
    ".btn-info": ("#0C7A8C", "#FFFFFF"),
}


def read(path):
    return path.read_text(encoding="utf8")


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


class TestNothingUsesAGradient:
    """The maintainer's call: solid colours only."""

    @pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
    def test_no_gradient_in_stylesheets(self, path):
        css = strip_comments(read(path))
        found = re.findall(r"\b\w*-?gradient\s*\(", css)
        assert not found, f"{path.name} still uses {sorted(set(found))}"

    @pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
    def test_no_gradient_in_templates(self, path):
        html = re.sub(r"{#.*?#}", "", read(path), flags=re.S)
        found = re.findall(r"\b\w*-?gradient\s*\(", html)
        assert not found, f"{path.name} has an inline {sorted(set(found))}"

    @pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
    def test_no_gradient_built_in_js(self, path):
        source = re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)
        found = re.findall(r"\b\w*-?gradient\s*\(", source)
        assert not found, f"{path.name} builds a {sorted(set(found))}"

    def test_no_token_is_still_named_gradient(self):
        """`--btn-gradient` held a solid colour since T22 flattened it.

        A token named "gradient" that must not be one is an invitation to put
        a gradient back into it.
        """
        css = strip_comments(read(Path("App/static/css/tokens.css")))
        named = re.findall(r"(--[\w-]*gradient[\w-]*)\s*:", css)
        assert not named, f"tokens still named for gradients: {sorted(set(named))}"


class TestTheButtonPaletteIsAccessible:
    @staticmethod
    def declared():
        """(background, colour) for each button rule in styles.css.

        Handles grouped selectors. `.btn-pokemon, .btn-primary { ... }` is one
        rule serving two names, and a parser that only matched
        `\\.btn-pokemon\\s*\\{` reported it as undeclared — a false failure that
        would have been "fixed" by duplicating the declaration.
        """
        css = strip_comments(read(Path("App/static/css/styles.css")))
        out = {}
        for heads, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            names = {part.strip() for part in heads.split(",")}
            wanted = names & set(BUTTON_PALETTE)
            if not wanted:
                continue
            bg = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,8})", body)
            fg = re.search(r"(?<!-)\bcolor:\s*(#[0-9a-fA-F]{3,8})", body)
            if bg and fg:
                for name in wanted:
                    out[name] = (bg.group(1).upper(), fg.group(1).upper())
        return out

    @pytest.mark.parametrize("selector", sorted(BUTTON_PALETTE))
    def test_variant_is_declared_and_meets_aa(self, selector):
        declared = self.declared()
        assert selector in declared, (
            f"{selector} declares no explicit background+colour pair, so its "
            f"contrast cannot be measured and Bootstrap's default applies"
        )
        bg, fg = declared[selector]
        expected_bg, expected_fg = BUTTON_PALETTE[selector]
        assert (bg, fg) == (expected_bg.upper(), expected_fg.upper()), (
            f"{selector} is {fg} on {bg}; the palette says "
            f"{expected_fg} on {expected_bg}"
        )
        ratio = contrast(fg, bg)
        assert ratio >= WCAG_AA_NORMAL, (
            f"{selector} is {ratio:.2f}:1 ({fg} on {bg})"
        )

    def test_the_bare_btn_rule_sets_no_colour(self):
        """The bug this whole change exists to fix.

        `.btn` and `.btn-danger` are both one class. styles.css loads after
        Bootstrap, so a `background` on the bare `.btn` beats every variant by
        source order — silently, and on all nine pages.
        """
        css = strip_comments(read(Path("App/static/css/styles.css")))
        block = re.search(r"(?<![\w-])\.btn\s*\{([^}]*)\}", css)
        assert block, "no .btn rule in styles.css"
        body = block.group(1)
        for prop in ("background", "color"):
            assert not re.search(rf"(?<!-)\b{prop}(?:-color)?:", body), (
                f".btn sets `{prop}`, which overrides every Bootstrap variant "
                f"at equal specificity: {body.strip()[:120]}"
            )


class TestNoButtonLosesItsColour:
    """Making `.btn` geometry-only leaves bare buttons unpainted.

    Five buttons relied on the old default. Each must now either carry a
    variant, the house class, or its own inline background.
    """

    VARIANTS = (
        "btn-pokemon", "btn-primary", "btn-secondary", "btn-success",
        "btn-danger", "btn-warning", "btn-info", "btn-light", "btn-dark",
        "btn-link", "btn-theme", "btn-close", "btn-outline",
    )

    @pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
    def test_every_button_is_painted(self, path):
        html = read(path)
        unpainted = []
        for tag in re.findall(r"<button[^>]*>", html):
            classes = re.search(r'class="([^"]*)"', tag)
            if not classes or "btn" not in classes.group(1).split():
                if not classes or "btn" not in classes.group(1):
                    continue
            names = classes.group(1)
            if any(v in names for v in self.VARIANTS):
                continue
            if re.search(r"style=\"[^\"]*background", tag):
                continue
            unpainted.append(tag[:110])
        assert not unpainted, (
            f"{path.name} has button(s) with no variant, no house class and no "
            f"inline background:\n  " + "\n  ".join(unpainted)
        )


class TestBadgesOnLightBackgroundsAreReadable:
    """Two contrast failures that no sweep had ever reached.

    Both need page state to appear, which is why ten axe runs missed them:

      - the Legendary badge only renders for a legendary Pokemon, and was
        white on #F9A825 — 1.63:1, papered over with a text-shadow
      - the detail header painted white text on the RAW type colour, which is
        fine for fire (#F08030) and unreadable for electric (#F8D030)
    """

    def test_the_legendary_modifier_wins_the_cascade(self):
        """Declaring the colour is not enough; it has to survive the cascade.

        The modifier was first written ABOVE `.type-badge-lg`, which sets
        `color: #fff` at the same specificity (both are two classes under the
        page root). Source order decided it and the badge stayed white — the
        browser caught this, the declaration-only test below did not, and axe
        stayed quiet because a `text-shadow` makes it report "incomplete"
        rather than a violation.

        This is the T22 `.type-badge` trap exactly: a second rule further down
        the file silently overriding authored dark text.
        """
        css = strip_comments(read(Path("App/static/css/pages.css")))
        base = css.find(".pokemon-detail-page .type-badge-lg {")
        modifier = css.find(".pokemon-detail-page .type-badge-lg--legendary {")
        assert base != -1 and modifier != -1, "one of the badge rules is missing"
        assert modifier > base, (
            "the --legendary modifier is declared before .type-badge-lg. Same "
            "specificity, so the base rule's `color: #fff` wins on order and "
            "the badge renders white on gold at 1.63:1"
        )

    def test_the_legendary_badge_uses_dark_text(self):
        css = strip_comments(read(Path("App/static/css/pages.css")))
        block = re.search(r"\.type-badge-lg--legendary\s*\{([^}]*)\}", css)
        assert block, "no .type-badge-lg--legendary rule"
        body = block.group(1)
        bg = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,8})", body)
        fg = re.search(r"(?<!-)\bcolor:\s*(#[0-9a-fA-F]{3,8})", body)
        assert bg and fg, f"legendary badge declares no colour pair: {body}"
        ratio = contrast(fg.group(1), bg.group(1))
        assert ratio >= WCAG_AA_NORMAL, (
            f"the Legendary badge is {ratio:.2f}:1 "
            f"({fg.group(1)} on {bg.group(1)})"
        )

    def test_the_detail_header_uses_the_aa_darkened_type_token(self):
        """`--type-<name>-text` is the type hue darkened until it reaches 4.5:1
        against white. Contrast is symmetric, so the same value used as a
        BACKGROUND carries white text at the same ratio — every type, no
        per-type special cases."""
        html = read(Path("App/templates/pokemon_area_details.html"))
        assert "--detail-accent" in html, "the header sets no accent variable"
        assert re.search(r"--detail-accent:\s*var\(--type-", html), (
            "the detail header still paints the raw type colour; white text on "
            "electric (#F8D030) is 1.66:1"
        )


class TestBootstrapUtilitiesThatAreGradients:
    """Some Bootstrap classes ARE gradients, under a name that does not say so.

    `.progress-bar-striped` draws its stripes with
    `linear-gradient(45deg, ...)`, and `.progress-bar-animated` slides that
    gradient. Grepping our own source for "gradient" never finds them — the
    quiz's progress bar was caught by reading computed styles in a browser,
    not by the scans above.
    """

    GRADIENT_UTILITIES = ["progress-bar-striped", "progress-bar-animated"]

    @pytest.mark.parametrize("utility", GRADIENT_UTILITIES)
    def test_no_template_uses_it(self, utility):
        """Jinja comments are stripped first, as in the scans above.

        Without that this fails on the comment in quiz.html that explains why
        the class was removed — the third time this session a guard tripped
        over its own documentation, after T21's deleted-handler check and
        T25's orphan check.
        """
        offenders = [
            p.name for p in TEMPLATES
            if utility in re.sub(r"{#.*?#}", "", read(p), flags=re.S)
        ]
        assert not offenders, (
            f"{offenders} use .{utility}, which Bootstrap implements as a "
            f"linear-gradient"
        )
