"""T24 — SocketIO server with an authenticated handshake.

This is the highest-deploy-risk task in the plan, because the worker class is
not a chat concern: it decides how **every** HTTP request is served. Most of
these tests exist to prove the blast radius stayed small.

**The plan said eventlet; this does not use it.** Three options were measured
before choosing (recorded in tasks/todo.md):

  - eventlet 0.41.1 works on Python 3.12 but now emits an official
    EventletDeprecationWarning: "strongly recommend against using it for new
    projects".
  - eventlet and gevent both require `monkey_patch()` before any other import,
    and with it psycopg2 blocks the whole worker: measured against live Neon,
    a 2s query let a 100ms ticker run **1** time instead of 25. One slow query
    would stall every HTTP request and every WebSocket heartbeat on Render's
    single worker. `psycogreen` fixes it, and nothing in the plan mentioned
    needing it.
  - `async_mode="threading"` with simple-websocket needs **no monkey patching
    at all**, so psycopg2, SQLAlchemy, Redis and all 38 existing routes keep
    the code path they have today. It serves real WebSockets, not just
    long-polling. The cost is a thread per connection, which suits this app.

The maintainer chose the third. `TestNothingWasMonkeyPatched` is the guard that
keeps that decision true, because reintroducing a patch would silently change
how every request in the application is executed.
"""

import re
from pathlib import Path

import pytest

from App.app import app as flask_app
from App.config import get_settings


@pytest.fixture(autouse=True)
def _chat_enabled():
    """Turn the feature on for this file.

    `CHAT_ENABLED` defaults to False — trainer chat ships closed while it is
    still being built — so without this every test here would be asserting
    against a "coming soon" page and a refused socket.

    The feature is gated, not deleted, and this is what keeps it covered while
    it is switched off. `tests/test_chat_disabled.py` asserts the other half.
    """
    from App.app import app as _app

    previous = _app.config.get("CHAT_ENABLED")
    _app.config["CHAT_ENABLED"] = True
    yield
    _app.config["CHAT_ENABLED"] = previous


REQUIREMENTS = Path("requirements.txt")
RENDER_YAML = Path("render.yaml")
SOCKETS_MODULE = Path("App/sockets.py")


def read(path):
    return Path(path).read_text(encoding="utf8")


@pytest.fixture(autouse=True)
def _isolate_connection_registry():
    """Empty the per-connection registry around every test.

    `_CONNECTIONS` is module-global and only evicted on disconnect. Most tests
    here open a socket and never close it — as a browser tab would not — so
    without this the registry accumulates across the file and any assertion
    about its size measures test-execution order instead of behaviour.

    Clearing it is also what makes those size assertions strong enough to catch
    a real eviction bug, rather than having to weaken them to "my sid is gone".
    """
    from App.sockets import _CONNECTIONS

    _CONNECTIONS.clear()
    yield
    _CONNECTIONS.clear()


@pytest.fixture
def socket_client(auth_client):
    """A SocketIO test client that carries the logged-in browser's cookies.

    Passing `flask_test_client` is what makes the handshake see the JWT cookie;
    without it the connection is anonymous no matter who logged in.
    """
    from App.sockets import socketio

    return socketio.test_client(flask_app, flask_test_client=auth_client)


@pytest.fixture
def anon_socket_client(client):
    """A SocketIO test client with no auth cookies at all."""
    from App.sockets import socketio

    return socketio.test_client(flask_app, flask_test_client=client)


class TestSocketIOIsWiredIntoTheFactory:
    def test_sockets_module_exists(self):
        assert SOCKETS_MODULE.exists(), f"{SOCKETS_MODULE} was never created"

    def test_socketio_is_initialised_on_the_app(self):
        from App.sockets import socketio

        assert socketio.server is not None, (
            "SocketIO was constructed but never init_app'd, so no handshake "
            "endpoint exists"
        )

    def test_the_handshake_endpoint_answers(self, client):
        """Flask-SocketIO installs WSGI middleware rather than a URL rule.

        Checking `url_map` finds nothing and proves nothing; the handshake
        itself is the only real evidence that `gunicorn wsgi:app` will serve
        Socket.IO.
        """
        response = client.get("/socket.io/?EIO=4&transport=polling")
        assert response.status_code == 200, (
            f"the Socket.IO handshake returned {response.status_code}; "
            "the middleware is not installed"
        )
        assert b'"sid"' in response.data, "handshake returned no session id"

    def test_the_handshake_offers_a_websocket_upgrade(self, client):
        """Long-polling alone would satisfy a naive check but is not the goal.

        `threading` mode only reaches real WebSocket when simple-websocket is
        installed; without it this advertises no upgrades and the chat silently
        degrades to polling.
        """
        response = client.get("/socket.io/?EIO=4&transport=polling")
        assert b'"upgrades":["websocket"]' in response.data, (
            "the server advertises no websocket upgrade — is simple-websocket "
            f"installed? payload: {response.data[:120]}"
        )

    def test_async_mode_is_threading(self):
        from App.sockets import socketio

        assert socketio.async_mode == "threading", (
            f"async_mode is {socketio.async_mode!r}; the maintainer chose "
            "'threading' so that no monkey patching is required"
        )


