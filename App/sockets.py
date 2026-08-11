"""Socket.IO server and the authenticated handshake.

WHY THERE IS NO MONKEY PATCH HERE
The plan called for eventlet. Three async modes were measured before this was
written, and the maintainer chose the one that leaves the rest of the app
alone:

  - eventlet 0.41.1 runs on Python 3.12 but now emits an official
    EventletDeprecationWarning telling you not to use it for new projects.
  - eventlet and gevent both require `monkey_patch()` before any other import,
    and once patched, psycopg2 blocks the entire worker: measured against live
    Neon, a 2s query let a 100ms ticker fire **once** instead of 25 times.
    A single slow query would stall every HTTP request and every WebSocket
    heartbeat on Render's one worker. `psycogreen` fixes that, and nothing in
    the plan said it was needed.
  - `async_mode="threading"` needs no patching. psycopg2, SQLAlchemy, Redis
    and all 38 existing routes keep exactly the code path they have today,
    which is what turns "the highest deploy risk in the plan" into a change
    that cannot affect a request that is not a socket.

The trade is a thread per open connection instead of a greenlet — tens of
concurrent clients rather than thousands. For this app that ceiling is far
above the traffic, and the risk reduction is worth more than the headroom.

Real WebSocket transport still works: python-engineio serves it through
simple-websocket, which is already a dependency. Without that package the
server silently downgrades to long-polling, so a test asserts the handshake
advertises a websocket upgrade.

RUNNING IT
`gunicorn --worker-class gthread --threads 8 wsgi:app`. The default sync worker
cannot hold a connection open. No `socketio.run()` in production; the Socket.IO
endpoint is WSGI middleware installed on `app.wsgi_app`, so plain `wsgi:app` is
the right target.
"""

import logging

from flask import current_app, request
from flask_jwt_extended import verify_jwt_in_request
from flask_socketio import SocketIO, emit, join_room

from App.auth_helpers import current_user_id
from App.blueprints.chat import (
    ChatError,
    recent_messages,
    require_room,
    store_message,
)
from App.models import User, db

logger = logging.getLogger(__name__)

# Constructed unbound so `create_app()` owns initialisation, matching how
# `limiter` and `db` are wired.
socketio = SocketIO()


def _cors_origins(settings):
    """Translate the app's CORS policy into what python-socketio expects.

    Socket.IO does **not** inherit Flask-CORS. Left alone it defaults to `*`,
    which would hand any origin a credentialed socket while the HTTP side is
    locked to an allowlist. python-socketio wants the bare string `"*"` rather
    than `["*"]`, so the wildcard case is translated rather than passed through.
    """
    origins = settings.cors_origin_list
    if "*" in origins:
        return "*"
    return origins


def init_socketio(app, settings):
    """Attach the Socket.IO server to an application.

    Installs WSGI middleware on ``app.wsgi_app`` rather than a URL rule, which
    is why nothing appears in ``app.url_map``.

    Args:
        app: The Flask application.
        settings: The resolved ``Settings`` object, for the CORS policy.

    Returns:
        The initialised ``SocketIO`` instance.
    """
    # One worker needs no message queue — broadcasts stay in-process. With
    # two, a message published on worker A reaches nobody on worker B unless
    # both share a Redis pub/sub channel.
    #
    # None, not "", when Redis is absent: python-socketio treats any string as
    # a URL and an empty one raises. Development and the test suite therefore
    # run entirely in-process, which is also what keeps the suite off the
    # shared Upstash instance (see T18b).
    #
    # Verified to work in threading mode: the manager resolves to RedisManager
    # and starts its listener thread.
    message_queue = settings.redis_url if settings.redis_url else None
    if message_queue:
        logger.info(
            "Socket.IO message queue enabled on channel %s",
            settings.socketio_channel,
        )

    socketio.init_app(
        app,
        async_mode="threading",
        cors_allowed_origins=_cors_origins(settings),
        message_queue=message_queue,
        channel=settings.socketio_channel,
        # Defaults are 25s/20s, so a dropped peer can go unnoticed for ~45s.
        # 15s/10s halves that. Acknowledgements are what actually make a lost
        # send visible; this just shortens how long the status indicator lies.
        ping_interval=15,
        ping_timeout=10,
        # NOT the default, and not optional. Flask-SocketIO's manage_session=True
        # path does `ctx.session = session_obj`, and Flask 3.1 made
        # RequestContext.session a read-only property — so the default raises
        # `AttributeError: property 'session' of 'RequestContext' object has no
        # setter` on *every* connection, before any handler runs. Verified
        # against Flask 3.1.3 / Flask-SocketIO 5.5.1.
        #
        # With it False, handlers see the ordinary Flask session and socket
        # identity is kept in _CONNECTIONS below.
        manage_session=False,
    )
    return socketio


# Per-connection identity, keyed by Socket.IO session id.
#
# Not `socketio.server.save_session()`, which is the obvious choice and does
# work at runtime — but flask_socketio's test client never registers an
# engine.io socket, so reading it back raises KeyError('Session not found')
# and the binding becomes untestable. A plain dict behaves identically in both,
# and a socket only ever lives on the worker that accepted it, so there is
# nothing here that wants sharing between processes.
_CONNECTIONS = {}


def _save_identity(sid, user):
    """Bind an authenticated user to one open connection."""
    _CONNECTIONS[sid] = {
        "user_id": user.id,
        "username": user.username,
        # Rooms this connection has joined. Posting is limited to these, so a
        # client cannot broadcast into a room it is not in — and cannot end up
        # sending a message it will never receive itself.
        "rooms": set(),
    }


