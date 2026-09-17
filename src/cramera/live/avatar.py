"""
Where a headset viewer is standing, as markers in the running demo's world.

Someone who enters VR joins the demo the way every other CRAM component announces
itself to the scene: by publishing markers. :mod:`cramera.live.markers` reads marker
messages duck-typed and is deliberately free of ROS imports, so a pose a browser
POSTs becomes a marker without a ROS round trip — and every viewer already renders
it, the desktop page included, with no client change at all.

A viewer is drawn as the parts the headset actually tracks: a block for the head and
one for each controller it is reporting. The parts arrive together in one post so
the whole viewer moves in one step, and :data:`PART_SHAPES` is the one place that
says what each is drawn as — the ``visualization_msgs`` vocabulary has no mesh
marker the overlay would render (see :data:`cramera.live.markers.MARKER_KINDS`), so
these are primitives by necessity.

Avatars live in the bridge's marker stores, which the live bridge owns, so they are
gone when the demo is. Within a run they are dropped when the headset says it left,
and swept when one goes quiet — a browser that crashes mid-session never gets to say
goodbye. The sweep runs on each post, so the last viewer to go quiet is only
collected once somebody else moves; a run that ends with a stale arrow in it is
showing a viewer who really was there, which is the harmless direction to fail in.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from typing_extensions import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from cramera.live.markers import ADD_ACTION, DELETE_ACTION
from cramera.logging_setup import get_logger

if TYPE_CHECKING:
    from cramera.live.bridge import Bridge

logger = get_logger(__name__)

AVATAR_TOPIC = "/cramera/vr_viewer"
"""
The topic the avatars are kept under, separate from the CRAM systems' own so that
turning their marker topics off in the viewer does not take the people with them.
"""

AVATAR_NAMESPACE = "vr_viewer"
"""
The marker namespace every avatar shares; the viewer's marker settings list it by
this name, so the people can be hidden without touching the debug markers.
"""

CUBE_TYPE = 1
"""
``Marker.CUBE``, scaled to its extents along the pose's own axes.
"""

ARROW_TYPE = 0
"""
``Marker.ARROW``, running along its pose's +X — which is the line of sight, so an
arrow needs no reorienting to swap into :data:`PART_SHAPES` in place of a block.
Its scale reads as length, shaft diameter and head diameter instead of extents.
"""

VIEWER_COLORS = (
    "#4ac2ff",
    "#ffb648",
    "#7fe08a",
    "#ff7fb0",
    "#c98bdb",
    "#6bd0c0",
)
"""
The colours viewers are drawn in, one each, so two people in the same room are told
apart at a glance.

They are brighter than the loose objects' cycle in :mod:`cramera.palette`: a viewer
is someone to find in the scene, not part of the furniture.
"""

AVATAR_OPACITY = 0.95


@dataclass(frozen=True)
class PartShape:
    """
    How one tracked part of a viewer is drawn.

    Only the shape — the colour belongs to the viewer, not the part, so all of one
    person's parts read as one person.
    """

    kind: int
    """
    The ``visualization_msgs`` marker type, from :data:`ARROW_TYPE` or
    :data:`CUBE_TYPE`.
    """

    scale: Tuple[float, float, float]
    """
    The marker's scale in metres, with the meaning its type gives it: an arrow's is
    length, shaft diameter and head diameter; a cube's is its extents.
    """


PART_SHAPES: Dict[str, PartShape] = {
    # a headset-sized block: deep enough along the line of sight to read as facing
    # somewhere, wide enough to read as a head
    "head": PartShape(CUBE_TYPE, (0.13, 0.11, 0.20)),
    # a controller-sized block, long axis forward, so which way a hand is turned
    # reads at a glance
    "left": PartShape(CUBE_TYPE, (0.12, 0.05, 0.07)),
    "right": PartShape(CUBE_TYPE, (0.12, 0.05, 0.07)),
}
"""
The parts a viewer may publish, and what each is drawn as.

Every pose arrives with its +X down the part's line of sight, so a cube's extents
read as ``(forward, up, right)`` — the browser turns each device's own -Z forward
into this +X before posting.

A part the headset is not tracking is simply absent from a post; only the parts that
arrive are drawn, so a viewer who puts a controller down loses that hand rather than
leaving it stranded wherever it last was.
"""

SILENCE_TIMEOUT_SECONDS = 5.0
"""
How long a viewer may go without a post before it is swept as gone.