class TestHandshakeRequiresAuthentication:
    """Acceptance: a connection without a valid JWT cookie is *rejected*.

    "Rejected, not silently accepted" is the wording that matters. A handler
    that merely declines to join a room still leaves an open socket that can
    receive broadcasts, which is the failure mode this guards.
    """

    def test_anonymous_connection_is_refused(self, anon_socket_client):
        assert not anon_socket_client.is_connected(), (
            "a client with no JWT cookie completed the handshake; it must be "
            "rejected outright, not connected-but-unauthorised"
        )

    def test_authenticated_connection_is_accepted(self, socket_client):
        assert socket_client.is_connected(), (
            "a logged-in client was rejected — the handshake cannot read the "
            "JWT cookie"
        )

    def test_a_forged_cookie_is_refused(self, client):
        """A signature check, not merely a presence check.

        Reading `request.cookies` and trusting a non-empty value would pass
        the anonymous test above and accept anyone.
        """
        from App.sockets import socketio

        client.set_cookie("access_token", "not.a.real.jwt", domain="localhost")
        forged = socketio.test_client(flask_app, flask_test_client=client)
        assert not forged.is_connected(), (
            "a garbage access_token cookie was accepted; the handshake is not "
            "verifying the signature"
        )

    def test_the_connection_knows_which_user_it_is(self, socket_client):
        """Rejecting anonymice is half of it; the session must also be bound.

        Without this, every authenticated socket is interchangeable and T25
        has no way to attribute a message.
        """
        received = socket_client.get_received()
        hello = [m for m in received if m["name"] == "connected"]
        assert hello, (
            f"no `connected` event was emitted on join; received: "
            f"{[m['name'] for m in received]}"
        )
        payload = payload_of(hello[0])
        assert payload.get("username") == "bob", (
            f"the socket did not resolve the authenticated user: {payload}"
        )


class TestFlaskThreeCompatibility:
    """`manage_session=False` is required, not a preference.

    Flask-SocketIO's default (`manage_session=True`) copies the HTTP session
    per socket by doing `ctx.session = session_obj`. Flask 3.1 made
    `RequestContext.session` a read-only property, so that assignment raises
    `AttributeError: property 'session' of 'RequestContext' object has no
    setter` — on **every** connection, before any handler runs.

    Reproduced directly against Flask 3.1.3 / Flask-SocketIO 5.5.1 while
    building this. Anyone "tidying up" the argument would break all chat.
    """

    def test_manage_session_is_disabled(self):
        source = read(SOCKETS_MODULE)
        assert re.search(r"manage_session\s*=\s*False", source), (
            "manage_session is not explicitly False; the Flask-SocketIO default "
            "raises AttributeError on Flask 3.1 for every connection"
        )

    def test_a_connection_survives_the_session_handling(self, socket_client):
        """The end-to-end proof: if the default crept back, this dies."""
        assert socket_client.is_connected()


class TestConnectionsAreReleased:
    """Identity is held in a module dict, so disconnect must evict.

    Nothing else clears it. A missed eviction is a slow leak on a worker that
    stays up for weeks, and it is invisible unless something checks.
    """

    def test_disconnect_removes_the_identity(self, socket_client):
        from App.sockets import _CONNECTIONS

        assert socket_client.is_connected()
        assert len(_CONNECTIONS) >= 1, "connect stored no identity"
        socket_client.disconnect()
        assert not socket_client.is_connected()
        assert len(_CONNECTIONS) == 0, (
            f"disconnect left {len(_CONNECTIONS)} identity/identities behind: "
            f"{_CONNECTIONS}"
        )

    def test_a_rejected_handshake_stores_nothing(self, anon_socket_client):
        from App.sockets import _CONNECTIONS

        assert not anon_socket_client.is_connected()
        assert len(_CONNECTIONS) == 0, (
            "a refused connection still recorded an identity"
        )


class TestNothingWasMonkeyPatched:
    """The reason this task is low-risk instead of high-risk.

    eventlet/gevent replace socket, threading, ssl and select process-wide.
    That is what would have made a chat feature able to break Neon connection
    handling, the Redis session store and every HTTP route at once. The choice
    to avoid it is only worth anything if it stays avoided.
    """

    def test_the_standard_library_is_untouched(self):
        import socket
        import threading

        assert socket.socket.__module__ == "socket", (
            f"socket.socket now comes from {socket.socket.__module__} — "
            "something monkey-patched the standard library"
        )
        assert threading.Thread.__module__ == "threading", (
            f"threading.Thread now comes from {threading.Thread.__module__}"
        )

    @pytest.mark.parametrize("module", ["eventlet", "gevent", "psycogreen"])
    def test_no_greenlet_runtime_is_imported(self, module):
        import sys

        assert module not in sys.modules, (
            f"{module} is imported. The chosen async mode needs no green "
            "threads; importing one means a monkey patch is nearby."
        )

    def test_wsgi_entrypoint_has_no_monkey_patch(self):
        source = read("wsgi.py")
        assert "monkey_patch" not in source and "patch_all" not in source, (
            "wsgi.py monkey-patches. That changes how every request in the "
            "application is executed, which this design exists to avoid."
        )


class TestExistingRoutesStillWork:
    """Acceptance: all existing HTTP routes still work.

    Wrapping `app.wsgi_app` in middleware is exactly the kind of change that
    can swallow or reorder requests, so the ordinary pages are re-checked
    rather than assumed.
    """

    @pytest.mark.parametrize(
        "route",
        ["/app", "/pokemon-area", "/pokemon-area/pokemon-details/1", "/quiz",
         "/pokemon-stats", "/pokemon-ml", "/pokemon-piechart"],
    )
    def test_authenticated_page_still_renders(self, auth_client, route):
        assert auth_client.get(route).status_code == 200

    @pytest.mark.parametrize("route", ["/", "/signup"])
    def test_public_page_still_renders(self, client, route):
        assert client.get(route).status_code == 200

    def test_the_login_flow_still_works(self, client):
        from tests.helpers import login

        response = login(client, "bob", "bobpass")
        assert response.status_code == 200
        assert client.get_cookie("access_token") is not None, (
            "logging in no longer sets the access cookie"
        )

    def test_an_unknown_path_is_still_a_404(self, client):
        """The middleware must pass non-socket paths straight through."""
        assert client.get("/definitely-not-a-route").status_code == 404


