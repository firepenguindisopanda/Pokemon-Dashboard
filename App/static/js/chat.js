/* Trainer chat client (T26).
 *
 * THE RULE THIS FILE EXISTS TO FOLLOW
 * Every message rendered here was typed by another user. That makes this the
 * only place in the application that displays genuinely untrusted input, and
 * the only correct way to put it on screen is `textContent`. There is not a
 * single assignment to innerHTML below, and a test enforces that — a message
 * of `<script>alert(1)</script>` must appear as those literal characters.
 *
 * The server already refuses empty and over-long messages; the client repeats
 * both checks so a mistake is caught before a round trip, never instead of the
 * server check.
 *
 * Depends on the Socket.IO browser client (loaded first by chat.html) and on
 * dashboard.js for readPageData().
 */

/* global io, readPageData */

(function () {
  'use strict';

  var page = readPageData('chat-page-data') || {};
  var MAX_LENGTH = page.max_length || 500;
  // How long to wait for the server's acknowledgement before treating a
  // message as undelivered. Generous enough for a slow round trip, short
  // enough that a user is not left wondering.
  var SEND_TIMEOUT_MS = 6000;
  var me = page.username;
  var currentRoom = page.default_room;

  var list = document.getElementById('message-list');
  var form = document.getElementById('composer');
  var input = document.getElementById('message-input');
  var counter = document.getElementById('char-counter');
  var status = document.getElementById('connection-status');
  var roomSelect = document.getElementById('room-select');

  if (!list || !form || !input) {
    return;   // not the chat page
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  /** Format an ISO timestamp as a short local time, with the full value in a
   *  title attribute so the date is still reachable. */
  function formatTime(iso) {
    if (!iso) return '';
    var when = new Date(iso);
    if (isNaN(when.getTime())) return '';
    return when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  /**
   * Build one message row.
   *
   * Everything variable goes in through textContent. Nothing here concatenates
   * a string into markup, which is what makes the XSS test pass by
   * construction rather than by escaping carefully enough.
   */
  function renderMessage(message) {
    var item = document.createElement('li');
    item.className = 'chat-message';
    if (message.username === me) {
      item.classList.add('chat-message--own');
    }

    var meta = document.createElement('div');
    meta.className = 'chat-message-meta';

    var who = document.createElement('span');
    who.className = 'chat-message-author';
    who.textContent = message.username;          // untrusted
    meta.appendChild(who);

    var time = document.createElement('time');
    time.className = 'chat-message-time';
    time.textContent = formatTime(message.timestamp);
    if (message.timestamp) {
      time.setAttribute('datetime', message.timestamp);
      time.title = new Date(message.timestamp).toLocaleString();
    }
    meta.appendChild(time);

    var body = document.createElement('p');
    body.className = 'chat-message-text';
    body.textContent = message.text;             // untrusted

    item.appendChild(meta);
    item.appendChild(body);
    return item;
  }

  function atBottom() {
    return list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  }

  function append(message, keepPosition) {
    var stick = keepPosition ? false : atBottom();
    list.appendChild(renderMessage(message));
    // Only auto-scroll when already at the bottom: yanking the view away from
    // someone reading back through history is worse than a missed message.
    if (stick || keepPosition) {
      list.scrollTop = list.scrollHeight;
    }
  }

  function clearMessages() {
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
  }

  function systemNote(text) {
    var item = document.createElement('li');
    item.className = 'chat-message chat-message--system';
    item.textContent = text;
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
  }

  // ── Composer ─────────────────────────────────────────────────────────────

  function updateCounter() {
    var used = input.value.length;
    counter.textContent = used + '/' + MAX_LENGTH;
    counter.classList.toggle('chat-counter--full', used >= MAX_LENGTH);
  }

  input.addEventListener('input', updateCounter);
  updateCounter();

  // ── Outbox ───────────────────────────────────────────────────────────────
  // Messages typed while the socket is down. Socket.IO buffers emits itself,
  // but silently and with no visible state, so a user cannot tell whether
  // anything was sent. Holding them here means the UI can say so, and
  // flushQueue() replays them once the connection returns.
  var outbox = [];

  function queueOffline(payload) {
    outbox.push(payload);
    systemNote('Not delivered — queued, will send when the connection returns.');
  }

  // Sends awaiting an acknowledgement, each with a timer this code owns.
  //
  // `socket.timeout(...).emit(...)` was the obvious mechanism and does not
  // work here: when the connection dies, Socket.IO discards the pending ack
  // callback rather than invoking it with an error. Measured — the callback
  // never fired, and the message was lost exactly as silently as before.
  //
  // A setTimeout this code controls always fires, whatever the library does
  // with its own bookkeeping.
  var pending = {};
  var nextSendId = 1;

  function trackSend(payload) {
    var id = nextSendId++;
    pending[id] = {
      payload: payload,
      timer: setTimeout(function () {
        if (!pending[id]) return;
        delete pending[id];
        queueOffline(payload);
      }, SEND_TIMEOUT_MS)
    };
    return id;
  }

  function settleSend(id) {
    var entry = pending[id];
    if (!entry) return;
    clearTimeout(entry.timer);
    delete pending[id];
  }

  function sweepPending() {
    // A known disconnect is better evidence than a timer that has not expired
    // yet, so surface those immediately rather than making the user wait.
    Object.keys(pending).forEach(function (id) {
      clearTimeout(pending[id].timer);
      var payload = pending[id].payload;
      delete pending[id];
      queueOffline(payload);
    });
  }

  function flushQueue() {
    if (!outbox.length) return;
    var pending = outbox.splice(0, outbox.length);
    pending.forEach(function (item) {
      socket.emit('send_message', item);
    });
    systemNote('Sent ' + pending.length + ' queued message'
               + (pending.length === 1 ? '' : 's') + '.');
  }

  // ── Connection ───────────────────────────────────────────────────────────

  function setStatus(text, state) {
    if (!status) return;
    status.textContent = text;
    status.className = 'chat-status' + (state ? ' chat-status--' + state : '');
  }

  // Cookies carry the JWT, and the handshake is where the server checks it —
  // an unauthenticated socket is rejected outright rather than connected.
  //
  // Backoff matters on a single-instance deploy: without it every open tab
  // retries on the same fixed interval, so a restart is met by a synchronised
  // stampede at the moment the server is least able to absorb it. Randomised
  // exponential backoff spreads them out.
  var socket = io({
    withCredentials: true,
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,        // first retry after ~1s
    reconnectionDelayMax: 10000,    // never wait longer than 10s
    randomizationFactor: 0.5        // +/-50% jitter, so tabs do not sync up
  });

  socket.on('connect', function () {
    setStatus('Connected', 'ok');
    // Rooms live on the server and do not survive a dropped socket. Without
    // this the page looks connected and receives nothing, which is worse than
    // an obvious failure. This runs on the first connect and every reconnect.
    //
    // The outbox is NOT flushed here. Joining triggers a `history` reply that
    // rebuilds the list from scratch, which would wipe the "sent N queued
    // messages" confirmation the moment it appeared — observed doing exactly
    // that. The flush happens in the history handler instead, once the list
    // has been rebuilt.
    socket.emit('join', { room: currentRoom });
  });

  socket.on('disconnect', function () {
    setStatus('Disconnected — reconnecting…', 'error');
    sweepPending();
  });

  socket.on('connect_error', function () {
    sweepPending();
  });

  socket.io.on('reconnect_attempt', function (attempt) {
    setStatus('Reconnecting… (attempt ' + attempt + ')', 'error');
  });

  socket.io.on('reconnect', function () {
    // Clearing the state matters as much as showing it: a permanent
    // "Reconnecting…" trains people to ignore the indicator entirely.
    setStatus('Reconnected', 'ok');
  });

  socket.on('connect_error', function () {
    // Most often a rejected handshake — the session expired, so the cookie no
    // longer verifies and no amount of retrying will help.
    setStatus('Not connected — try reloading', 'error');
  });

  socket.on('history', function (data) {
    if (!data || data.room !== currentRoom) return;
    clearMessages();
    (data.messages || []).forEach(function (message) {
      append(message, true);
    });
    if (!data.messages || data.messages.length === 0) {
      systemNote('No messages yet. Say something.');
    }
    // Now that the list is rebuilt, anything queued while offline can go —
    // and its confirmation will survive.
    flushQueue();
  });

  socket.on('message', function (message) {
    if (!message || message.room !== currentRoom) return;
    append(message);
  });

  socket.on('error', function (data) {
    var text = (data && data.error) || 'Something went wrong.';
    if (window.showError) {
      window.showError(text);
    } else {
      setStatus(text, 'error');
    }
  });

  // ── Actions ──────────────────────────────────────────────────────────────

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var text = input.value.trim();

    // Both checks mirror the server's. They exist to save a round trip, not to
    // replace it — the server validates again and is the authority.
    if (!text) return;
    if (text.length > MAX_LENGTH) return;

    var payload = { room: currentRoom, text: text };

    // The connection check comes BEFORE the input is cleared. Clearing first
    // is what makes a lost message silent: the text vanishes from the screen
    // and never reached the server, with nothing to retry from.
    if (!socket.connected) {
      queueOffline(payload);
      input.value = '';
      updateCounter();
      return;
    }

    // `socket.connected` is necessary but NOT sufficient. engine.io only
    // notices a dead peer through ping/pong, so there is a window — measured
    // at more than 15 seconds after the server was killed — where `connected`
    // is still true and an emit disappears with no error anywhere.
    //
    // The acknowledgement closes it: the server returns a value from its
    // handler, and an ack that never arrives is proof the message did not
    // land. Anything that fails goes to the outbox instead of nowhere.
    // Track before emitting: the ack can in principle arrive synchronously.
    var sendId = trackSend(payload);
    socket.emit('send_message', payload, function () {
      // Any acknowledgement — positive or negative — proves the message
      // reached the server, so it is not queued. A rejection already produced
      // an `error` event carrying the specific reason, and re-sending it would
      // fail identically.
      settleSend(sendId);
    });

    input.value = '';
    updateCounter();
    input.focus();
  });

  if (roomSelect) {
    roomSelect.addEventListener('change', function () {
      currentRoom = roomSelect.value;
      clearMessages();
      systemNote('Joining #' + currentRoom + '…');
      socket.emit('join', { room: currentRoom });
      input.setAttribute('placeholder', 'Message #' + currentRoom);
    });
  }
})();
