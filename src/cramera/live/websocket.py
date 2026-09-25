"""
The live bridge's WebSocket, ``GET /ws``: the world streamed to a viewer, and the
viewer's drags and headset poses streamed back, on one connection.

Polling ``/state`` costs a full request per frame. Through a proxy -- JupyterHub's, and
a headset on a slow link behind it -- each one takes a round trip or more, so the view
updates only a few times a second however fast the demo runs, and every drag is its own
request queued behind the polls. Here the bridge sends each new snapshot as it is taken,
and the viewer's messages ride the same connection.

Server to viewer, one JSON text frame each:

- ``{"type": "update", "state": {...}, "markers": {...}}`` -- the newest ``/state``
  payload, and the ``/markers`` payload when the overlay changed since the last update
  (either key may be missing). The viewer answers every update with ``{"type": "ack"}``.
- ``{"type": "reply", "id": n, "body": {...}}`` -- the answer to a request that carried
  an ``id``.

Viewer to server:

- ``{"type": "move" | "joint" | "avatar", "body": {...}, "id"?: n}`` -- the body a
  ``POST /move``, ``/joint`` or ``/avatar`` would carry, validated and applied the same
  way; with an ``id``, answered with a ``reply``.
- ``{"type": "ack"}`` -- one update applied.

Updates are flow-controlled by the acks: no more than :data:`UPDATES_IN_FLIGHT` go out
unanswered. Without that a slow link fills the proxy's buffers with snapshots the viewer
works through long after they are stale -- the lag grows the longer the page is open.
With it the bridge skips the snapshots the viewer could not take, and what arrives is
never more than a round trip old.

A deliberately small server side of RFC 6455 on the bridge's own HTTP handler thread:
text, ping/pong and close frames, no extensions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time

from typing_extensions import Any, Callable, Dict, Optional, Tuple

from cramera.live.avatar import observe_avatar
from cramera.live.bridge import (
    Bridge,
    JointMoveRequest,
    MalformedJointMoveRequest,
    MalformedMoveRequest,
    MoveRequest,
)
from cramera.logging_setup import get_logger

logger = get_logger(__name__)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
"""The fixed key suffix of the handshake (RFC 6455, 1.3)."""

UPDATES_IN_FLIGHT = 2
"""Updates sent and not yet acknowledged, at most."""

POLL_SECONDS = 1.0 / 120.0
"""How often the sender looks for a new snapshot."""

UPDATE_INTERVAL_SECONDS = 1.0 / 60.0
"""The shortest time between two updates: a headset draws at most this often, and
every state applied beyond that is work taken from drawing."""

IDLE_RESEND_SECONDS = 0.1
"""How often the state is re-read while no new snapshot is taken: an idle sim overlays
the queued viewer moves on its last snapshot (see ``Bridge.get_state``), which changes
the payload without a new sequence number."""

PING_SECONDS = 15.0
"""A ping after this long without sending, so no proxy takes the connection for idle."""

MAX_MESSAGE_BYTES = 1 << 20
"""The largest message a viewer may send."""

OP_CONTINUATION, OP_TEXT, OP_BINARY = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA


class ConnectionClosed(Exception):
    """The viewer went away, or broke the protocol."""


def accept_key(key: str) -> str:
    """
    The ``Sec-WebSocket-Accept`` answering a handshake's ``Sec-WebSocket-Key``.

    :param key: The key the viewer sent.
    """
    digest = hashlib.sha1((key.strip() + GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def encode_frame(opcode: int, payload: bytes) -> bytes:
    """
    One final, unmasked frame, as a server sends it.

    :param opcode: The frame's opcode.
    :param payload: The frame's payload.
    """
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


def _read_exactly(stream: Any, count: int) -> bytes:
    data = stream.read(count)
    if data is None or len(data) < count:
        raise ConnectionClosed("the viewer hung up")
    return data


def read_frame(stream: Any) -> Tuple[bool, int, bytes]:
    """
    One frame from a viewer: ``(final, opcode, unmasked payload)``.

    :param stream: The connection's buffered reader.
    """
    first, second = _read_exactly(stream, 2)
    final, opcode = bool(first & 0x80), first & 0x0F
    if not second & 0x80:
        raise ConnectionClosed("a viewer's frame must be masked")
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _read_exactly(stream, 2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _read_exactly(stream, 8))
    if length > MAX_MESSAGE_BYTES:
        raise ConnectionClosed(f"a frame of {length} bytes is too large")
    mask = _read_exactly(stream, 4)
    payload = bytearray(_read_exactly(stream, length))
    for index in range(length):
        payload[index] ^= mask[index % 4]
    return final, opcode, bytes(payload)


def queue_move(bridge: Bridge, body: Any) -> Tuple[Dict[str, Any], int]:
    """
    Validate and queue an object move, as ``POST /move`` does.

    :param bridge: The bridge to queue it on.
    :param body: The decoded request body.
    :return: The answer and its HTTP status.
    """
    if not isinstance(body, dict):
        return {"ok": False, "error": "body must be a JSON object"}, 400
    try:
        move = MoveRequest.from_payload(body)
    except MalformedMoveRequest as error:
        return {"ok": False, "error": str(error)}, 400
    bridge.queue_move(move)
    return {"ok": True}, 200


def queue_joint_move(bridge: Bridge, body: Any) -> Tuple[Dict[str, Any], int]:
    """
    Validate and queue a joint set by hand, as ``POST /joint`` does.

    :param bridge: The bridge to queue it on.
    :param body: The decoded request body.
    :return: The answer and its HTTP status.
    """
    if not isinstance(body, dict):
        return {"ok": False, "error": "body must be a JSON object"}, 400
    try:
        request = JointMoveRequest.from_payload(body)
    except MalformedJointMoveRequest as error:
        return {"ok": False, "error": str(error)}, 400
    bridge.queue_joint_move(request)
    return {"ok": True}, 200


REQUESTS: Dict[str, Callable[[Bridge, Any], Tuple[Dict[str, Any], int]]] = {
    "move": queue_move,
    "joint": queue_joint_move,
    "avatar": observe_avatar,
}
"""What each request type a viewer sends does."""


class LiveSocket:
    """
    One viewer's connection, after the handshake.

    The handler thread that accepted it runs :meth:`serve`, the sender; a second
    thread reads the viewer's messages. Either one ending ends both.

    :param bridge: The bridge streamed.
    :param connection: The socket.
    :param reader: The buffered reader the HTTP handler read the handshake from, which
        may already hold the viewer's first frames.
    """

    def __init__(self, bridge: Bridge, connection: socket.socket, reader: Any) -> None:
        self.bridge = bridge
        self.connection = connection
        self.reader = reader
        self._send_lock = threading.Lock()
        self._acked = threading.Condition()
        self._in_flight = 0
        self._closed = threading.Event()
        self._last_send = time.monotonic()

    # %% sending
    def _send(self, opcode: int, payload: bytes) -> None:
        frame = encode_frame(opcode, payload)
        with self._send_lock:
            self.connection.sendall(frame)
            self._last_send = time.monotonic()

    def _send_json(self, message: Dict[str, Any]) -> None:
        self._send(OP_TEXT, json.dumps(message, separators=(",", ":")).encode())

    def serve(self) -> None:
        """
        Stream updates until the viewer goes away.
        """
        threading.Thread(
            target=self._receive, daemon=True, name="live-ws-reader"
        ).start()
        try:
            self._stream()
        except (ConnectionClosed, OSError):
            pass
        finally:
            self.close()

    def _stream(self) -> None:
        last_sequence: Optional[int] = None
        last_state: Optional[str] = None
        last_read = 0.0
        last_markers: Optional[int] = None
        while not self._closed.is_set():
            with self._acked:
                while (
                    self._in_flight >= UPDATES_IN_FLIGHT and not self._closed.is_set()
                ):
                    self._acked.wait(PING_SECONDS)
                    if self._in_flight >= UPDATES_IN_FLIGHT:
                        self._ping_if_quiet()
            if self._closed.is_set():
                return
            now = time.monotonic()
            sequence = self.bridge.state.sequence_number
            message: Dict[str, Any] = {}
            if sequence != last_sequence or now - last_read >= IDLE_RESEND_SECONDS:
                last_read = now
                state = self.bridge.get_state()
                encoded = json.dumps(state, separators=(",", ":"))
                if encoded != last_state:
                    last_state, last_sequence = encoded, sequence
                    message["state"] = state
                version = state.get("markersVersion")
                if version != last_markers:
                    last_markers = version
                    message["markers"] = self.bridge.get_markers()
            if message:
                message["type"] = "update"
                with self._acked:
                    self._in_flight += 1
                self._send_json(message)
                self._closed.wait(UPDATE_INTERVAL_SECONDS)
            else:
                self._ping_if_quiet()
                self._closed.wait(POLL_SECONDS)

    def _ping_if_quiet(self) -> None:
        if time.monotonic() - self._last_send > PING_SECONDS:
            self._send(OP_PING, b"")

    # %% receiving
    def _receive(self) -> None:
        try:
            fragments: list = []
            while not self._closed.is_set():
                final, opcode, payload = read_frame(self.reader)
                if opcode == OP_CLOSE:
                    self._send(OP_CLOSE, payload[:2])
                    return
                if opcode == OP_PING:
                    self._send(OP_PONG, payload)
                    continue
                if opcode == OP_PONG:
                    continue
                if opcode in (OP_TEXT, OP_BINARY):
                    fragments = [payload]
                elif opcode == OP_CONTINUATION:
                    fragments.append(payload)
                if final and fragments:
                    self._handle(b"".join(fragments))
                    fragments = []
        except (ConnectionClosed, OSError):
            pass
        except Exception:
            logger.exception("live websocket reader failed")
        finally:
            self.close()

    def _handle(self, data: bytes) -> None:
        try:
            message = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "ack":
            with self._acked:
                self._in_flight = max(0, self._in_flight - 1)
                self._acked.notify()
            return
        handler = REQUESTS.get(kind)
        if handler is None:
            answer: Dict[str, Any] = {"ok": False, "error": f"unknown type {kind!r}"}
        else:
            answer, _ = handler(self.bridge, message.get("body"))
            if not answer.get("ok", True) and "id" not in message:
                logger.debug("live websocket %s refused: %s", kind, answer.get("error"))
        if "id" in message:
            self._send_json({"type": "reply", "id": message["id"], "body": answer})

    def close(self) -> None:
        """
        End the connection; safe to call from either thread, and more than once.
        """
        if self._closed.is_set():
            return
        self._closed.set()
        with self._acked:
            self._acked.notify_all()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