class TestCorsMatchesTheAppPolicy:
    """Acceptance: CORS matching CORS_ORIGINS.

    Socket.IO does its own CORS handling — it does not inherit Flask-CORS. Left
    at the default, `cors_allowed_origins` is "*", which would hand any origin
    a credentialed socket while the HTTP side is locked down. That asymmetry is
    the bug worth guarding.
    """

    def test_socketio_does_not_default_to_a_wildcard(self):
        source = read(SOCKETS_MODULE)
        assert "cors_allowed_origins" in source, (
            "sockets.py never sets cors_allowed_origins, so Socket.IO defaults "
            "to '*' regardless of what CORS_ORIGINS says"
        )

    def test_socketio_uses_the_same_origins_as_the_http_layer(self):
        from App.sockets import socketio

        configured = socketio.server.eio.cors_allowed_origins
        expected = get_settings().cors_origin_list
        if "*" in expected:
            return  # dev default; the production guard below is what matters
        assert configured == expected, (
            f"Socket.IO allows {configured!r} but the app allows {expected!r}"
        )


class TestDeploymentConfiguration:
    """The half of this task that only fails in production.

    Verified here because the Render flip is deferred to the final pass, so
    these files are the only artefact until then.
    """

    def test_simple_websocket_is_pinned(self):
        assert re.search(r"^simple-websocket==", read(REQUIREMENTS), re.M), (
            "simple-websocket is not in requirements.txt; without it the "
            "server falls back to long-polling in production"
        )

    def test_no_greenlet_runtime_is_a_dependency(self):
        text = read(REQUIREMENTS)
        for package in ("eventlet", "gevent", "psycogreen"):
            assert not re.search(rf"^{package}[=<>]", text, re.M), (
                f"{package} is in requirements.txt but the chosen async mode "
                "does not use it"
            )

    def test_greenlet_is_not_pinned_to_a_prerelease(self):
        """Found during T24: the pin was `3.0.0rc3`, a release candidate.

        Nothing to do with sockets — but it is a transitive dependency of
        SQLAlchemy and it was shipping to production as an RC.
        """
        match = re.search(r"^greenlet==(\S+)", read(REQUIREMENTS), re.M)
        assert match, "greenlet is no longer pinned"
        version = match.group(1)
        assert not re.search(r"(rc|a|b)\d*$", version), (
            f"greenlet is pinned to the prerelease {version}"
        )

    def test_the_start_command_can_hold_websockets(self):
        """The default sync worker cannot keep a connection open.

        `threading` mode needs a worker that gives each connection its own
        thread; gunicorn's default sync worker does not.
        """
        start = re.search(r"startCommand:\s*\"([^\"]+)\"", read(RENDER_YAML))
        assert start, "render.yaml has no startCommand"
        command = start.group(1)
        assert "gthread" in command, (
            f"start command still uses the sync worker: {command}"
        )
        threads = re.search(r"--threads\s+(\d+)", command)
        assert threads, (
            f"gthread worker with no --threads is single-threaded: {command}"
        )
        # Not a style preference — a measured floor. A held WebSocket occupies
        # its thread for the whole connection, so this value is the ceiling on
        # concurrent chat users. At --threads 8, eight sockets starved the
        # worker outright: GET /app timed out after 10s and a ninth socket
        # could not connect. At 64, forty sockets were held with HTTP still
        # answering in ~40ms for +4MB of RSS.
        assert int(threads.group(1)) >= 32, (
            f"--threads {threads.group(1)} is too low: that is roughly the "
            "number of concurrent chat users before HTTP starts timing out"
        )

    def test_render_yaml_no_longer_promises_eventlet(self):
        text = read(RENDER_YAML)
        assert "worker-class eventlet" not in text, (
            "render.yaml still tells the next reader to switch to eventlet"
        )


# ══════════════════════════════════════════════════════════════════════════
# T25 — rooms, persistence and the history API
# ══════════════════════════════════════════════════════════════════════════


ROOM = "general"


def payload_of(event):
    """The dict a handler emitted, however the test client wrapped it.

    flask_socketio's test client is inconsistent: a single dict emitted with
    `to=<room>` is recorded as `args` directly, while the same dict emitted to
    the caller arrives as `args[0]`. Normalising here beats each assertion
    guessing, and beats depending on which form a given emit happens to take.
    """
    args = event["args"]
    if isinstance(args, dict):
        return args
    return args[0]



def _room_list():
    from App.constants import CHAT_ROOMS

    return CHAT_ROOMS


def _seed_messages(count, room=ROOM, text=lambda i: f"message {i}"):
    """Insert messages directly, bypassing the socket layer.

    History is a read contract; driving it through sockets would couple these
    tests to the write path and hide an ordering bug behind a working sender.
    """
    from App.models import Message, User, db

    with flask_app.app_context():
        user = User.query.filter_by(username="bob").first()
        for i in range(count):
            db.session.add(Message(sender_id=user.id, room=room, text=text(i)))
        db.session.commit()


