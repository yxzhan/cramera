/* ============================================================================
 * core/live-socket.js — the live bridge's WebSocket (see cramera.live.websocket).
 *
 * One connection carries the running world to the viewer — every new /state
 * snapshot, and the /markers overlay whenever it changes — and the viewer's drags
 * and headset poses back, instead of a request per frame each way. Through a proxy
 * and a slow link that is the difference between a view that updates a few times a
 * second and one that keeps up with the demo.
 *
 * Every update is acknowledged as it arrives; the bridge sends no more than a
 * couple ahead of the acknowledgements, so a slow link skips stale snapshots
 * instead of queueing them.
 *
 * The connection comes back by itself when it drops; while it is down the panel
 * falls back to polling, so nothing here is required for the view to work.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: how long to wait before reconnecting, in ms, doubled per failed attempt up to
  //: RECONNECT_MAX_MS
  const RECONNECT_MS = 500;
  const RECONNECT_MAX_MS = 5000;
  //: how long a request waits for its reply, in ms
  const REPLY_TIMEOUT_MS = 5000;

  //: The ws:// or wss:// url of the bridge at ``base``, which is a full http(s) url
  //: or a path on this page's origin.
  function socketUrl(base) {
    const url = new URL(base.replace(/\/+$/, '') + '/ws', global.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }

  //: Open a connection to the bridge at ``base()`` that stays open until ``close``.
  //:
  //: :param base: Returns the bridge's base url, read again on every reconnect.
  //: :param handlers: ``onUpdate(message)`` for each update, ``onOpen()`` and
  //:     ``onClose()`` as the connection comes and goes.
  function connect(base, handlers) {
    let socket = null;
    let wanted = true;
    let delay = RECONNECT_MS;
    let retry = null;
    let nextId = 1;
    const pending = new Map();        // request id -> {resolve, timer}

    function open() {
      retry = null;
      if (!wanted) return;
      try {
        socket = new global.WebSocket(socketUrl(base()));
      } catch (e) {
        schedule();
        return;
      }
      socket.onopen = function () {
        delay = RECONNECT_MS;
        if (handlers.onOpen) handlers.onOpen();
      };
      socket.onmessage = function (event) {
        let message;
        try { message = JSON.parse(event.data); } catch (e) { return; }
        if (message.type === 'update') {
          socket.send('{"type":"ack"}');
          if (handlers.onUpdate) handlers.onUpdate(message);
        } else if (message.type === 'reply') {
          const waiting = pending.get(message.id);
          if (!waiting) return;
          pending.delete(message.id);
          clearTimeout(waiting.timer);
          waiting.resolve(message.body);
        }
      };
      socket.onclose = function () {
        const was = socket;
        socket = null;
        pending.forEach(function (waiting) { clearTimeout(waiting.timer); waiting.resolve(null); });
        pending.clear();
        if (was && handlers.onClose) handlers.onClose();
        schedule();
      };
    }

    function schedule() {
      if (!wanted || retry) return;
      retry = setTimeout(open, delay);
      delay = Math.min(delay * 2, RECONNECT_MAX_MS);
    }

    function isOpen() {
      return !!socket && socket.readyState === global.WebSocket.OPEN;
    }

    //: Send one ``type`` message with ``body``, without waiting for an answer.
    //: Returns whether it went out; the caller falls back to HTTP when it did not.
    function send(type, body) {
      if (!isOpen()) return false;
      socket.send(JSON.stringify({ type: type, body: body }));
      return true;
    }

    //: Send one ``type`` message and resolve with the bridge's answer, or with null
    //: when none comes. Null right away when the connection is not open.
    function request(type, body) {
      if (!isOpen()) return Promise.resolve(null);
      const id = nextId++;
      return new Promise(function (resolve) {
        const timer = setTimeout(function () {
          pending.delete(id);
          resolve(null);
        }, REPLY_TIMEOUT_MS);
        pending.set(id, { resolve: resolve, timer: timer });
        socket.send(JSON.stringify({ type: type, id: id, body: body }));
      });
    }

    function close() {
      wanted = false;
      if (retry) { clearTimeout(retry); retry = null; }
      if (socket) {
        const was = socket;
        socket = null;
        was.onclose = null;
        was.close();
        if (handlers.onClose) handlers.onClose();
      }
    }

    open();
    return { isOpen: isOpen, send: send, request: request, close: close };
  }

  //: The connection the page's live view holds, if any, so other modules (the
  //: headset presence) can send over it instead of opening a second one.
  let shared = null;

  global.LiveSocket = {
    connect: connect,
    socketUrl: socketUrl,
    //: The page's connection, or null.
    shared: function () { return shared; },
    //: Set (or with null, clear) the page's connection.
    share: function (connection) { shared = connection; },
  };
})(window);
