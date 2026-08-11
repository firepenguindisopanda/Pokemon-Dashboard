"""Chat rooms: validation, persistence and the history API.

The socket handlers live in `App/sockets.py`; everything they need in order to
be *correct* lives here, so the HTTP endpoint and the WebSocket path cannot
drift apart. Both read history through `recent_messages()` and both validate
through `clean_message_text()`.

Two rules that are load-bearing rather than stylistic:

**Rooms are a whitelist.** A name outside `CHAT_ROOMS` is rejected, never
created. Auto-creating on join means a typo silently forks a conversation —
two people in `genral` and `general` each see an empty room and conclude chat
is broken — and it lets any client create unbounded rooms.

**Ordering is `(timestamp, id)`, never `timestamp` alone.** `Message.timestamp`
is a database-side default, and *both* backends produce ties — measured, not
assumed. SQLite's CURRENT_TIMESTAMP is whole seconds, so a burst of separately
committed messages shares one value. Postgres' `now()` is the transaction start
time, so anything written in one transaction shares a value regardless of clock
precision. Neither guarantees an order for ties, so sorting on timestamp alone
makes "the last 50, oldest first" undefined — and SQLite hides it by happening
to return rowid order, which is exactly how a Postgres-only bug ships green.
"""

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_jwt_extended import current_user, jwt_required

from App.constants import CHAT_ROOMS
from App.models import Message, User, db

chat_bp = Blueprint("chat", __name__)

# Longest message a client may send. Enforced after stripping, so trailing
# whitespace cannot push an otherwise-valid message over the edge.
MAX_MESSAGE_LENGTH = 500

# What a join hands back, and the ceiling on an explicit `?limit=`.
DEFAULT_HISTORY = 50
MAX_HISTORY = 200


class ChatError(ValueError):
    """A client-correctable problem, safe to send back verbatim."""


def is_valid_room(room):
    """True when `room` is one of the configured rooms."""
    return room in CHAT_ROOMS


def require_room(room):
    """Raise unless `room` is on the whitelist.

    Raises:
        ChatError: naming the room, so a typo is obvious to the sender.
    """
    if not is_valid_room(room):
        raise ChatError(f"Unknown room: {room!r}")
    return room


def clean_message_text(text):
    """Validate and normalise a message body.

    Args:
        text: Whatever the client sent. Any type; only strings can be valid.

    Returns:
        The stripped message.

    Raises:
        ChatError: if it is not a non-empty string of at most 500 characters
            once stripped.
    """
    if not isinstance(text, str):
        raise ChatError("Message must be text.")

    cleaned = text.strip()
    if not cleaned:
        raise ChatError("Message cannot be empty.")
    # Length is checked *after* stripping: "  " + 500 chars + "  " is a valid
    # 500-character message, not an oversized one.
    if len(cleaned) > MAX_MESSAGE_LENGTH:
        raise ChatError(
            f"Message is too long ({len(cleaned)} characters; "
            f"the limit is {MAX_MESSAGE_LENGTH})."
        )
    return cleaned


def serialise(message, username):
    """Shape a Message for the wire. One definition, both transports."""
    return {
        "id": message.id,
        "room": message.room,
        "text": message.text,
        "username": username,
        "timestamp": message.timestamp.isoformat() if message.timestamp else None,
    }


def recent_messages(room, limit=DEFAULT_HISTORY):
    """The most recent `limit` messages in `room`, returned oldest-first.

    The query orders **descending** to let the database use the index and stop
    after `limit` rows, then reverses in Python. Ordering ascending and taking
    `.limit()` would return the *oldest* messages instead — the opposite — and
    would still pass a naive length check.

    Args:
        room: A whitelisted room name.
        limit: How many to return, clamped to MAX_HISTORY.

    Returns:
        A list of wire-shaped dicts, oldest first.
    """
    limit = max(1, min(int(limit), MAX_HISTORY))
    rows = (
        db.session.query(Message, User.username)
        .join(User, Message.sender_id == User.id)
        # `id` is the tie-break. See the module docstring: timestamp alone is
        # ambiguous on SQLite, where a burst of chat shares one second.
        .filter(Message.room == room)
        .order_by(Message.timestamp.desc(), Message.id.desc())
        .limit(limit)
        .all()
    )
    return [serialise(message, username) for message, username in reversed(rows)]