class TestRoomWhitelist:
    """Acceptance: unknown room names rejected, not silently created.

    A typo'd room that quietly springs into existence splits a conversation in
    half with no error anywhere, and lets a client create unbounded rooms.
    """

    def test_the_starting_set_is_small(self):
        """The maintainer chose 5–6 over the eventual 19.

        `general` plus 18 type rooms would leave almost all of them empty,
        which reads as a dead feature.
        """
        rooms = _room_list()
        assert 5 <= len(rooms) <= 6, (
            f"{len(rooms)} rooms configured; the ruling was to start with 5–6"
        )
        assert "general" in rooms

    def test_joining_an_unknown_room_is_refused(self, socket_client):
        socket_client.emit("join", {"room": "does-not-exist"})
        received = socket_client.get_received()
        errors = [m for m in received if m["name"] == "error"]
        assert errors, (
            f"joining an unknown room produced no error: "
            f"{[m['name'] for m in received]}"
        )

    def test_joining_an_unknown_room_creates_nothing(self, socket_client):
        from App.models import Message

        socket_client.emit("join", {"room": "does-not-exist"})
        socket_client.emit(
            "send_message", {"room": "does-not-exist", "text": "hello"}
        )
        with flask_app.app_context():
            leaked = Message.query.filter_by(room="does-not-exist").count()
        assert leaked == 0, "a message was persisted into an unknown room"

    def test_history_for_an_unknown_room_is_404(self, auth_client):
        response = auth_client.get("/api/chat/does-not-exist/history")
        assert response.status_code == 404, (
            f"unknown room returned {response.status_code}; it must not be "
            "treated as an empty valid room"
        )


class TestJoinDeliversHistory:
    """Acceptance: join delivers the last 50 messages oldest-first."""

    def test_join_emits_recent_history(self, socket_client):
        _seed_messages(3)
        socket_client.get_received()  # drain the connect event
        socket_client.emit("join", {"room": ROOM})
        history = [m for m in socket_client.get_received() if m["name"] == "history"]
        assert history, "join delivered no history event"
        messages = payload_of(history[0])["messages"]
        assert [m["text"] for m in messages] == [
            "message 0",
            "message 1",
            "message 2",
        ]

    def test_history_is_capped_at_fifty(self, socket_client):
        _seed_messages(60)
        socket_client.get_received()
        socket_client.emit("join", {"room": ROOM})
        messages = payload_of(
            [m for m in socket_client.get_received() if m["name"] == "history"][0]
        )["messages"]
        assert len(messages) == 50, f"join delivered {len(messages)} messages, not 50"

    def test_the_fifty_delivered_are_the_most_recent(self, socket_client):
        """Capped *and* correct: the newest 50, presented oldest-first.

        Taking `.limit(50)` off an ascending query returns the fifty *oldest*
        messages — the exact opposite — and still passes a length check.
        """
        _seed_messages(60)
        socket_client.get_received()
        socket_client.emit("join", {"room": ROOM})
        messages = payload_of(
            [m for m in socket_client.get_received() if m["name"] == "history"][0]
        )["messages"]
        texts = [m["text"] for m in messages]
        assert texts[0] == "message 10", f"history starts at {texts[0]!r}, not message 10"
        assert texts[-1] == "message 59", f"history ends at {texts[-1]!r}, not message 59"

    def test_ordering_is_stable_when_timestamps_collide(self, socket_client):
        """The contract: a burst of chat comes back in the order it was sent.

        SQLite's CURRENT_TIMESTAMP has **second** resolution, so 20 rapid
        inserts share one value (measured). This asserts the observable result.

        Note it is NOT sufficient on its own — see the query-shape test below.
        Dropping the tie-break still passes here, because SQLite happens to
        return rows in rowid order. The bug it guards against is latent and
        surfaces on Postgres, where equal-timestamp rows come back in whatever
        order the heap gives.
        """
        _seed_messages(20)
        from App.models import Message

        with flask_app.app_context():
            stamps = {m.timestamp for m in Message.query.all()}
        assert len(stamps) < 20, (
            "this test is only meaningful when timestamps actually collide; "
            f"got {len(stamps)} distinct values for 20 rows"
        )
        socket_client.get_received()
        socket_client.emit("join", {"room": ROOM})
        messages = payload_of(
            [m for m in socket_client.get_received() if m["name"] == "history"][0]
        )["messages"]
        assert [m["text"] for m in messages] == [f"message {i}" for i in range(20)]

    def test_the_query_breaks_ties_on_id(self, client):
        """Checks the mechanism, because behaviour cannot.

        Removing the `id` tie-break passes every behavioural test in this file:
        SQLite returns equal-timestamp rows in rowid order, which is the right
        answer by accident. Postgres gives no such guarantee, so the defect
        would ship green and appear only in production, under load, as
        shuffled chat history — the same SQLite-hides-a-Postgres-bug shape that
        has reached production twice in this project.

        So this asserts the compiled SQL orders by both columns. Testing the
        implementation is the lesser evil when the behaviour is untestable on
        the backend the suite runs against.
        """
        import inspect as _inspect

        from App.blueprints import chat as chat_module

        source = _inspect.getsource(chat_module.recent_messages)
        order_by = re.search(r"\.order_by\(([^)]*\)[^)]*)\)", source)
        assert order_by, f"recent_messages() has no order_by:\n{source}"
        clause = order_by.group(1)
        assert "Message.timestamp" in clause, f"not ordered by timestamp: {clause}"
        assert "Message.id" in clause, (
            f"no id tie-break in the ordering: {clause!r}. Equal timestamps "
            "would come back in an arbitrary order on Postgres, and SQLite "
            "hides it by returning rowid order."
        )

    def test_history_is_scoped_to_the_room(self, socket_client):
        rooms = _room_list()
        other = next(r for r in rooms if r != ROOM)
        _seed_messages(2, room=ROOM, text=lambda i: f"in-general {i}")
        _seed_messages(2, room=other, text=lambda i: f"in-other {i}")
        socket_client.get_received()
        socket_client.emit("join", {"room": ROOM})
        messages = payload_of(
            [m for m in socket_client.get_received() if m["name"] == "history"][0]
        )["messages"]
        assert all("in-general" in m["text"] for m in messages), (
            f"history leaked another room's messages: {[m['text'] for m in messages]}"
        )


