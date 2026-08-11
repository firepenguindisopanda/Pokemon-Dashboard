"""Chat is switched off, and "off" has to mean the socket too.

The obvious version of this change hides the nav link and returns a friendly
page at /chat. That closes the front door and leaves three others open:

    GET  /api/chat/<room>/history   the messages themselves
    GET  /api/chat/rooms            the room list
    SocketIO connect/join/send      the actual chat

The socket is the one that matters. It does not route through Flask's view
layer at all, so a guard on the page function does nothing to it — anyone who
had the page once, or who reads chat.js, can open a socket and talk. These
tests exist mostly to say that out loud.

`CHAT_ENABLED` is a real switch, not a deletion: the 1,554 lines of tests in
test_chat.py and test_chat_page.py still exercise the feature with it on, and
`TestTurningItBackOn` proves flipping it restores everything.
"""

import pytest

from App.app import app as flask_app, db
from App.models import Message

CHAT_UI_MARKERS = [
    'id="message-input"',
    'id="message-list"',
    'id="composer"',
    'id="room-select"',
]


@pytest.fixture(autouse=True)
def chat_disabled():
    """The production default. Set explicitly so the test does not depend on
    what happens to be in the environment."""
    previous = flask_app.config.get("CHAT_ENABLED")
    flask_app.config["CHAT_ENABLED"] = False
    yield
    flask_app.config["CHAT_ENABLED"] = previous


def socket_for(client):
    from App.sockets import socketio

    return socketio.test_client(flask_app, flask_test_client=client)


class TestTheChatPageIsClosed:
    def test_it_still_answers(self, auth_client):
        """200 with an explanation, not a 404.

        A 404 tells a logged-in user the page never existed; they followed a
        nav link there yesterday.
        """
        assert auth_client.get("/chat").status_code == 200

    def test_it_says_the_feature_is_coming(self, auth_client):
        body = auth_client.get("/chat").get_data(as_text=True).lower()
        assert "coming soon" in body

    @pytest.mark.parametrize("marker", CHAT_UI_MARKERS)
    def test_it_renders_no_chat_interface(self, auth_client, marker):
        body = auth_client.get("/chat").get_data(as_text=True)
        assert marker not in body, f"the disabled page still renders {marker}"

    def test_it_does_not_load_the_realtime_client(self, auth_client):
        """A page that loads chat.js will try to open a socket on arrival.

        The server refuses it, so nothing breaks — but it is a pointless
        request and a console error on a page whose whole message is
        "not yet".
        """
        body = auth_client.get("/chat").get_data(as_text=True)
        assert "socket.io" not in body
        assert "js/chat.js" not in body

    def test_it_is_still_behind_the_login(self, client):
        """Disabled is not the same as public."""
        assert client.get("/chat", follow_redirects=False).status_code != 200


class TestTheJsonApisAreClosed:
    """404 here, unlike the page: these are data endpoints, not somewhere a
    person navigates, and there is no message to show a fetch call."""

    def test_history_is_gone(self, auth_client):
        assert auth_client.get("/api/chat/general/history").status_code == 404

    def test_the_room_list_is_gone(self, auth_client):
        assert auth_client.get("/api/chat/rooms").status_code == 404

    def test_history_leaks_no_messages(self, auth_client):
        """The point of closing it: no message text in the response."""
        with flask_app.app_context():
            db.session.add(Message(room="general", sender_id=1, text="secret-plans"))
            db.session.commit()
        body = auth_client.get("/api/chat/general/history").get_data(as_text=True)
        assert "secret-plans" not in body


class TestTheSocketIsClosed:
    """The entry point a page-only fix leaves wide open."""

    @pytest.fixture(autouse=True)
    def _client(self, auth_client):
        """Held on the instance so parametrised cases can reach it."""
        type(self)._client = auth_client
        return auth_client

    def test_an_authenticated_socket_cannot_connect(self, auth_client):
        socket = socket_for(auth_client)
        assert not socket.is_connected(), (
            "a logged-in user can still open a chat socket while chat is "
            "disabled — the page guard does not cover SocketIO"
        )

    def test_the_connection_is_unusable_not_merely_silent(self):
        """Refused, not connected-but-ignored.

        flask_socketio raises `RuntimeError: not connected` for any operation
        on a rejected client, which is the contract worth asserting: there is
        no half-open state where a socket exists, receives broadcasts, and is
        simply not supposed to act.
        """
        socket = socket_for(self._client)
        with pytest.raises(RuntimeError, match="not connected"):
            socket.get_received()

    @pytest.mark.parametrize("event,payload", [
        ("join", {"room": "general"}),
        ("send_message", {"room": "general", "text": "should not land"}),
    ])
    def test_no_event_can_be_emitted(self, event, payload):
        socket = socket_for(self._client)
        with pytest.raises(RuntimeError, match="not connected"):
            socket.emit(event, payload)

    def test_nothing_reaches_the_database(self):
        """The assertion that matters is against the database, not the socket.

        A refused emit that still wrote a row would be a closed door with an
        open window.
        """
        with flask_app.app_context():
            before = db.session.query(Message).count()

        socket = socket_for(self._client)
        with pytest.raises(RuntimeError):
            socket.emit("send_message", {"room": "general", "text": "should not land"})

        with flask_app.app_context():
            assert db.session.query(Message).count() == before
            assert (
                db.session.query(Message)
                .filter(Message.text == "should not land")
                .first()
                is None
            )


class TestTheNavigationDoesNotOfferIt:
    def test_no_chat_link_in_the_navbar(self, auth_client):
        body = auth_client.get("/app").get_data(as_text=True)
        assert 'href="/chat"' not in body, (
            "the navbar still links to a feature that is switched off"
        )

    def test_other_pages_are_unaffected(self, auth_client):
        """Turning chat off must not disturb anything else."""
        for route in ("/app", "/pokemon-area", "/quiz", "/pokemon-ml"):
            assert auth_client.get(route).status_code == 200, route


class TestTurningItBackOn:
    """A switch, not a deletion — the feature is still there and still tested."""

    def test_the_page_returns(self, auth_client):
        flask_app.config["CHAT_ENABLED"] = True
        body = auth_client.get("/chat").get_data(as_text=True)
        assert 'id="message-input"' in body
        assert "coming soon" not in body.lower()

    def test_the_apis_return(self, auth_client):
        flask_app.config["CHAT_ENABLED"] = True
        assert auth_client.get("/api/chat/rooms").status_code == 200

    def test_the_socket_connects_again(self, auth_client):
        flask_app.config["CHAT_ENABLED"] = True
        assert socket_for(auth_client).is_connected()

    def test_the_nav_link_returns(self, auth_client):
        flask_app.config["CHAT_ENABLED"] = True
        assert 'href="/chat"' in auth_client.get("/app").get_data(as_text=True)


class TestTheDefaultIsOff:
    def test_the_setting_defaults_to_disabled(self):
        """Shipping this on by accident is the failure worth preventing."""
        from App.config import Settings

        assert Settings.model_fields["chat_enabled"].default is False
