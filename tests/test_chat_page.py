"""T26 — the chat page and its client.

Split out from `tests/test_chat.py` rather than appended to it, as T18 split
`test_sql_pushdown.py` off: that file is the server contract (handshake, rooms,
persistence, history) and already runs 73 tests. This one is the UI contract.
Keeping them apart means a failure names which half broke.

The page inherits every rule Phase 7 established, and those are not optional
just because chat is new:

  T21  no inline `<style>` block; page CSS lives in pages.css scoped under a
       page-root class, or it restyles the other nine pages
  T22  4.5:1 contrast, visible focus, real labels, aria-live on async regions,
       axe clean in both themes
  T23  no horizontal overflow at 375px, touch targets >= 24px

The one requirement unique to this task is **`textContent`, never
`innerHTML`** — chat renders text that another user typed, which is the only
place in this application where that is true.
"""

import re
from pathlib import Path

TEMPLATES = Path("App/templates")
CHAT_HTML = TEMPLATES / "chat.html"
CHAT_JS = Path("App/static/js/chat.js")
PAGES_CSS = Path("App/static/css/pages.css")
LAYOUT = TEMPLATES / "layout.html"


def read(path):
    return Path(path).read_text(encoding="utf8")


class TestPageExistsAndIsReachable:
    def test_the_template_exists(self):
        assert CHAT_HTML.exists(), f"{CHAT_HTML} was never created"

    def test_the_client_script_exists(self):
        assert CHAT_JS.exists(), f"{CHAT_JS} was never created"

    def test_chat_route_renders(self, auth_client):
        assert auth_client.get("/chat").status_code == 200

    def test_chat_requires_authentication(self, client):
        """Chat is not public, and neither is the page that opens the socket."""
        response = client.get("/chat")
        assert response.status_code in (302, 401), (
            f"/chat returned {response.status_code} to an anonymous visitor"
        )

    def test_chat_is_in_the_nav(self, auth_client):
        html = auth_client.get("/app").get_data(as_text=True)
        assert re.search(r'<a class="nav-link[^"]*"[^>]*href="/chat"', html), (
            "no /chat link in the navigation, so the page is unreachable"
        )

    def test_the_nav_link_marks_itself_active(self, auth_client):
        """Every other nav entry does; an odd one out looks like a bug."""
        html = auth_client.get("/chat").get_data(as_text=True)
        assert re.search(r'<a class="nav-link active"[^>]*href="/chat"', html), (
            "the chat nav link does not highlight when you are on /chat"
        )


class TestNoUntrustedHtml:
    """The requirement unique to this page.

    Chat renders text another user typed. Every other page in this app renders
    either its own copy or database rows the user themselves created — this is
    the first genuine cross-user injection surface, and `innerHTML` anywhere in
    the render path is a stored-XSS bug.
    """

    def test_the_client_never_assigns_innerhtml(self):
        """Scans code, not comments.

        A bare substring search would fail on the comment in chat.js that
        explains why innerHTML is avoided — the same trap as T25's orphan
        check, where a text search matched its own documentation.
        """
        source = read(CHAT_JS)
        # chat.js is JavaScript, so this is a targeted scan rather than a parse:
        # assignment to .innerHTML / .outerHTML, or an insertAdjacentHTML call.
        offenders = []
        for lineno, line in enumerate(source.splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"\.(inner|outer)HTML\s*=", code):
                offenders.append(f"{lineno}: {line.strip()[:80]}")
            if "insertAdjacentHTML" in code:
                offenders.append(f"{lineno}: {line.strip()[:80]}")
        assert not offenders, (
            "chat.js writes HTML instead of text — a message containing a "
            "script tag would execute:\n  " + "\n  ".join(offenders)
        )

    def test_the_client_uses_textcontent(self):
        source = read(CHAT_JS)
        assert "textContent" in source, (
            "chat.js never uses textContent; how is message text rendered?"
        )

    def test_no_jinja_interpolation_into_a_script_block(self):
        """Server-rendered data belongs in a JSON island (T21's pattern)."""
        html = read(CHAT_HTML)
        for tag, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
            if "application/json" in tag or "src=" in tag:
                continue
            assert "{{" not in body, (
                f"chat.html interpolates Jinja into executable script: {body.strip()[:90]}"
            )