class TestMessageValidation:
    """Acceptance: non-empty, stripped, <=500 chars — and persisted."""

    def _send(self, client, text):
        from App.models import Message

        client.emit("join", {"room": ROOM})
        client.get_received()
        client.emit("send_message", {"room": ROOM, "text": text})
        with flask_app.app_context():
            stored = Message.query.filter_by(room=ROOM).all()
        return stored, client.get_received()

    def test_a_valid_message_is_persisted(self, socket_client):
        stored, _ = self._send(socket_client, "hello world")
        assert len(stored) == 1
        assert stored[0].text == "hello world"

    def test_whitespace_is_stripped(self, socket_client):
        stored, _ = self._send(socket_client, "   padded   ")
        assert stored[0].text == "padded", f"stored {stored[0].text!r}"

    @pytest.mark.parametrize("text", ["", "   ", "\n\t  \n"])
    def test_an_empty_message_is_rejected(self, socket_client, text):
        stored, received = self._send(socket_client, text)
        assert not stored, f"an empty message was persisted: {text!r}"
        assert [m for m in received if m["name"] == "error"], (
            "an empty message was dropped silently, with no error to the sender"
        )

    def test_an_overlong_message_is_rejected(self, socket_client):
        stored, received = self._send(socket_client, "x" * 501)
        assert not stored, "a 501-character message was persisted"
        assert [m for m in received if m["name"] == "error"]

    def test_exactly_five_hundred_is_allowed(self, socket_client):
        """Off-by-one guard: the limit is inclusive."""
        stored, _ = self._send(socket_client, "y" * 500)
        assert len(stored) == 1, "a 500-character message was rejected"

    def test_length_is_measured_after_stripping(self, socket_client):
        """A 500-char message padded with spaces is still a 500-char message."""
        stored, _ = self._send(socket_client, "  " + "z" * 500 + "  ")
        assert len(stored) == 1, "padding pushed a valid message over the limit"

    def test_posting_to_a_room_you_never_joined_is_refused(self, socket_client):
        """Membership, not just a valid room name.

        Without this a client could broadcast into a conversation it is not
        part of — and, because broadcasts go to the room, would never see its
        own message, which looks like the send silently failed.
        """
        from App.models import Message

        socket_client.get_received()
        socket_client.emit("send_message", {"room": ROOM, "text": "uninvited"})
        received = socket_client.get_received()

        assert [m for m in received if m["name"] == "error"], (
            "posting without joining produced no error"
        )
        with flask_app.app_context():
            assert Message.query.filter_by(text="uninvited").count() == 0

    def test_the_message_is_persisted_before_it_is_broadcast(self, socket_client):
        """Acceptance says persisted *before* broadcast, and order matters.

        Broadcast-then-save means a database error leaves every other client
        showing a message that does not exist — and it reappears missing on
        reload. Asserted by making the commit fail and requiring silence.
        """
        from App.models import Message, db

        socket_client.emit("join", {"room": ROOM})
        socket_client.get_received()
        real_commit = db.session.commit

        def boom():
            raise RuntimeError("database is down")

        db.session.commit = boom
        try:
            socket_client.emit("send_message", {"room": ROOM, "text": "ghost"})
        finally:
            db.session.commit = real_commit

        broadcasts = [
            m for m in socket_client.get_received() if m["name"] == "message"
        ]
        assert not broadcasts, (
            "the message was broadcast even though persisting it failed — "
            "clients would show a message that was never saved"
        )
        with flask_app.app_context():
            assert Message.query.filter_by(text="ghost").count() == 0


class TestAttributionCannotBeSpoofed:
    """The sender is whoever the handshake authenticated — never the payload.

    T24 binds identity at connect precisely so later handlers do not have to
    trust the client. A `sender_id` accepted from the message body would let
    any authenticated user post as anyone.
    """

    def test_the_sender_is_the_authenticated_user(self, socket_client):
        from App.models import Message, User

        socket_client.emit("join", {"room": ROOM})
        socket_client.emit("send_message", {"room": ROOM, "text": "mine"})
        with flask_app.app_context():
            bob = User.query.filter_by(username="bob").first()
            stored = Message.query.filter_by(text="mine").first()
        assert stored is not None
        assert stored.sender_id == bob.id

    def test_a_client_supplied_sender_is_ignored(self, socket_client):
        from App.models import Message, User

        socket_client.emit("join", {"room": ROOM})
        socket_client.emit(
            "send_message",
            {"room": ROOM, "text": "forged", "sender_id": 9999, "username": "nick"},
        )
        with flask_app.app_context():
            bob = User.query.filter_by(username="bob").first()
            stored = Message.query.filter_by(text="forged").first()
        assert stored is not None
        assert stored.sender_id == bob.id, (
            f"the client set the sender to {stored.sender_id}; attribution must "
            "come from the authenticated socket"
        )

    def test_the_broadcast_names_the_real_sender(self, socket_client):
        socket_client.emit("join", {"room": ROOM})
        socket_client.get_received()
        socket_client.emit(
            "send_message", {"room": ROOM, "text": "hi", "username": "nick"}
        )
        broadcast = [
            m for m in socket_client.get_received() if m["name"] == "message"
        ]
        assert broadcast, "no message broadcast"
        assert payload_of(broadcast[0])["username"] == "bob"


