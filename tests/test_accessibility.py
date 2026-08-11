"""T22 — accessibility pass.

The measurable half of the acceptance criteria lives here. The other half —
"axe-core zero critical violations on the 7 main routes in both themes" — needs
a real browser and is run by hand; `tasks/t22-axe.md` records the numbers.

What these tests are for is the part a browser pass cannot give you: a
*regression guard*. A contrast ratio fixed by hand today is one careless colour
edit away from being broken again, and nobody re-runs axe before every commit.

The baseline these were written against, measured in Chrome before any change:

    13 of 18 type badges below 4.5:1, the worst at 1.49:1 (electric)
    5,858 color-contrast nodes across 8 routes x 2 themes
    2 critical rules: select-name (10 nodes), button-name (4 nodes)

The 13 was a surprise: `styles.css` *authored* dark text for electric, ground,
ice and steel, but a second `.type-badge { color: white }` rule 400 lines later
overrode all four at equal specificity. Static reading of the file said 9 were
broken; the browser said 13. `test_no_duplicate_type_badge_colour` exists so
that specific trap cannot be reset.
"""

import re
from pathlib import Path

import pytest

from App.constants import TYPE_COLORS

TEMPLATES = Path("App/templates")
STYLES_CSS = Path("App/static/css/styles.css")
TOKENS_CSS = Path("App/static/css/tokens.css")
COMPONENTS_CSS = Path("App/static/css/components.css")
DASHBOARD_JS = Path("App/static/js/dashboard.js")

WCAG_AA_NORMAL = 4.5

# Every type-as-text site renders on white in BOTH themes — verified in the
# browser, not assumed. Bootstrap's .card never got a dark-theme background, so
# the card titles, section headers and stat values all sit on #FFFFFF whichever
# theme is active. That is why one darkened variant per type is enough instead
# of a light/dark pair. It is also a real dark-mode gap, recorded separately.
TEXT_ON = "#FFFFFF"


def read(path):
    return Path(path).read_text(encoding="utf8")