class TestMessageRendering:
    """Acceptance: own vs others distinguished, timestamps shown."""

    def test_own_and_other_messages_get_different_classes(self):
        source = read(CHAT_JS)
        assert re.search(r"chat-message--(own|mine)", source), (
            "no distinct class for the current user's own messages"
        )

    def test_the_page_knows_who_you_are(self, auth_client):
        """Distinguishing own messages needs the viewer's identity server-side.

        Comparing against a username typed by the client would be spoofable and
        wrong after a rename.
        """
        html = auth_client.get("/chat").get_data(as_text=True)
        island = re.search(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S
        )
        assert island, "chat.html emits no JSON island with the page context"
        import json

        payload = json.loads(island.group(1))
        assert payload.get("username") == "bob", (
            f"the page does not carry the viewer's identity: {payload}"
        )

    def test_timestamps_are_rendered(self):
        source = read(CHAT_JS)
        assert "timestamp" in source, "chat.js ignores the message timestamp"

    def test_the_room_list_comes_from_the_server(self, auth_client):
        """Hard-coding the six rooms in the template would drift from
        CHAT_ROOMS the moment the list changes."""
        from App.constants import CHAT_ROOMS

        html = auth_client.get("/chat").get_data(as_text=True)
        for room in CHAT_ROOMS:
            assert room in html, f"room {room!r} missing from the page"