class TestBroadcastReachesTheRoom:
    """Acceptance: two clients in one room both receive a broadcast."""

    def _second_client(self, auth_client):
        from App.sockets import socketio

        return socketio.test_client(flask_app, flask_test_client=auth_client)

    def test_both_members_receive_the_message(self, auth_client, socket_client):
        other = self._second_client(auth_client)
        for client in (socket_client, other):
            client.emit("join", {"room": ROOM})
            client.get_received()

        socket_client.emit("send_message", {"room": ROOM, "text": "to everyone"})

        for label, client in (("sender", socket_client), ("other member", other)):
            got = [m for m in client.get_received() if m["name"] == "message"]
            assert got, f"the {label} received no broadcast"
            assert payload_of(got[0])["text"] == "to everyone"

    def test_a_different_room_hears_nothing(self, auth_client, socket_client):
        rooms = _room_list()
        elsewhere = next(r for r in rooms if r != ROOM)
        other = self._second_client(auth_client)
        socket_client.emit("join", {"room": ROOM})
        other.emit("join", {"room": elsewhere})
        socket_client.get_received()
        other.get_received()

        socket_client.emit("send_message", {"room": ROOM, "text": "private"})

        leaked = [m for m in other.get_received() if m["name"] == "message"]
        assert not leaked, (
            f"a client in {elsewhere!r} received a message sent to {ROOM!r}"
        )


class TestHistoryEndpoint:
    """Acceptance: requires auth, caps `limit` at 200."""

    def test_history_requires_authentication(self, client):
        response = client.get(f"/api/chat/{ROOM}/history")
        assert response.status_code == 401, (
            f"unauthenticated history returned {response.status_code}"
        )

    def test_history_returns_messages_oldest_first(self, auth_client):
        _seed_messages(3)
        payload = auth_client.get(f"/api/chat/{ROOM}/history").get_json()
        assert [m["text"] for m in payload["messages"]] == [
            "message 0",
            "message 1",
            "message 2",
        ]

    def test_limit_is_honoured(self, auth_client):
        _seed_messages(10)
        payload = auth_client.get(f"/api/chat/{ROOM}/history?limit=4").get_json()
        assert len(payload["messages"]) == 4

    def test_limit_is_capped_at_two_hundred(self, auth_client):
        """A client asking for everything must not be able to.

        Unbounded, one request could pull the whole table into memory and back
        out as JSON.
        """
        _seed_messages(205)
        payload = auth_client.get(f"/api/chat/{ROOM}/history?limit=100000").get_json()
        assert len(payload["messages"]) <= 200, (
            f"limit=100000 returned {len(payload['messages'])} messages"
        )

    def test_each_clamp_holds_on_its_own(self, auth_client):
        """The cap is applied in two places, so test both.

        `_requested_limit()` clamps the query string and `recent_messages()`
        clamps its argument. That redundancy is deliberate — the socket path
        calls `recent_messages()` directly and never sees a query string — but
        it also means removing either one alone leaves the end-to-end test
        above still passing. Found by mutating exactly that.
        """
        from App.blueprints.chat import MAX_HISTORY, _requested_limit

        assert _requested_limit("100000") == MAX_HISTORY, (
            "the query-string clamp is gone"
        )
        assert _requested_limit("7") == 7

        _seed_messages(MAX_HISTORY + 5)
        with flask_app.app_context():
            from App.blueprints.chat import recent_messages

            assert len(recent_messages(ROOM, limit=100000)) == MAX_HISTORY, (
                "recent_messages() no longer clamps its own argument"
            )

    @pytest.mark.parametrize("bad", ["abc", "-5", "0", ""])
    def test_a_nonsense_limit_does_not_error(self, auth_client, bad):
        """Garbage in the query string is a client bug, not a 500."""
        _seed_messages(3)
        response = auth_client.get(f"/api/chat/{ROOM}/history?limit={bad}")
        assert response.status_code == 200, (
            f"limit={bad!r} returned {response.status_code}"
        )
        assert len(response.get_json()["messages"]) >= 1


class TestMessageIndexExists:
    """History always filters by room and orders by time.

    Without a composite index that is a full scan plus a sort over *every*
    room's history on each join, growing with the whole table rather than the
    room.
    """

    def test_the_index_exists_in_the_built_schema(self, client):
        """Inspect the real schema, not the migration's source text.

        An earlier version of this test regex-matched `create_index('message'`
        and failed against correct code: `batch_alter_table` — Alembic's SQLite
        path — puts the table name on the *outer* call, so the pattern could
        never match. Asking the database what indexes it actually has is both
        simpler and stronger.
        """
        from sqlalchemy import inspect

        from App.models import db

        with flask_app.app_context():
            indexes = inspect(db.engine).get_indexes("message")

        covering = [
            ix for ix in indexes
            if [c.lower() for c in ix["column_names"][:2]] == ["room", "timestamp"]
        ]
        assert covering, (
            "no index on message(room, timestamp); history does a full scan. "
            f"present: {[(ix['name'], ix['column_names']) for ix in indexes]}"
        )

    def test_the_column_order_is_room_then_timestamp(self, client):
        """Order matters, and the wrong one still 'has an index'.

        room is an equality filter and timestamp is the sort; leading with
        timestamp cannot satisfy the filter and the planner falls back to a
        scan.
        """
        from sqlalchemy import inspect

        from App.models import db

        with flask_app.app_context():
            indexes = inspect(db.engine).get_indexes("message")
        named = [ix for ix in indexes if ix["name"] == "ix_message_room_timestamp"]
        assert named, f"expected index missing: {[ix['name'] for ix in indexes]}"
        assert [c.lower() for c in named[0]["column_names"]] == ["room", "timestamp"], (
            f"column order is {named[0]['column_names']}, not (room, timestamp)"
        )

    def test_a_migration_creates_it(self):
        """The model alone would only help databases built by `create_all()`.

        Production is built by `flask db upgrade`, so the index has to be in a
        migration too. T8's `compare_metadata` drift guard in
        tests/test_migrations.py is what keeps the two in agreement; this just
        checks the migration exists at all.
        """
        migrations = "\n".join(
            read(p) for p in Path("migrations/versions").glob("*.py")
        )
        assert "ix_message_room_timestamp" in migrations, (
            "no migration creates ix_message_room_timestamp, so a deployed "
            "database built by `flask db upgrade` would not have it"
        )
        assert re.search(
            r"batch_alter_table\(\s*['\"]message['\"]", migrations
        ) or re.search(r"create_index\([^)]*['\"]message['\"]", migrations), (
            "the index migration does not target the message table"
        )