Headsets post continuously while someone wears one, so silence this long means the
tab is closed, asleep or crashed.
"""


def marker_id(viewer: str, part: str) -> int:
    """
    A stable non-negative marker id for one part of one viewer.

    :param viewer: The identifier the browser generated for itself.
    :param part: The part's name, a key of :data:`PART_SHAPES`.
    """
    digest = hashlib.sha1(("%s/%s" % (viewer, part)).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _numbers(value: Any, count: int) -> Optional[List[float]]:
    """
    ``value`` as exactly ``count`` finite floats, or None when it is not that.

    :param value: The candidate, as it arrived in the JSON payload.
    :param count: How many numbers are required.
    """
    if not isinstance(value, (list, tuple)) or len(value) != count:
        return None
    numbers = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        numbers.append(number)
    return numbers


def _channels(hex_color: str) -> Tuple[float, float, float]:
    """
    A ``#rrggbb`` colour as red, green and blue in [0, 1].

    :param hex_color: The colour to read.
    """
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _message(
    identifier: int,
    shape: Optional[PartShape],
    hex_color: str,
    position: List[float],
    quaternion: List[float],
    action: int,
) -> SimpleNamespace:
    """
    One part as the duck-typed marker message the overlay reads.

    :param identifier: The marker's id within :data:`AVATAR_NAMESPACE`.
    :param shape: How the part is drawn, or None for a delete, which reads no
        further than the action, namespace and id.
    :param hex_color: The viewer's colour as ``#rrggbb``.
    :param position: The part's position in the world frame.
    :param quaternion: The part's orientation as ``[x, y, z, w]``.
    :param action: :data:`~cramera.live.markers.ADD_ACTION` or
        :data:`~cramera.live.markers.DELETE_ACTION`.
    """
    drawn = shape or PART_SHAPES["head"]
    red, green, blue = _channels(hex_color)
    return SimpleNamespace(
        ns=AVATAR_NAMESPACE,
        id=identifier,
        type=drawn.kind,
        action=action,
        # the empty frame reads as the world root, so the posted pose is used as-is
        header=SimpleNamespace(frame_id=""),
        pose=SimpleNamespace(
            position=SimpleNamespace(x=position[0], y=position[1], z=position[2]),
            orientation=SimpleNamespace(
                x=quaternion[0], y=quaternion[1], z=quaternion[2], w=quaternion[3]
            ),
        ),
        scale=SimpleNamespace(x=drawn.scale[0], y=drawn.scale[1], z=drawn.scale[2]),
        color=SimpleNamespace(r=red, g=green, b=blue, a=AVATAR_OPACITY),
        points=[],
        text="",
    )


def _delete(identifier: int) -> SimpleNamespace:
    """
    The message that takes one marker back out of the overlay.

    :param identifier: The marker's id.
    """
    return _message(
        identifier, None, VIEWER_COLORS[0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], DELETE_ACTION
    )


@dataclass(frozen=True)
class PartPose:
    """
    One validated part of a post, ready to be drawn.
    """

    identifier: int
    """
    The marker id this part is drawn under.
    """

    shape: PartShape
    """
    How the part is drawn.
    """

    position: List[float]
    """
    The part's position in the world frame.
    """

    quaternion: List[float]
    """
    The part's orientation as ``[x, y, z, w]``.
    """


@dataclass
class AvatarRoster:
    """
    Who is currently in VR, when each of them last said so, and which markers they
    are being drawn with.

    The bridge's marker store is the display; this is only the bookkeeping that
    decides when one of its entries has gone stale, and which ids to clear when it
    has. Parts come and go within a session — a controller put down stops being
    posted — so the ids to clear are the ones that viewer last published, not every
    id its name could produce.
    """

    last_seen: Dict[str, float] = field(default_factory=dict)
    """
    Viewer identifier to the monotonic time of that viewer's last post.
    """

    shown: Dict[str, List[int]] = field(default_factory=dict)
    """
    Viewer identifier to the marker ids currently drawn for it.
    """

    slots: Dict[str, int] = field(default_factory=dict)
    """
    Viewer identifier to its position in :data:`VIEWER_COLORS`.

    A position is held for as long as the viewer is, and freed when they leave, so
    the colours stay distinct rather than drifting apart as people come and go.
    """

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    """
    Guards the maps — posts arrive on the bridge's HTTP threads.
    """

    def touch(self, viewer: str, now: float, identifiers: List[int]) -> Tuple[str, List[int]]:
        """
        Record a viewer's post, and report the markers it has stopped publishing.

        :param viewer: The viewer's identifier.
        :param now: The current monotonic time.
        :param identifiers: The marker ids this post carries.
        :return: The viewer's colour, and the ids it showed before but no longer does.
        """
        with self._lock:
            dropped = [i for i in self.shown.get(viewer, []) if i not in identifiers]
            self.last_seen[viewer] = now
            self.shown[viewer] = list(identifiers)
            if viewer not in self.slots:
                self.slots[viewer] = self._free_slot()
            slot = self.slots[viewer]
        return VIEWER_COLORS[slot % len(VIEWER_COLORS)], dropped

    def color_of(self, viewer: str) -> str:
        """
        The colour a viewer is being drawn in.

        :param viewer: The viewer's identifier.
        """
        with self._lock:
            return VIEWER_COLORS[self.slots.get(viewer, 0) % len(VIEWER_COLORS)]

    def _free_slot(self) -> int:
        """
        The lowest colour position no current viewer holds.

        Only ever called while :attr:`_lock` is held.
        """
        taken = set(self.slots.values())
        slot = 0
        while slot in taken:
            slot += 1
        return slot

    def drop(self, viewer: str) -> List[int]:
        """
        Forget a viewer that has said it is leaving.

        :param viewer: The viewer's identifier.
        :return: The marker ids that viewer was being drawn with.
        """
        with self._lock:
            self.last_seen.pop(viewer, None)
            self.slots.pop(viewer, None)
            return self.shown.pop(viewer, [])

    def expired(self, now: float) -> List[int]:
        """
        The markers of every viewer that has gone quiet for longer than
        :data:`SILENCE_TIMEOUT_SECONDS`, forgetting them as they are reported.

        :param now: The current monotonic time.
        """
        with self._lock:
            stale = [
                viewer
                for viewer, seen in self.last_seen.items()
                if now - seen > SILENCE_TIMEOUT_SECONDS
            ]
            identifiers: List[int] = []
            for viewer in stale:
                self.last_seen.pop(viewer, None)
                self.slots.pop(viewer, None)
                identifiers.extend(self.shown.pop(viewer, []))
        return identifiers

    def count(self) -> int:
        """
        How many viewers are currently being tracked.
        """
        with self._lock:
            return len(self.last_seen)


ROSTER = AvatarRoster()
"""
The one roster, alongside the one bridge the live process serves.
"""


def observe_avatar(bridge: Bridge, payload: Any) -> Tuple[Dict[str, Any], int]:
    """
    Apply one headset's report to the marker overlay.

    The payload is ``{viewer, parts: [{name, position, quaternion}, ...]}`` with the
    poses in the *world* frame — the z-up frame every other published pose uses — or
    ``{viewer, gone: true}`` when the headset is leaving. Poses are validated here,
    on the HTTP thread, so malformed input is refused rather than reaching the marker
    store, and a post naming no part the overlay draws is refused rather than
    silently drawing nothing.

    :param bridge: The bridge whose marker overlay the viewer joins.
    :param payload: The decoded JSON body.
    :return: The response body and its HTTP status code.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "expected a JSON object"}, 400
    viewer = str(payload.get("viewer") or "").strip()
    if not viewer:
        return {"ok": False, "error": "no viewer id"}, 400
    now = time.monotonic()
    messages: List[SimpleNamespace] = []
    color = ROSTER.color_of(viewer)

    if payload.get("gone"):
        messages.extend(_delete(identifier) for identifier in ROSTER.drop(viewer))
    else:
        parts = payload.get("parts")
        if not isinstance(parts, list) or not parts:
            return {"ok": False, "error": "parts must be a non-empty list"}, 400
        poses: List[PartPose] = []
        for part in parts:
            if not isinstance(part, dict):
                return {"ok": False, "error": "each part is an object"}, 400
            name = str(part.get("name") or "")
            shape = PART_SHAPES.get(name)
            if shape is None:
                return {
                    "ok": False,
                    "error": "unknown part %r, expected one of %s"
                    % (name, ", ".join(sorted(PART_SHAPES))),
                }, 400
            position = _numbers(part.get("position"), 3)
            quaternion = _numbers(part.get("quaternion"), 4)
            if position is None or quaternion is None:
                return {
                    "ok": False,
                    "error": "%s needs a position of 3 numbers and a quaternion of 4"
                    % name,
                }, 400
            poses.append(PartPose(marker_id(viewer, name), shape, position, quaternion))
        # the roster is only told once every part has been read, so a post that turns
        # out to be malformed leaves the viewer as it was rather than half-moved
        color, dropped = ROSTER.touch(viewer, now, [pose.identifier for pose in poses])
        messages.extend(
            _message(pose.identifier, pose.shape, color, pose.position, pose.quaternion, ADD_ACTION)
            for pose in poses
        )
        # a part this viewer has stopped tracking stops being drawn
        messages.extend(_delete(identifier) for identifier in dropped)

    messages.extend(_delete(identifier) for identifier in ROSTER.expired(now))
    bridge.observe_ros_markers(AVATAR_TOPIC, messages)
    return {"ok": True, "viewers": ROSTER.count(), "color": color}, 200


__all__ = [
    "AVATAR_TOPIC",
    "AVATAR_NAMESPACE",
    "PART_SHAPES",
    "VIEWER_COLORS",
    "PartShape",
    "PartPose",
    "SILENCE_TIMEOUT_SECONDS",
    "AvatarRoster",
    "ROSTER",
    "marker_id",
    "observe_avatar",
]