def _channel(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fg, bg):
    """WCAG 2.1 contrast ratio. Same formula axe-core uses."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def declared_type_badges():
    """`.type-x { background-color: …; color: … }` as authored in styles.css."""
    css = re.sub(r"/\*.*?\*/", "", read(STYLES_CSS), flags=re.S)
    found = {}
    for name, body in re.findall(r"\.type-([a-z]+)\s*\{([^}]*)\}", css):
        if name in ("badge", "badge-sm"):
            continue
        bg = re.search(r"background-color:\s*(#[0-9A-Fa-f]{3,6})", body)
        fg = re.search(r"(?<!-)\bcolor:\s*(#[0-9A-Fa-f]{3,6})", body)
        if bg and fg:
            found[name] = (bg.group(1), fg.group(1))
    return found


class TestContrastHelperIsCorrect:
    """The helper is the measuring instrument — pin it against known values.

    Every other test in this file is only as trustworthy as this one.
    """

    @pytest.mark.parametrize(
        "fg,bg,expected",
        [
            ("#000000", "#FFFFFF", 21.0),   # maximum possible
            ("#FFFFFF", "#FFFFFF", 1.0),    # minimum possible
            ("#777777", "#FFFFFF", 4.48),   # just under AA — a classic near-miss
            ("#767676", "#FFFFFF", 4.54),   # just over
        ],
    )
    def test_known_ratios(self, fg, bg, expected):
        assert contrast(fg, bg) == pytest.approx(expected, abs=0.01)

    def test_ratio_is_symmetric(self):
        assert contrast("#78C850", "#FFFFFF") == pytest.approx(
            contrast("#FFFFFF", "#78C850")
        )


class TestTypeBadgeContrast:
    """Acceptance: all 18 type badges meet 4.5:1."""

    def test_all_eighteen_types_are_styled(self):
        declared = declared_type_badges()
        missing = sorted(set(TYPE_COLORS) - set(declared))
        assert not missing, f"no badge rule for {missing}"
        assert len(declared) == 18, f"expected 18 badge rules, found {len(declared)}"

    @pytest.mark.parametrize("name", sorted(TYPE_COLORS))
    def test_badge_meets_aa(self, name):
        bg, fg = declared_type_badges()[name]
        ratio = contrast(fg, bg)
        assert ratio >= WCAG_AA_NORMAL, (
            f".type-{name} is {ratio:.2f}:1 ({fg} on {bg}); AA needs "
            f"{WCAG_AA_NORMAL}:1 for text this size"
        )

    def test_background_colours_are_unchanged(self):
        """The fix flips text colour only.

        The type backgrounds are the recognisable identity, and the spec is
        explicit that Phase 7 is not a redesign. If a later edit "fixes"
        contrast by shifting a background, this fails.
        """
        declared = declared_type_badges()
        drifted = {
            name: (declared[name][0].upper(), expected.upper())
            for name, expected in TYPE_COLORS.items()
            if name in declared and declared[name][0].upper() != expected.upper()
        }
        assert not drifted, (
            f"badge background colours drifted from App/constants.py: {drifted}"
        )

    # Classes worn *alongside* a `.type-<name>` class. Anything here that sets
    # its own `color` sits at the same specificity as the per-type rule and, in
    # this file, below it — so it wins and silently overrides all 18.
    BADGE_SHELL_CLASSES = [
        ".type-badge",
        ".type-badge-sm",
        ".cp-type-badge",
        # T26: found by an audit that finally reached the 18x18 matchup
        # matrix, which only renders after a team is generated.
        ".matchup-table .type-label",
    ]

    @pytest.mark.parametrize("shell", BADGE_SHELL_CLASSES)
    def test_badge_shell_classes_do_not_set_colour(self, shell):
        """The trap that made this worse than it looked, generalised.

        `.type-badge` was declared twice in styles.css. The second, 400 lines
        below the per-type rules, set `color: white` at equal specificity — so
        it won, and the dark text authored for electric/ground/ice/steel never
        rendered. Static analysis found 9 failures; the browser found 13.

        `.cp-type-badge` had exactly the same defect, found later in the same
        pass. Parametrised rather than hard-coded to `.type-badge`, because the
        bug is the pattern, not the one class.
        """
        css = re.sub(r"/\*.*?\*/", "", read(STYLES_CSS), flags=re.S)
        blocks = re.findall(rf"(?<![\w-]){re.escape(shell)}\s*\{{([^}}]*)\}}", css)
        with_colour = [b for b in blocks if re.search(r"(?<!-)\bcolor:", b)]
        assert not with_colour, (
            f"{shell} sets `color` in {len(with_colour)} rule(s). It must not: "
            "the per-type rules own the text colour, and this class overrides "
            "them all at equal specificity."
        )

    def test_scripts_build_badges_with_the_type_class(self):
        """A badge given an inline background inherits whatever text colour.

        The ML playground built its team chips as
        `<span class="type-badge" style="background-color: …">`, with no
        `.type-<name>` class. Once `.type-badge` stopped forcing white, those
        inherited the page text colour: 3.0:1 on fighting, 1.59:1 on grass in
        dark. The class carries a checked pair, an inline background does not.
        """
        js = read("App/static/js/ml_playground.js")
        # Covers the matchup-matrix cells too, not just badges: those carry the
        # same `color: white` over an inline type colour and were missed by the
        # original T22 sweep because the 18x18 table only renders after a team
        # is generated.
        offenders = re.findall(
            r'class=\\?"[^"\']*type-(?:badge|label)[^"\']*\\?"\s*style=', js
        )
        assert not offenders, (
            "a script builds a type-coloured element with an inline background, "
            f"so its text colour is whatever it inherits: {offenders}"
        )

    def test_badges_have_no_dark_text_shadow(self):
        """`text-shadow: …rgba(0,0,0,.3)` behind near-black text is mud.

        axe counts the shadow as a second foreground and flagged ~3,874 nodes
        on it. With 13 badges now carrying dark text the shadow is actively
        harmful, not merely redundant.
        """
        css = re.sub(r"/\*.*?\*/", "", read(STYLES_CSS), flags=re.S)
        for body in re.findall(r"(?<![\w-])\.type-badge\s*\{([^}]*)\}", css):
            shadow = re.search(r"text-shadow:\s*([^;]+);", body)
            assert shadow is None or "none" in shadow.group(1), (
                f".type-badge still sets text-shadow: {shadow.group(1).strip()}"
            )


class TestTypeColourUsedAsText:
    """The larger contrast cause, folded into T22 on the maintainer's ruling.

    The raw type colours are also used as *text* — Pokémon card titles, section
    headers, stat values — on white. `#78C850` on white is 2.06:1 and `#F8D030`
    is 1.49:1. Those need a darkened variant; the badge and gradient colours
    stay exactly as they are.
    """

    def test_every_type_has_a_text_token(self):
        css = read(TOKENS_CSS)
        missing = [t for t in sorted(TYPE_COLORS) if f"--type-{t}-text:" not in css]
        assert not missing, f"tokens.css defines no --type-*-text for {missing}"

    def _text_tokens(self):
        css = read(TOKENS_CSS)
        return dict(re.findall(r"--type-([a-z]+)-text:\s*(#[0-9A-Fa-f]{3,6})", css))

    @pytest.mark.parametrize("name", sorted(TYPE_COLORS))
    def test_text_token_meets_aa_on_white(self, name):
        token = self._text_tokens()[name]
        ratio = contrast(token, TEXT_ON)
        assert ratio >= WCAG_AA_NORMAL, (
            f"--type-{name}-text is {token}, {ratio:.2f}:1 on {TEXT_ON}"
        )

    @pytest.mark.parametrize("name", sorted(TYPE_COLORS))
    def test_text_token_keeps_the_type_recognisable(self, name):
        """Darkened, not replaced.

        A token that meets AA by going to #000 everywhere would pass the test
        above and destroy the type coding. Require the hue to stay close to the
        original.
        """
        import colorsys

        def hue(h):
            h = h.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            return colorsys.rgb_to_hls(r, g, b)[0]

        original = TYPE_COLORS[name]
        token = self._text_tokens()[name]
        delta = abs(hue(original) - hue(token))
        delta = min(delta, 1 - delta)  # hue is circular
        assert delta < 0.05, (
            f"--type-{name}-text ({token}) shifted hue by {delta:.3f} from "
            f"{original}; it should be the same colour, darker"
        )

    def test_macros_do_not_inline_any_interpolated_text_colour(self):
        """The macros must set text colour by class, not by interpolation.

        `pokemon_card` and `section_header` dropped a passed-in colour straight
        into `style="color: …"`, which is how 982 card titles ended up at
        2.06:1. Renaming the variable to a safe one would fix today's numbers
        and leave the mechanism — any caller could pass a failing colour again —
        so this checks for *any* interpolated `color:`, not one variable name.

        `background:` and `background-color:` are untouched: the vivid type
        colour is still correct behind a badge or inside a stat bar.
        """
        macros = read(TEMPLATES / "_macros.html")
        offenders = re.findall(r'style="[^"]*(?<![-\w])color:\s*\{\{[^}]*\}\}', macros)
        assert not offenders, (
            "_macros.html still interpolates a text colour instead of using a "
            f"--type-*-text class: {offenders}"
        )


class TestFormsHaveRealLabels:
    """Acceptance: real <label>s on all inputs.

    axe reported `select-name` as critical, 10 nodes across both themes: the ML
    playground's five selects carry a `.form-label` span-like <label> with no
    `for`, so nothing associates them.
    """

    CONTROL = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.I)

    def _controls(self, name):
        html = read(TEMPLATES / name)
        out = []
        for tag, attrs in self.CONTROL.findall(html):
            if re.search(r'type="(hidden|submit|button|reset)"', attrs, re.I):
                continue
            out.append((tag, attrs))
        return html, out

    @pytest.mark.parametrize(
        "name",
        ["login.html", "signup.html", "home.html", "pokemon_area.html", "pokemon_ml.html"],
    )
    def test_every_control_is_labelled(self, name):
        html, controls = self._controls(name)
        label_targets = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', html))
        unlabelled = []
        for tag, attrs in controls:
            if re.search(r'\baria-label(ledby)?="', attrs):
                continue
            ident = re.search(r'\bid="([^"]+)"', attrs)
            if ident and ident.group(1) in label_targets:
                continue
            unlabelled.append(f"<{tag} {attrs.strip()[:70]}>")
        assert not unlabelled, (
            f"{name} has {len(unlabelled)} control(s) with no <label for> and no "
            f"aria-label:\n  " + "\n  ".join(unlabelled)
        )

    def test_placeholder_is_not_the_only_name(self):
        """A placeholder is not a label — it vanishes on first keystroke.

        Several search and rename inputs relied on one entirely.
        """
        for name in ["home.html", "pokemon_area.html"]:
            html = read(TEMPLATES / name)
            for _tag, attrs in self.CONTROL.findall(html):
                if re.search(r'type="(hidden|submit|button|reset)"', attrs, re.I):
                    continue
                if "placeholder=" not in attrs:
                    continue
                has_name = re.search(r'\baria-label(ledby)?="', attrs) or (
                    (m := re.search(r'\bid="([^"]+)"', attrs))
                    and f'for="{m.group(1)}"' in html
                )
                assert has_name, (
                    f"{name}: control relies on placeholder alone: "
                    f"{attrs.strip()[:80]}"
                )


class TestGeneratedMarkupIsAccessible:
    """axe `button-name`, critical: the toast close button has no text.

    It is built in dashboard.js, so no template scan would ever find it.
    """

    def test_toast_close_button_has_an_accessible_name(self):
        js = read(DASHBOARD_JS)
        close = re.search(r'class="btn-close[^"]*"[^>]*', js)
        assert close, "could not find the toast close button in dashboard.js"
        assert "aria-label" in close.group(0), (
            "the toast close button renders as an empty <button> with no "
            f"accessible name: {close.group(0)[:90]}"
        )

    def test_toasts_announce_themselves(self):
        """A toast must carry a live-region role and an aria-live politeness.

        Matching the *values* rather than a literal call shape: the role is
        chosen per toast type (an error interrupts, a confirmation waits), so
        pinning `setAttribute('role', 'alert')` verbatim would reject the more
        correct implementation.
        """
        js = read(DASHBOARD_JS)
        role_call = re.search(r"setAttribute\(\s*['\"]role['\"]\s*,([^;]+)\);", js)
        assert role_call, "toasts set no role at all"
        assert re.search(r"['\"](alert|status)['\"]", role_call.group(1)), (
            f"toast role is not alert/status: {role_call.group(1).strip()}"
        )
        live_call = re.search(r"setAttribute\(\s*['\"]aria-live['\"]\s*,([^;]+)\);", js)
        assert live_call, "toasts set no aria-live, so nothing is announced"
        assert re.search(r"['\"](polite|assertive)['\"]", live_call.group(1)), (
            f"toast aria-live is not polite/assertive: {live_call.group(1).strip()}"
        )


class TestFocusIsVisible:
    """Acceptance: visible focus rings on every interactive element.

    Only `.theme-toggle-nav` had a `:focus-visible` rule, and `.form-control:focus`
    actively removed the outline (`outline: none`) in exchange for a box-shadow.
    """

    def test_a_global_focus_visible_rule_exists(self):
        css = read(TOKENS_CSS) + read(STYLES_CSS)
        assert re.search(r":focus-visible\s*\{[^}]*outline:", css), (
            "no :focus-visible rule sets an outline anywhere"
        )

    def test_no_rule_removes_focus_outline_without_replacing_it(self):
        """`outline: none` is only acceptable alongside a visible substitute."""
        css = re.sub(r"/\*.*?\*/", "", read(STYLES_CSS), flags=re.S)
        bad = []
        for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            if "focus" not in selector:
                continue
            if not re.search(r"outline:\s*(none|0)", body):
                continue
            if not re.search(r"box-shadow:|outline-offset:|border-color:", body):
                bad.append(selector.strip())
        assert not bad, (
            f"these focus rules remove the outline with no visible replacement: {bad}"
        )


class TestControlsAreKeyboardOperable:
    """Acceptance: arena, quiz and forms fully keyboard-navigable.

    Tab *order* is rarely the thing that breaks keyboard users — a `<div>` with
    an `onclick` is. It cannot be focused, so it cannot be reached or activated
    at all, and no amount of focus styling helps. Real `<button>`/`<a>` elements
    activate on Enter/Space for free.

    Verified in the browser across five routes at the time of writing: zero
    non-native click targets. This keeps it that way.
    """

    NATIVE = ("a", "button", "input", "select", "textarea", "summary")

    @pytest.mark.parametrize(
        "name", sorted(p.name for p in TEMPLATES.glob("*.html"))
    )
    def test_no_click_handler_on_a_non_interactive_element(self, name):
        html = read(TEMPLATES / name)
        offenders = []
        for tag, attrs in re.findall(r"<(\w+)([^>]*\bonclick=[^>]*)>", html):
            if tag.lower() in self.NATIVE:
                continue
            if re.search(r'\btabindex="(?!-1)', attrs):
                continue  # deliberately made focusable
            offenders.append(f"<{tag} {attrs.strip()[:70]}>")
        assert not offenders, (
            f"{name} puts a click handler on a non-interactive element, which "
            f"no keyboard user can reach:\n  " + "\n  ".join(offenders)
        )

    def test_icon_only_buttons_have_names(self):
        """An icon-only control announces nothing without an explicit name.

        The rename/release buttons repeat once per caught Pokémon, so they also
        need to say *which* one — every row otherwise gets the same name.
        """
        html = read(TEMPLATES / "home.html")
        for action in ("rename-", "release-"):
            for match in re.findall(rf"<button[^>]*form=\"{action}[^>]*>", html):
                assert "aria-label" in match, (
                    f"icon-only button has no accessible name: {match[:100]}"
                )


class TestAsyncRegionsAnnounce:
    """Acceptance: aria-live on async regions.

    Every one of these swaps its content in from a fetch with no announcement,
    so a screen-reader user is told nothing when the data lands.
    """

    @pytest.mark.parametrize(
        "template,element_id",
        [
            ("pokemon_dashboard.html", "overview-loading"),
            ("pokemon_ml.html", "analytics-status-banner"),
            ("pokemon_dashboard.html", "analytics-status-banner"),
        ],
    )
    def test_region_has_a_live_announcement(self, template, element_id):
        html = read(TEMPLATES / template)
        tag = re.search(rf'<[^>]*\bid="{element_id}"[^>]*>', html)
        assert tag, f"{template} has no #{element_id}"
        assert re.search(r'aria-live="|role="status"|role="alert"', tag.group(0)), (
            f"#{element_id} in {template} updates asynchronously but announces "
            f"nothing: {tag.group(0)[:110]}"
        )


class TestDarkThemeIsReadable:
    """Found while measuring: the ability chip is invisible in dark mode.

    `.ability-chip` keeps a hard-coded `#f0f0f0` background in both themes, but
    its text inherits `--text-primary`, which is `#E0E0FF` in dark. That is
    near-white on near-white — measured at roughly 1.05:1.
    """

    def test_ability_chip_sets_its_own_text_colour(self):
        css = read("App/static/css/pages.css")
        block = re.search(r"\.ability-chip\s*\{([^}]*)\}", css)
        assert block, "no .ability-chip rule in pages.css"
        assert re.search(r"(?<!-)\bcolor:", block.group(1)), (
            ".ability-chip sets a light background but no text colour, so in "
            "dark mode it inherits near-white text and disappears"
        )


class TestMatchupCellContrast:
    """The severity palette, which the 40/40 sweep never saw.

    Same blind spot as the 27 nodes T26 turned up: the matchup grid on
    /pokemon-ml only renders after a team is generated, so axe walked that page
    ten times without ever encountering a single cell. Measured by hand:

        immune     #FFF on #424242  10.05:1  ok
        resist-4x  #FFF on #1B5E20   7.87:1  ok
        resist-2x  #FFF on #4CAF50   2.78:1  FAIL
        weak-2x    #FFF on #E65100   3.79:1  FAIL
        weak-4x    #FFF on #B71C1C   6.57:1  ok

    The two failures are the two most common cells in any matrix. They are now
    reachable by the sweep, because the Pokemon details page renders the same
    classes server-side on every load.

    Unlike the type badges, these backgrounds carry no franchise identity —
    they are invented severity indicators — so darkening them is allowed here
    where `test_background_colours_are_unchanged` forbids it for badges.
    """

    SEVERITY_CLASSES = ["immune", "resist-4x", "resist-2x", "weak-2x", "weak-4x"]

    @staticmethod
    def declared_cells():
        css = re.sub(r"/\*.*?\*/", "", read(STYLES_CSS), flags=re.S)
        found = {}
        for mod in TestMatchupCellContrast.SEVERITY_CLASSES:
            block = re.search(
                rf"\.matchup-cell\.{re.escape(mod)}\s*\{{([^}}]*)\}}", css)
            if not block:
                continue
            body = block.group(1)
            bg = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,6})", body)
            fg = re.search(r"(?<!-)\bcolor:\s*(#[0-9a-fA-F]{3,6})", body)
            if bg and fg:
                found[mod] = (bg.group(1), fg.group(1))
        return found

    @pytest.mark.parametrize("mod", SEVERITY_CLASSES)
    def test_every_severity_cell_meets_aa(self, mod):
        declared = self.declared_cells()
        assert mod in declared, (
            f".matchup-cell.{mod} must declare an explicit background and "
            f"colour so its contrast can be measured"
        )
        bg, fg = declared[mod]
        ratio = contrast(fg, bg)
        assert ratio >= WCAG_AA_NORMAL, (
            f".matchup-cell.{mod} is {ratio:.2f}:1 ({fg} on {bg}); AA needs "
            f"{WCAG_AA_NORMAL}:1"
        )

    def test_the_neutral_cell_does_not_depend_on_theme_text(self):
        """`neutral` used `var(--text-primary)` over a translucent white.

        On the details page the card is #FFFFFF in both themes, so in dark mode
        that resolved to near-white text on near-white — the same defect as
        `.ability-chip`, in a cell that appears more often than any other.
        """
        css = re.sub(r"/\*.*?\*/", "", read(STYLES_CSS), flags=re.S)
        block = re.search(r"\.matchup-cell\.neutral\s*\{([^}]*)\}", css)
        assert block, "no .matchup-cell.neutral rule"
        assert "--text-primary" not in block.group(1), (
            ".matchup-cell.neutral inherits --text-primary, which is near-white "
            "in dark mode and sits on a near-white cell"
        )

    def test_severity_is_never_signalled_by_colour_alone(self):
        """Every cell carries its multiplier as text.

        Colour-coding a grid is fine; colour-coding it *only* is not, and the
        renderer that draws these cells must always write the label.
        """
        js = read("App/static/js/ml_playground.js")
        assert re.search(r"label\s*=\s*['\"]", js), (
            "ml_playground.js no longer writes a textual multiplier label"
        )


class TestFloatingLabelsAreReadableInBothThemes:
    """Bootstrap's floating label overlays the input, not the card.

    `.form-floating > label` is `position: absolute` and sits on top of the
    field, so its contrast is measured against the INPUT's background. In dark
    mode the input is `rgba(30, 30, 60, 0.8)` while the label kept Bootstrap's
    light-mode `#212529` — dark text on a dark field, measured at 1.82:1 on
    both auth screens, which are the first pages anyone sees.

    Nothing in the project had ever set a colour for it, so it inherited a
    value that only works in one theme. Scoped per page because an unscoped
    `.form-floating` rule in pages.css reaches all nine.
    """

    @pytest.mark.parametrize("page", ["login", "signup"])
    def test_the_label_sets_a_theme_aware_colour(self, page):
        css = re.sub(r"/\*.*?\*/", "", read("App/static/css/pages.css"), flags=re.S)
        block = re.search(
            rf"\.{page}-page \.form-floating > label\s*\{{([^}}]*)\}}", css)
        assert block, (
            f"no .{page}-page .form-floating > label rule; the label inherits "
            f"Bootstrap's light-mode colour and is unreadable in dark mode"
        )
        body = block.group(1)
        assert re.search(r"(?<!-)\bcolor:\s*var\(--", body), (
            f"the {page} floating label pins a literal colour; it sits on an "
            f"input whose background genuinely changes with the theme, so it "
            f"needs a token: {body.strip()}"
        )