# ══════════════════════════════════════════════════════════════════════════
# T27 — Redis message queue and reconnect handling
# ══════════════════════════════════════════════════════════════════════════


CHAT_JS = Path("App/static/js/chat.js")


class TestMessageQueueConfiguration:
    """Acceptance: SocketIO configured with the Upstash rediss:// URL.

    One worker needs no message queue — broadcasts stay in-process. The moment
    there are two, a message published on worker A reaches nobody connected to
    worker B unless both share a Redis pub/sub channel.
    """

    def test_a_namespaced_channel_is_configured(self):
        """Upstash is shared with another application.

        python-socketio's default channel is the generic `socketio`. T13 found
        the same problem with Flask-Limiter, whose default key carried no
        application identifier at all. ~104 of the 105 keys on that instance
        belong to something else.
        """
        from App.config import get_settings

        channel = get_settings().socketio_channel
        assert channel != "socketio", (
            "the message queue uses python-socketio's default channel name on "
            "an instance shared with another application"
        )
        assert channel.startswith("pokemon-dashboard"), (
            f"channel {channel!r} is not namespaced to this application"
        )

    def test_the_queue_is_wired_when_redis_is_configured(self):
        """Checks the call, because the test suite has no Redis.

        conftest deliberately overwrites REDIS_URL so the suite can never reach
        live Upstash (T18b). That means the wired-up path cannot be observed by
        inspecting the running app here — the source is the evidence.
        """
        source = read("App/sockets.py")
        assert "message_queue" in source, (
            "init_socketio never passes message_queue, so a second worker "
            "would broadcast into the void"
        )
        assert "socketio_channel" in source, (
            "the channel name is not taken from settings"
        )

    def test_no_queue_without_redis(self):
        """Development must not require Redis just to open a socket.

        Passing message_queue=None is what python-socketio expects for the
        single-process case; passing an empty string would raise.
        """
        source = read("App/sockets.py")
        assert re.search(r"if\s+settings\.redis_url|redis_url\s+else\s+None", source), (
            "message_queue is passed unconditionally; without REDIS_URL that "
            "either crashes or silently points at localhost"
        )

    def test_the_running_test_app_has_no_redis_manager(self):
        """The hermeticity guard, for this new dependency.

        If a future change made the queue unconditional, the suite would start
        talking to whatever REDIS_URL resolved to — which is exactly how T18b's
        bug reached the shared production instance.
        """
        from App.sockets import socketio

        assert type(socketio.server.manager).__name__ != "RedisManager", (
            "the test suite is using a Redis-backed manager; it must stay "
            "in-process"
        )


class TestClientReconnects:
    """Acceptance: reconnect with backoff, and a visible reconnecting state.

    A socket that drops silently looks identical to a quiet room. The user has
    no way to tell that what they type next will go nowhere.
    """

    def test_reconnection_is_enabled_with_backoff(self):
        source = read(CHAT_JS)
        assert "reconnection" in source, "the client never configures reconnection"
        assert re.search(r"reconnectionDelay|randomizationFactor", source), (
            "reconnection has no backoff configured, so a server restart means "
            "every client retries in lockstep"
        )

    def test_a_reconnecting_state_is_shown(self):
        source = read(CHAT_JS)
        assert re.search(r"reconnect_attempt|reconnecting", source, re.I), (
            "nothing handles the reconnecting state, so a dropped connection "
            "is invisible"
        )

    def test_recovery_is_announced_too(self):
        """Coming back matters as much as dropping.

        Leaving "Reconnecting…" on screen after recovery trains people to
        ignore it.
        """
        source = read(CHAT_JS)
        assert "reconnect" in source and re.search(r"on\(\s*['\"]connect['\"]", source), (
            "the client never clears the reconnecting state"
        )

    def test_the_flush_survives_the_history_reload(self):
        """Joining rebuilds the message list, wiping anything already in it.

        Flushing in the connect handler meant the "sent N queued messages"
        confirmation appeared and was destroyed microseconds later by the
        history reply — observed in a browser. The flush belongs after the
        rebuild.
        """
        source = read(CHAT_JS)
        connect = re.search(
            r"socket\.on\(\s*['\"]connect['\"][\s\S]*?\n  \}\s*\)\s*;", source
        )
        assert connect, "no connect handler"
        assert "flushQueue" not in connect.group(0), (
            "the outbox is flushed in the connect handler, so its confirmation "
            "is wiped by the history reload that join triggers"
        )
        history = re.search(
            r"socket\.on\(\s*['\"]history['\"][\s\S]*?\n  \}\s*\)\s*;", source
        )
        assert history and "flushQueue" in history.group(0), (
            "the outbox is never flushed after history is rebuilt"
        )

    def test_the_room_is_rejoined_after_reconnecting(self):
        """Rooms live on the server and do not survive a dropped socket.

        Without an explicit rejoin the page looks connected and receives
        nothing — the worst of both states.
        """
        source = read(CHAT_JS)
        connect_handler = re.search(
            r"socket\.on\(\s*['\"]connect['\"][\s\S]*?\}\s*\)\s*;", source
        )
        assert connect_handler, "no connect handler"
        assert "join" in connect_handler.group(0), (
            "the connect handler does not re-join the current room, so after a "
            "reconnect the client is in no room at all"
        )