class TestComposerLimit:
    """Acceptance: composer enforces 500 chars with a visible counter."""

    def test_the_input_has_a_maxlength(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        field = re.search(r"<(input|textarea)[^>]*id=\"message-input\"[^>]*>", html)
        assert field, "no #message-input in chat.html"
        assert 'maxlength="500"' in field.group(0), (
            f"the composer has no maxlength: {field.group(0)[:120]}"
        )

    def test_the_limit_matches_the_server(self):
        """A client cap looser than the server's produces silent rejections.

        The server refuses >500 with an error; a composer that let you type 600
        would look broken rather than guarded.
        """
        from App.blueprints.chat import MAX_MESSAGE_LENGTH

        html = read(CHAT_HTML)
        assert f'maxlength="{MAX_MESSAGE_LENGTH}"' in html, (
            f"composer maxlength does not match MAX_MESSAGE_LENGTH "
            f"({MAX_MESSAGE_LENGTH})"
        )

    def test_a_counter_element_exists(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        assert 'id="char-counter"' in html, "no visible character counter"

    def test_the_counter_is_announced_politely(self, auth_client):
        """It updates on every keystroke.

        `aria-live="assertive"` here would interrupt a screen reader on each
        character typed, which is worse than silence.
        """
        html = auth_client.get("/chat").get_data(as_text=True)
        counter = re.search(r"<[^>]*id=\"char-counter\"[^>]*>", html)
        assert counter, "no #char-counter"
        assert 'aria-live="polite"' in counter.group(0), (
            f"the counter is not politely announced: {counter.group(0)}"
        )

    def test_the_client_updates_the_counter(self):
        source = read(CHAT_JS)
        assert "char-counter" in source, "chat.js never updates the counter"


class TestAccessibility:
    """T22's rules apply to new pages too."""

    def test_the_message_list_sits_inside_a_live_region(self, auth_client):
        """Messages arrive without a page change; nothing else announces them.

        The role is on a WRAPPER rather than the <ol> itself: `role="log"` on a
        list replaces its implicit `list` role and orphans every <li>, which
        axe reports as `listitem` (5 nodes). So this checks the list is
        *inside* a live region, not that it is one.
        """
        html = auth_client.get("/chat").get_data(as_text=True)
        assert 'id="message-list"' in html, "no #message-list"
        wrapper = re.search(
            r'<div[^>]*role="log"[^>]*>\s*<ol[^>]*id="message-list"', html, re.S
        )
        assert wrapper, (
            "#message-list is not wrapped in a role=log live region, so "
            "incoming messages are never announced"
        )
        assert 'aria-live="polite"' in wrapper.group(0), (
            f"the live region is not polite: {wrapper.group(0)[:120]}"
        )

    def test_the_list_keeps_its_list_semantics(self, auth_client):
        """The <ol> must not carry a role that overrides `list`."""
        html = auth_client.get("/chat").get_data(as_text=True)
        ol = re.search(r'<ol[^>]*id="message-list"[^>]*>', html)
        assert ol, "no <ol id=message-list>"
        assert "role=" not in ol.group(0), (
            f"the list has an overriding role, which orphans its items: {ol.group(0)}"
        )

    def test_the_composer_has_a_real_label(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        field = re.search(r"<(input|textarea)[^>]*id=\"message-input\"[^>]*>", html)
        assert field, "no #message-input"
        has_label = (
            'for="message-input"' in html
            or "aria-label=" in field.group(0)
            or "aria-labelledby=" in field.group(0)
        )
        assert has_label, "the composer input has no accessible name"

    def test_the_room_selector_has_a_real_label(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        field = re.search(r"<select[^>]*id=\"room-select\"[^>]*>", html)
        assert field, "no #room-select"
        assert 'for="room-select"' in html or "aria-label=" in field.group(0), (
            "the room selector has no accessible name"
        )

    def test_no_click_handler_on_a_non_interactive_element(self):
        html = read(CHAT_HTML)
        offenders = [
            f"<{tag} {attrs.strip()[:60]}>"
            for tag, attrs in re.findall(r"<(\w+)([^>]*\bonclick=[^>]*)>", html)
            if tag.lower() not in ("a", "button", "input", "select", "textarea")
        ]
        assert not offenders, f"keyboard-unreachable controls: {offenders}"


class TestStylesDoNotLeak:
    """T21's structural rule: page CSS is scoped or it hits all ten pages."""

    def test_the_template_has_no_inline_style_block(self):
        block = re.search(r"{% block styles %}(.*?){% endblock %}", read(CHAT_HTML), re.S)
        lines = [ln for ln in block.group(1).splitlines() if ln.strip()] if block else []
        assert not lines, (
            f"chat.html holds {len(lines)} lines of inline CSS; it belongs in "
            "pages.css scoped under .chat-page"
        )

    def test_chat_rules_are_scoped_to_the_page(self):
        css = re.sub(r"/\*.*?\*/", "", read(PAGES_CSS), flags=re.S)
        css = re.sub(r"@media[^{]*\{", "", css)
        selectors = {
            part.strip()
            for block in re.findall(r"([^{}]+)\{", css)
            for part in block.split(",")
            if part.strip() and not part.strip().startswith("@")
        }
        chat_rules = [s for s in selectors if "chat" in s]
        assert chat_rules, "no chat styles in pages.css"
        unscoped = [s for s in chat_rules if not s.startswith(".chat-page")]
        assert not unscoped, (
            f"chat rules not scoped under .chat-page: {unscoped}. Unscoped they "
            "apply to all ten pages — this is exactly how T20 restyled "
            "/pokemon-stats."
        )

    def test_the_page_root_class_is_present(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        assert "chat-page" in html, (
            "the .chat-page root class is missing, so every scoped rule is dead"
        )


class TestClientLoading:
    """The Socket.IO browser client has to be there, and before chat.js."""

    def test_the_socketio_client_is_loaded(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        assert re.search(r'<script[^>]+src="[^"]*socket\.io[^"]*\.js"', html), (
            "/chat never loads the Socket.IO browser client"
        )

    def test_it_loads_before_the_page_script(self, auth_client):
        """The same ordering bug T21 fixed on /pokemon-piechart.

        A library loaded after the script that uses it is not a fix.
        """
        html = auth_client.get("/chat").get_data(as_text=True)
        library = re.search(r'<script[^>]+src="[^"]*socket\.io[^"]*\.js"', html)
        page = html.find("js/chat.js")
        assert library and page > 0, "could not locate both scripts"
        assert library.start() < page, (
            "socket.io loads after chat.js, so `io` is undefined when it runs"
        )

    def test_chat_js_is_loaded(self, auth_client):
        html = auth_client.get("/chat").get_data(as_text=True)
        assert "js/chat.js" in html


class TestBaselineHarnessCoversTheNewPage:
    """A tenth page that the harness does not know about is unprotected.

    Every template refactor from here on would silently skip /chat.
    """

    def test_chat_is_in_the_page_baseline(self):
        source = read("tasks/page-baseline.py")
        assert '"/chat"' in source, (
            "tasks/page-baseline.py does not capture /chat, so future template "
            "work would not notice breaking it"
        )