def connected_user(sid=None):
    """Return ``{'user_id', 'username'}`` for a connection, or None.

    The handshake is the only place authentication happens, so every later
    handler reads identity from here rather than re-deriving it from cookies.
    T25's message attribution depends on this.
    """
    return _CONNECTIONS.get(sid or request.sid)


@socketio.on("connect")
def handle_connect(auth=None):
    """Authenticate the handshake, or refuse the connection outright.

    Returning False makes python-socketio reject the connection. That is the
    important part of the contract: a socket that connects but is "not allowed
    to do anything" is still an open socket that receives broadcasts. There is
    no such state here — an unauthenticated client never completes the
    handshake.

    The JWT is read from cookies because that is where this app puts it; the
    browser attaches them to the handshake automatically, so the client needs
    no token-passing code. `verify_jwt_in_request` checks the signature and
    expiry, not merely that a cookie is present.

    Returns:
        False to reject. Anything else accepts.
    """
    # The feature flag is checked HERE, before authentication, and this is the
    # only place it can be checked effectively.
    #
    # A socket does not pass through Flask's view layer, so guarding the /chat
    # view function does nothing to it. Anyone who loaded the page while it was
    # open — or who simply reads chat.js — can still open a socket, join a room
    # and broadcast to everyone else who did the same. Refusing the handshake
    # is what makes `join` and `send_message` unreachable rather than merely
    # unadvertised.
    if not current_app.config.get("CHAT_ENABLED", False):
        logger.info("Socket handshake rejected: chat is disabled")
        return False

    try:
        verify_jwt_in_request(locations=["cookies"])
    except Exception as exc:
        # Every failure mode lands here: missing cookie, bad signature, expired
        # token. The reason is logged, never sent to the client.
        logger.info("Socket handshake rejected: %s", type(exc).__name__)
        return False

    user_id = current_user_id()
    if user_id is None:
        logger.info("Socket handshake rejected: token carried no usable subject")
        return False

    user = db.session.get(User, user_id)
    if user is None:
        # A token signed for a user who has since been deleted.
        logger.info("Socket handshake rejected: user %s no longer exists", user_id)
        return False

    _save_identity(request.sid, user)
    logger.info("Socket connected: %s (%s)", user.username, request.sid)
    emit("connected", {"username": user.username, "user_id": user.id})
    return True


@socketio.on("disconnect")
def handle_disconnect(reason=None):
    """Release the connection's identity.

    Nothing else evicts from `_CONNECTIONS`, so skipping this would leak an
    entry per connection for the process's lifetime — a slow memory leak on a
    long-running worker, and exactly the kind of thing that never shows up in
    a test suite.
    """
    identity = _CONNECTIONS.pop(request.sid, None)
    if identity:
        logger.info("Socket disconnected: %s", identity.get("username"))


# ── Rooms (T25) ────────────────────────────────────────────────────────────
#
# Validation, persistence and history all live in App/blueprints/chat.py so the
# HTTP endpoint and these handlers cannot disagree about what a valid room is,
# what a valid message is, or what order history comes back in.


def _fail(message):
    """Tell the sender why, without disturbing anyone else in the room."""
    emit("error", {"error": str(message)})


@socketio.on("join")
def handle_join(data=None):
    """Join a room and receive its recent history.

    Rejects unknown rooms rather than creating them: an auto-created room
    silently forks a conversation on a typo, and lets a client mint rooms
    without limit.
    """
    identity = connected_user()
    if identity is None:
        # Only reachable if a socket outlived its handshake state.
        return _fail("Not authenticated.")

    room = (data or {}).get("room")
    try:
        require_room(room)
    except ChatError as exc:
        logger.info("Join refused for %s: %s", identity["username"], exc)
        return _fail(exc)

    join_room(room)
    identity["rooms"].add(room)
    emit("history", {"room": room, "messages": recent_messages(room)})
    logger.info("%s joined %s", identity["username"], room)


@socketio.on("send_message")
def handle_send_message(data=None):
    """Persist a message, then broadcast it to the room.

    The sender is taken from the authenticated connection, never from the
    payload. T24 binds identity at the handshake precisely so that a client
    cannot claim to be someone else here — a trusted `sender_id` field would
    let any logged-in user post as anybody.

    Persist first: broadcasting before the commit means a database failure
    leaves every other client showing a message that was never saved and that
    disappears on reload.
    """
    identity = connected_user()
    if identity is None:
        _fail("Not authenticated.")
        return {"ok": False, "error": "Not authenticated."}

    payload = data or {}
    room = payload.get("room")

    # Membership, not just a valid name. Broadcasting goes to the room, so a
    # sender who never joined would not receive their own message — and could
    # post into a conversation they are not part of.
    if room not in identity["rooms"]:
        message = f"Join {room!r} before posting to it."
        _fail(message)
        return {"ok": False, "error": message}

    try:
        message = store_message(
            room=room,
            sender_id=identity["user_id"],   # not payload["sender_id"]
            text=payload.get("text"),
        )
    except ChatError as exc:
        _fail(exc)
        return {"ok": False, "error": str(exc)}
    except Exception:
        # A real failure: log it, tell the sender something generic, and do
        # NOT broadcast — nothing was stored.
        logger.exception("Failed to store a chat message")
        generic = "Message could not be sent. Please try again."
        _fail(generic)
        return {"ok": False, "error": generic}

    emit("message", message, to=message["room"])
    # The return value becomes the client's acknowledgement. It is what closes
    # the window where `socket.connected` is still true but the peer is gone:
    # engine.io only notices a dead connection via ping/pong timeout, which
    # with the default 25s interval + 20s timeout can take ~45 seconds.
    # Measured: 15s after the server was killed the browser still reported
    # "Connected" and a message emitted then vanished with no trace. An ack
    # that never arrives is proof the message did not land.
    return {"ok": True, "id": message["id"]}