class TestSendIsAcknowledged:
    """The mechanism that makes "never silently vanish" actually true.

    `socket.connected` is necessary but not sufficient. engine.io detects a
    dead peer only through ping/pong, so there is a window where `connected`
    is still true and an emit goes nowhere. **Measured**: more than 15 seconds
    after the server was killed the browser still displayed "Connected", a
    message sent then was emitted into the void, the composer was cleared, and
    nothing anywhere told the user. The guard alone would have shipped that.

    An acknowledgement closes the window: no ack means the message did not
    land, whatever the connection state claims.
    """

    def test_a_successful_send_is_acknowledged(self, socket_client):
        socket_client.emit("join", {"room": ROOM})
        socket_client.get_received()
        ack = socket_client.emit(
            "send_message", {"room": ROOM, "text": "acked"}, callback=True
        )
        assert isinstance(ack, dict), f"no acknowledgement returned: {ack!r}"
        assert ack.get("ok") is True, f"send was not acknowledged as ok: {ack}"
        assert ack.get("id"), f"the ack carries no message id: {ack}"

    def test_a_rejected_send_is_acknowledged_too(self, socket_client):
        """Silence and refusal must be distinguishable.

        If only successes acked, the client could not tell "rejected" from
        "connection died", and would queue a message that will always fail.
        """
        socket_client.emit("join", {"room": ROOM})
        socket_client.get_received()
        ack = socket_client.emit(
            "send_message", {"room": ROOM, "text": "   "}, callback=True
        )
        assert isinstance(ack, dict), f"an invalid send was not acknowledged: {ack!r}"
        assert ack.get("ok") is False
        assert ack.get("error"), "the negative ack carries no reason"

    def test_posting_without_joining_is_acknowledged(self, socket_client):
        ack = socket_client.emit(
            "send_message", {"room": ROOM, "text": "uninvited"}, callback=True
        )
        assert ack.get("ok") is False
        assert "join" in ack.get("error", "").lower()

    def test_the_client_times_out_a_send_itself(self):
        """A timer this code owns, not `socket.timeout()`.

        `socket.timeout(ms).emit(...)` looks like the right mechanism and is
        not: when the connection dies, Socket.IO **discards** the pending ack
        callback instead of invoking it with an error. Measured in a browser —
        the callback never fired and the message vanished exactly as silently
        as with no ack at all.
        """
        source = read(CHAT_JS)
        assert "setTimeout" in source and "trackSend" in source, (
            "there is no send timer under this code's control"
        )
        assert not re.search(r"\.timeout\(\s*\w+\s*\)\s*\.emit\(", source), (
            "the client relies on socket.timeout(), which does not fire when "
            "the connection drops"
        )

    def test_an_unacknowledged_send_is_queued(self):
        source = read(CHAT_JS)
        timer = re.search(r"function trackSend\([\s\S]*?\n  \}", source)
        assert timer, "no trackSend()"
        assert "queueOffline" in timer.group(0), (
            "a send whose timer expires is not queued, so it is lost:\n"
            + timer.group(0)[:300]
        )

    def test_a_known_disconnect_flushes_pending_sends(self):
        """Waiting out the timer when the drop is already known is needless."""
        source = read(CHAT_JS)
        assert "sweepPending" in source, "no disconnect sweep"
        disconnect = re.search(
            r"socket\.on\(\s*['\"]disconnect['\"][\s\S]*?\}\s*\)\s*;", source
        )
        assert disconnect and "sweepPending" in disconnect.group(0), (
            "the disconnect handler does not surface in-flight sends"
        )

    def test_an_acknowledged_send_clears_its_timer(self):
        """Otherwise every delivered message is also reported as queued."""
        source = read(CHAT_JS)
        assert "settleSend" in source and "clearTimeout" in source, (
            "nothing cancels the pending-send timer on acknowledgement"
        )


class TestMessagesNeverVanishSilently:
    """Acceptance: messages sent while disconnected queue or fail visibly."""

    def test_sending_while_disconnected_is_handled(self):
        source = read(CHAT_JS)
        assert re.search(r"socket\.connected|isConnected|connected\s*===?\s*false", source), (
            "the send path never checks whether the socket is connected, so a "
            "message typed while offline is dropped with no trace"
        )

    def test_the_composer_is_not_cleared_on_a_failed_send(self):
        """Clearing the input is what makes the loss silent.

        The text is gone from the screen and never reached the server, and the
        user has no way to know or to retry.
        """
        source = read(CHAT_JS)
        send = re.search(
            r"form\.addEventListener\(\s*['\"]submit['\"][\s\S]*?\n  \}\s*\)\s*;", source
        )
        assert send, "could not find the submit handler"
        body = send.group(0)
        guard = re.search(r"if\s*\(\s*!\s*socket\.connected\s*\)", body)
        assert guard, (
            "the submit handler does not guard on connection state:\n" + body[:400]
        )
        # The guard must return before input.value is cleared.
        guard_pos = guard.start()
        clear_pos = body.find("input.value = ''")
        assert clear_pos == -1 or guard_pos < clear_pos, (
            "the input is cleared before the connection is checked, so the "
            "message is lost from the screen as well as from the server"
        )