def store_message(room, sender_id, text):
    """Persist one message and return it wire-shaped.

    Persisting *before* broadcasting is deliberate and is asserted by a test:
    broadcast-first means a failed commit leaves every other client displaying
    a message that does not exist and that vanishes on reload.

    Raises:
        ChatError: if the room or text is invalid.
    """
    require_room(room)
    cleaned = clean_message_text(text)

    message = Message(sender_id=sender_id, room=room, text=cleaned)
    db.session.add(message)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    username = db.session.get(User, sender_id).username
    return serialise(message, username)


def _requested_limit(raw):
    """Read `?limit=`, tolerating anything a client might send.

    A malformed query string is a client bug, not a server error — falling
    through to the default beats a 500.
    """
    try:
        return max(1, min(int(raw), MAX_HISTORY))
    except (TypeError, ValueError):
        return DEFAULT_HISTORY


def chat_is_enabled():
    """Whether the chat feature is switched on.

    Read from `app.config` rather than the Settings object so there is one
    source of truth at request time and tests can flip it. The socket handshake
    in App/sockets.py checks the same key — the four entry points must agree,
    because closing three of them is the same as closing none.
    """
    return current_app.config.get("CHAT_ENABLED", False)


@chat_bp.route("/api/chat/<room>/history", methods=["GET"])
@jwt_required()
def room_history(room):
    """Return a room's recent messages, oldest first.

    Authenticated: chat is not public, and this is the same data the socket
    hands out on join.
    """
    # 404 rather than the page's friendly 200: nobody navigates here, and a
    # fetch has nothing to do with a "coming soon" message.
    if not chat_is_enabled():
        abort(404)

    if not is_valid_room(room):
        # 404 rather than an empty 200: an unknown room does not exist, and
        # answering with `{"messages": []}` would make a typo look like a quiet
        # room instead of a mistake.
        return jsonify({"error": f"Unknown room: {room}"}), 404

    limit = _requested_limit(request.args.get("limit"))
    return jsonify({"room": room, "messages": recent_messages(room, limit)})


@chat_bp.route("/api/chat/rooms", methods=["GET"])
@jwt_required()
def list_rooms():
    """The room whitelist, so the client never has to hard-code it."""
    if not chat_is_enabled():
        abort(404)
    return jsonify({"rooms": list(CHAT_ROOMS)})


@chat_bp.route("/chat", methods=["GET"])
@jwt_required()
def chat_page():
    """Render the chat page.

    The viewer's identity is passed server-side so the client can mark its own
    messages without trusting anything typed in the browser — a username held
    only in JavaScript would be spoofable and would go stale after a rename.

    History is *not* rendered here. The socket delivers it on join, which keeps
    one code path for "the last 50 messages" instead of a server-rendered list
    that has to agree with a socket-delivered one.
    """
    # A page, so a page answer: 200 with an explanation. A 404 would tell a
    # logged-in user the feature never existed, when they followed a nav link
    # here yesterday. The template loads neither socket.io nor chat.js, so the
    # browser does not open a socket the server would only refuse.
    if not chat_is_enabled():
        return render_template("chat_coming_soon.html")

    page_data = {
        "username": current_user.username,
        "user_id": current_user.id,
        "rooms": list(CHAT_ROOMS),
        "default_room": CHAT_ROOMS[0],
        "max_length": MAX_MESSAGE_LENGTH,
    }
    return render_template("chat.html", rooms=CHAT_ROOMS, page_data=page_data)
