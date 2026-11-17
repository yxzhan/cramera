"""
The documents a live publisher serves and accepts: world state, plan, statechart,
transforms, markers, objects, robots, recording and the requests that change the world.

Keys keep the names earlier browsers read, so a page written against them reads these
documents unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import Any, Dict, List, Optional

from cramera_protocol.bundle.scene import DetectedEvent, PerceptionImage, PlanNodeKind
from cramera_protocol.bundle.statecharts import ChartEdge
from cramera_protocol.document import JsonDocument, document_field
from cramera_protocol.exceptions import InvalidDocumentValue
from cramera_protocol.geometry import NumberArray, Pose, Vector3

# %% values


@dataclass(frozen=True)
class Quaternion(NumberArray):
    """
    An orientation, written as ``[qx, qy, qz, qw]``.
    """

    qx: float
    """
    The x component.
    """

    qy: float
    """
    The y component.
    """

    qz: float
    """
    The z component.
    """

    qw: float
    """
    The scalar component.
    """

    @classmethod
    def length(cls) -> int:
        return 4


@dataclass(frozen=True)
class FiniteNumbers:
    """
    Checks that the numbers of a request are finite, so a request never writes ``NaN``
    or an infinity into a world.
    """

    document: str
    """
    The name of the document being checked, for the error.
    """

    def require(self, location: str, numbers: List[float]) -> None:
        """
        :param location: The key holding the numbers.
        :param numbers: The numbers to check.
        :raises InvalidDocumentValue: When one of them is not finite.
        """
        if not all(math.isfinite(number) for number in numbers):
            raise InvalidDocumentValue(
                self.document, location, numbers, "finite numbers"
            )


# %% query parameters


class LiveParameter(StrEnum):
    """
    The query parameters of the live routes.
    """

    KEY = "key"
    """
    The served mesh, for :attr:`~cramera_protocol.live.LiveRoute.MESH`, or the served
    image, for :attr:`~cramera_protocol.live.LiveRoute.PERCEPTION_IMAGE`.
    """

    SIDE = "side"
    """
    A file beside the served mesh, such as its material library.
    """

    REVISION = "revision"
    """
    The revision of the query steps the viewer shows, for
    :attr:`~cramera_protocol.live.LiveRoute.QUERY_STEPS`.
    """


# %% state


class LiveStateKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.STATE`.
    """

    SEQUENCE_NUMBER = "sequenceNumber"
    """
    The number of the snapshot, growing with every new one.
    """

    FRAMES = "frames"
    """
    The value of every movable joint.
    """

    BASE = "base"
    """
    The root pose of the selected robot.
    """

    OBJECTS = "objects"
    """
    The pose of every free object.
    """

    MODEL_BASES = "modelBases"
    """
    The root pose of every robot instance.
    """

    MARKERS_VERSION = "markersVersion"
    """
    The version of the marker overlay.
    """

    MARKER_POSES = "markerPoses"
    """
    The poses of the markers that follow a body.
    """


@dataclass(kw_only=True)
class LiveState(JsonDocument):
    """
    The world's joint values and poses at one moment, in the trajectory frame format.
    """

    sequence_number: int = document_field(LiveStateKey.SEQUENCE_NUMBER, default=0)
    """
    The number of the snapshot, so a reader skips one it already shows.
    """

    frames: Dict[str, float] = document_field(LiveStateKey.FRAMES, default_factory=dict)
    """
    The value of every movable joint by its prefixed name.
    """

    base: Optional[Pose] = document_field(LiveStateKey.BASE, default=None)
    """
    The root pose of the selected robot, or None without a robot.
    """

    objects: Dict[str, Pose] = document_field(
        LiveStateKey.OBJECTS, default_factory=dict
    )
    """
    The pose of every free object by its key.
    """

    model_bases: Dict[str, Pose] = document_field(
        LiveStateKey.MODEL_BASES, default_factory=dict
    )
    """
    The root pose of every robot instance by its identifier.
    """

    markers_version: int = document_field(LiveStateKey.MARKERS_VERSION, default=0)
    """
    The version of the marker overlay; a reader fetches the markers when it changes.
    """

    marker_poses: Dict[str, Pose] = document_field(
        LiveStateKey.MARKER_POSES, default_factory=dict
    )
    """
    The pose in the world of every marker of that overlay given in the frame of a body,
    by the marker's position among the overlay's markers; such a marker moves with its
    body while the overlay stays the same.
    """


# %% plan


class ConstraintObservationKey(StrEnum):
    """
    The keys of one constraint monitor attached to a plan node.
    """

    TEXT = "text"
    """
    The constraint as it was written.
    """

    MONITOR = "monitor"
    """
    The monitor class the constraint became.
    """

    OBSERVATION = "observation"
    """
    The monitor's current observation.
    """


@dataclass(kw_only=True)
class ConstraintObservation(JsonDocument):
    """
    A constraint monitor attached to a plan node and what it observes.
    """

    text: str = document_field(ConstraintObservationKey.TEXT)
    """
    The constraint as it was written.
    """

    monitor: str = document_field(ConstraintObservationKey.MONITOR)
    """
    The class name of the monitor the constraint became.
    """

    observation: Optional[str] = document_field(
        ConstraintObservationKey.OBSERVATION, default=None
    )
    """
    The name of the monitor's native observation value, or None before its first tick.
    """


class LivePlanNodeKey(StrEnum):
    """
    The keys of one node of the executing plan.
    """

    IDENTIFIER = "id"
    """
    The node's identity within the plan.
    """

    PARENT = "parent"
    """
    The identity of the node's parent.
    """

    KIND = "kind"
    """
    The class name of the plan node.
    """

    LABEL = "label"
    """
    The node's description.
    """

    STATUS = "status"
    """
    The node's native status.
    """

    ARM = "arm"
    """
    The arm the node's designator names.
    """

    TARGET = "target"
    """
    The object the node's designator names.
    """

    CONSTRAINTS = "constraints"
    """
    The constraint monitors attached to the node.
    """

    PARAMETERS = "parameters"
    """
    Every field of the node's designator by name.
    """

    PERCEIVED = "perceived"
    """
    The objects a perception step reported.
    """

    REASON = "reason"
    """
    Why a failed node failed.
    """

    IMAGES = "images"
    """
    What a perception step saw.
    """


@dataclass(kw_only=True)
class LivePlanNode(JsonDocument):
    """
    One node of the executing plan with its native status.
    """

    identifier: str = document_field(LivePlanNodeKey.IDENTIFIER)
    """
    The node's identity within the plan.
    """

    kind: PlanNodeKind = document_field(LivePlanNodeKey.KIND)
    """
    What kind of node it is.
    """

    label: str = document_field(LivePlanNodeKey.LABEL)
    """
    The class name of the node's designator, or the node's own class name.
    """

    status: str = document_field(LivePlanNodeKey.STATUS)
    """
    The name of the node's native task status.
    """

    parent: Optional[str] = document_field(LivePlanNodeKey.PARENT, default=None)
    """
    The identity of the node's parent, or None for the root.
    """

    arm: Optional[str] = document_field(LivePlanNodeKey.ARM, default=None)
    """
    The arm the node's designator names.
    """

    target: Optional[str] = document_field(LivePlanNodeKey.TARGET, default=None)
    """
    The key of the object the node's designator names.
    """

    constraints: List[ConstraintObservation] = document_field(
        LivePlanNodeKey.CONSTRAINTS, default_factory=list
    )
    """
    The constraint monitors attached to the node.
    """

    parameters: Dict[str, str] = document_field(
        LivePlanNodeKey.PARAMETERS, default_factory=dict
    )
    """
    Every field of the node's designator by name, rendered for reading: an enumeration
    member by its name, an object by its key, a pose by its position; empty for a node
    without a designator.
    """

    perceived: Optional[List[str]] = document_field(
        LivePlanNodeKey.PERCEIVED, default=None
    )
    """
    For a perception step: the objects it reported once it ended, by their keys, else
    their names, empty while it runs or when it failed; None for every other node.
    """

    reason: Optional[str] = document_field(LivePlanNodeKey.REASON, default=None)
    """
    Why the node failed, as coraplex words it; None for a node that did not fail.
    """

    images: Optional[List[PerceptionImage]] = document_field(
        LivePlanNodeKey.IMAGES, default=None
    )
    """
    For a perception step: the images of what it saw once it ended, each served by
    :attr:`~cramera_protocol.live.LiveRoute.PERCEPTION_IMAGE`; None for every other
    node.
    """


class LivePlanKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.PLAN`.
    """

    SIGNATURE = "signature"
    """
    The identity of the plan's shape.
    """

    NODES = "nodes"
    """
    The plan's nodes.
    """


@dataclass(kw_only=True)
class LivePlan(JsonDocument):
    """
    The executing plan, flattened with parent references.
    """

    signature: str = document_field(LivePlanKey.SIGNATURE, default="")
    """
    The identity of the plan's shape, unchanged while only statuses change.
    """

    nodes: List[LivePlanNode] = document_field(LivePlanKey.NODES, default_factory=list)
    """
    Every node of the plan, parents before their children.
    """


# %% statechart


class LiveChartNodeKey(StrEnum):
    """
    The keys of one node of the motion statechart being ticked.
    """

    IDENTIFIER = "id"
    """
    The node's identity within the chart.
    """

    NAME = "name"
    """
    The node's name.
    """

    CLASS_NAME = "class_name"
    """
    The class of the node.
    """

    PARENT = "parent"
    """
    The identity of the node's parent.
    """

    LIFE_CYCLE = "life_cycle"
    """
    The node's native life-cycle value.
    """

    OBSERVATION = "observation"
    """
    The node's native observation value.
    """


@dataclass(kw_only=True)
class LiveChartNode(JsonDocument):
    """
    One node of the motion statechart with its native state.
    """

    identifier: str = document_field(LiveChartNodeKey.IDENTIFIER)
    """
    The node's identity within the chart.
    """

    name: str = document_field(LiveChartNodeKey.NAME)
    """
    The node's name.
    """

    class_name: str = document_field(LiveChartNodeKey.CLASS_NAME)
    """
    The class of the node in CRAM.
    """

    life_cycle: str = document_field(LiveChartNodeKey.LIFE_CYCLE)
    """
    The name of the node's native life-cycle value.
    """

    observation: str = document_field(LiveChartNodeKey.OBSERVATION)
    """
    The name of the node's native observation value.
    """

    parent: Optional[str] = document_field(LiveChartNodeKey.PARENT, default=None)
    """
    The identity of the node's parent, or None for a top-level node.
    """


class LiveStatechartKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.STATECHART`.
    """

    SIGNATURE = "signature"
    """
    The identity of the chart's structure.
    """

    TITLE = "title"
    """
    What the chart executes.
    """

    NODES = "nodes"
    """
    The chart's nodes.
    """

    EDGES = "edges"
    """
    The chart's transitions.
    """


@dataclass(kw_only=True)
class LiveStatechart(JsonDocument):
    """
    The motion statechart being ticked, in its last recorded state.
    """

    signature: str = document_field(LiveStatechartKey.SIGNATURE, default="")
    """
    The identity of the chart's structure, unchanged while only states change.
    """

    title: str = document_field(LiveStatechartKey.TITLE, default="")
    """
    The name of the action whose motions the chart executes.
    """

    nodes: List[LiveChartNode] = document_field(
        LiveStatechartKey.NODES, default_factory=list
    )
    """
    The chart's nodes.
    """

    edges: List[ChartEdge] = document_field(
        LiveStatechartKey.EDGES, default_factory=list
    )
    """
    The chart's transitions.
    """


# %% transforms


class ConnectionKind(StrEnum):
    """
    How a connection holds its child frame.
    """

    FIXED = "fixed"
    """
    The child never moves relative to the parent.
    """

    ACTUATED = "actuated"
    """
    A joint with one degree of freedom.
    """

    FREE = "free"
    """
    A connection with six degrees of freedom.
    """

    OTHER = "other"
    """
    Any other connection.
    """


class TransformWriter(StrEnum):
    """
    Who last changed a connection.
    """

    DEMO = "demo"
    """
    The running program.
    """

    VIEWER = "viewer"
    """
    A person, through the viewer.
    """

    NOBODY = "nobody"
    """
    Nothing changed the connection yet.
    """


class TransformFreshness(StrEnum):
    """
    How recently a connection changed.
    """

    MOVING = "MOVING"
    """
    It changed a moment ago.
    """

    SETTLED = "SETTLED"
    """
    It changed a few seconds ago.
    """

    STALE = "STALE"
    """
    It has not changed for a while, or never.
    """

    STATIC = "STATIC"
    """
    It cannot change.
    """


class ConnectionStateKey(StrEnum):
    """
    The keys of one connection of the transform graph.
    """

    NAME = "name"
    """
    The connection's name.
    """

    PARENT = "parent"
    """
    The frame the connection hangs from.
    """

    CHILD = "child"
    """
    The frame the connection carries.
    """

    KIND = "kind"
    """
    How the connection holds its child.
    """

    WRITER = "writer"
    """
    Who last changed the connection.
    """

    FRESHNESS = "freshness"
    """
    How recently it changed.
    """

    AGE_SECONDS = "ageSeconds"
    """
    Seconds since it changed.
    """


@dataclass(kw_only=True)
class ConnectionState(JsonDocument):
    """
    One connection of the world and when it last changed.
    """

    name: str = document_field(ConnectionStateKey.NAME)
    """
    The connection's prefixed name.
    """

    parent: str = document_field(ConnectionStateKey.PARENT)
    """
    The name of the frame the connection hangs from.
    """

    child: str = document_field(ConnectionStateKey.CHILD)
    """
    The name of the frame the connection carries.
    """

    kind: ConnectionKind = document_field(ConnectionStateKey.KIND)
    """
    How the connection holds its child.
    """

    writer: TransformWriter = document_field(ConnectionStateKey.WRITER)
    """
    Who last changed the connection.
    """

    freshness: TransformFreshness = document_field(ConnectionStateKey.FRESHNESS)
    """
    How recently it changed.
    """

    age_seconds: Optional[float] = document_field(
        ConnectionStateKey.AGE_SECONDS, default=None
    )
    """
    Seconds since it changed, or None while it never did.
    """


class TransformGraphKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.TRANSFORMS`.
    """

    SIGNATURE = "signature"
    """
    The identity of the graph's structure.
    """

    CONNECTIONS = "connections"
    """
    The world's connections.
    """


@dataclass(kw_only=True)
class TransformGraph(JsonDocument):
    """
    Every connection of the world with how recently it changed.
    """

    signature: str = document_field(TransformGraphKey.SIGNATURE, default="")
    """
    The identity of the graph's structure, unchanged while only poses change.
    """

    connections: List[ConnectionState] = document_field(
        TransformGraphKey.CONNECTIONS, default_factory=list
    )
    """
    Every connection, sorted by name.
    """


# %% markers


class MarkerKind(StrEnum):
    """
    The marker primitives the viewer draws.
    """

    ARROW = "arrow"
    """
    An arrow.
    """

    CUBE = "cube"
    """
    A box.
    """

    SPHERE = "sphere"
    """
    A sphere.
    """

    CYLINDER = "cylinder"
    """
    A cylinder.
    """

    LINE_STRIP = "line_strip"
    """
    Connected line segments through the points.
    """

    LINE_LIST = "line_list"
    """
    One line segment per pair of points.
    """

    CUBE_LIST = "cube_list"
    """
    One box per point.
    """

    SPHERE_LIST = "sphere_list"
    """
    One sphere per point.
    """

    POINTS = "points"
    """
    One dot per point.
    """

    TEXT = "text"
    """
    A text label.
    """


class MarkerKey(StrEnum):
    """
    The keys of one debug marker.
    """

    TOPIC = "topic"
    """
    The topic the marker arrived on.
    """

    NAMESPACE = "ns"
    """
    The marker's namespace.
    """

    IDENTIFIER = "id"
    """
    The marker's identity within its namespace.
    """

    KIND = "kind"
    """
    The primitive the marker draws.
    """

    POSE = "pose"
    """
    The marker's pose in the world.
    """

    SCALE = "scale"
    """
    The marker's scale.
    """

    COLOR = "color"
    """
    The marker's colour.
    """

    OPACITY = "opacity"
    """
    The marker's opacity.
    """

    POINTS = "points"
    """
    The marker's points.
    """

    TEXT = "text"
    """
    The marker's text.
    """

    COLORS = "colors"
    """
    The colour of each of the marker's points.
    """


@dataclass(kw_only=True)
class MarkerDescription(JsonDocument):
    """
    One debug marker, posed in the world.
    """

    topic: str = document_field(MarkerKey.TOPIC)
    """
    The topic the marker arrived on.
    """

    namespace: str = document_field(MarkerKey.NAMESPACE)
    """
    The marker's namespace.
    """

    identifier: int = document_field(MarkerKey.IDENTIFIER)
    """
    The marker's identity within its namespace.
    """

    kind: MarkerKind = document_field(MarkerKey.KIND)
    """
    The primitive the marker draws.
    """

    pose: Pose = document_field(MarkerKey.POSE)
    """
    The marker's pose in world coordinates.
    """

    scale: Vector3 = document_field(MarkerKey.SCALE)
    """
    The marker's scale, with the meaning its kind gives it.
    """

    color: str = document_field(MarkerKey.COLOR)
    """
    The marker's colour as a CSS hexadecimal colour.
    """

    opacity: float = document_field(MarkerKey.OPACITY)
    """
    The marker's opacity between 0 and 1.
    """

    points: List[Vector3] = document_field(MarkerKey.POINTS, default_factory=list)
    """
    The marker's points, relative to its pose.
    """

    text: str = document_field(MarkerKey.TEXT, default="")
    """
    The text of a text marker.
    """

    colors: List[str] = document_field(MarkerKey.COLORS, default_factory=list)
    """
    The colour of each point as a CSS hexadecimal colour, when the marker gives its
    points colours of their own; :attr:`color` colours them otherwise.
    """


class MarkerOverlayKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.MARKERS`.
    """

    VERSION = "version"
    """
    The version of the overlay.
    """

    MARKERS = "markers"
    """
    The markers.
    """


@dataclass(kw_only=True)
class MarkerOverlay(JsonDocument):
    """
    Every debug marker the process currently shows.
    """

    version: int = document_field(MarkerOverlayKey.VERSION, default=0)
    """
    Grows whenever the markers change.
    """

    markers: List[MarkerDescription] = document_field(
        MarkerOverlayKey.MARKERS, default_factory=list
    )
    """
    The markers.
    """


class MarkerTopicsKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.MARKER_TOPICS`.
    """

    ROS = "ros"
    """
    Whether the process reads markers from ROS.
    """

    SUBSCRIBED = "subscribed"
    """
    The topics being read.
    """

    AVAILABLE = "available"
    """
    The marker topics the ROS graph offers.
    """


@dataclass(kw_only=True)
class MarkerTopics(JsonDocument):
    """
    The marker topics read and on offer.
    """

    ros: bool = document_field(MarkerTopicsKey.ROS)
    """
    Whether the process reads markers from ROS at all.
    """

    subscribed: List[str] = document_field(
        MarkerTopicsKey.SUBSCRIBED, default_factory=list
    )
    """
    The topics being read, sorted.
    """

    available: List[str] = document_field(
        MarkerTopicsKey.AVAILABLE, default_factory=list
    )
    """
    Every marker topic the ROS graph offers and every topic being read, sorted.
    """


class MarkerTopicRequestKey(StrEnum):
    """
    The keys of a request to read or stop reading a marker topic.
    """

    TOPIC = "topic"
    """
    The topic.
    """

    SUBSCRIBED = "subscribed"
    """
    Whether it is to be read.
    """


@dataclass(kw_only=True)
class MarkerTopicRequest(JsonDocument):
    """
    Starts or stops reading one marker topic.
    """

    topic: str = document_field(MarkerTopicRequestKey.TOPIC)
    """
    The topic, starting with ``/``.
    """

    subscribed: bool = document_field(MarkerTopicRequestKey.SUBSCRIBED, default=True)
    """
    Whether the topic is to be read.
    """

    def __post_init__(self) -> None:
        if not self.topic.startswith("/"):
            raise InvalidDocumentValue(
                type(self).__name__,
                MarkerTopicRequestKey.TOPIC,
                self.topic,
                "a topic starting with '/'",
            )


# %% objects


class ShapeKind(StrEnum):
    """
    The geometry of one shape of a live object.
    """

    BOX = "box"
    """
    A box.
    """

    CYLINDER = "cylinder"
    """
    A cylinder.
    """

    SPHERE = "sphere"
    """
    A sphere.
    """

    MESH = "mesh"
    """
    A mesh file.
    """


class ShapeKey(StrEnum):
    """
    The keys of one shape of a live object.
    """

    KIND = "kind"
    """
    The shape's geometry.
    """

    POSITION = "position"
    """
    The shape's position within its body.
    """

    QUATERNION = "quaternion"
    """
    The shape's orientation within its body.
    """

    COLOR = "color"
    """
    The shape's colour.
    """

    OPACITY = "opacity"
    """
    The shape's opacity.
    """

    SIZE = "size"
    """
    The extent of a box.
    """

    RADIUS = "radius"
    """
    The radius of a cylinder or sphere.
    """

    HEIGHT = "height"
    """
    The height of a cylinder.
    """

    MESH = "mesh"
    """
    The URL of a mesh file.
    """

    MATERIAL_LIBRARY = "mtl"
    """
    The URL of an OBJ mesh's material library.
    """

    FORMAT = "format"
    """
    The mesh file's format.
    """

    SCALE = "scale"
    """
    The mesh's scale.
    """


@dataclass(kw_only=True)
class ShapeDescription(JsonDocument):
    """
    One shape of a live object, as its native shape describes it.
    """

    kind: ShapeKind = document_field(ShapeKey.KIND)
    """
    The shape's geometry.
    """

    position: Vector3 = document_field(ShapeKey.POSITION)
    """
    The shape's position within its body, in metres.
    """

    quaternion: Quaternion = document_field(ShapeKey.QUATERNION)
    """
    The shape's orientation within its body.
    """

    color: str = document_field(ShapeKey.COLOR)
    """
    The native shape colour as a CSS hexadecimal colour.
    """

    opacity: float = document_field(ShapeKey.OPACITY)
    """
    The native shape opacity between 0 and 1.
    """

    size: Optional[Vector3] = document_field(ShapeKey.SIZE, default=None)
    """
    The extent of a box in metres.
    """

    radius: Optional[float] = document_field(ShapeKey.RADIUS, default=None)
    """
    The radius of a cylinder or sphere in metres.
    """

    height: Optional[float] = document_field(ShapeKey.HEIGHT, default=None)
    """
    The height of a cylinder in metres.
    """

    mesh: Optional[str] = document_field(ShapeKey.MESH, default=None)
    """
    The route serving a mesh file, relative to the publisher.
    """

    material_library: Optional[str] = document_field(
        ShapeKey.MATERIAL_LIBRARY, default=None
    )
    """
    The route serving an OBJ mesh's material library.
    """

    file_format: Optional[str] = document_field(ShapeKey.FORMAT, default=None)
    """
    The mesh file's suffix without the dot, such as ``obj``.
    """

    scale: Optional[Vector3] = document_field(ShapeKey.SCALE, default=None)
    """
    The mesh's scale per axis.
    """


class LiveObjectKey(StrEnum):
    """
    The keys of one free object of the live world.
    """

    KEY = "key"
    """
    The object's key in the state's object poses.
    """

    IDENTIFIER = "id"
    """
    The object's display name.
    """

    SHAPES = "shapes"
    """
    The object's shapes.
    """


@dataclass(kw_only=True)
class LiveObject(JsonDocument):
    """
    One free object of the live world with its shapes.
    """

    key: str = document_field(LiveObjectKey.KEY)
    """
    The object's key in :attr:`LiveState.objects`: the identity of its body.
    """

    identifier: str = document_field(LiveObjectKey.IDENTIFIER)
    """
    The object's display name: its body's name.
    """

    shapes: List[ShapeDescription] = document_field(
        LiveObjectKey.SHAPES, default_factory=list
    )
    """
    The object's shapes; empty for a body without any.
    """


class ObjectCatalogKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.OBJECTS`.
    """

    OBJECTS = "objects"
    """
    The free objects.
    """


@dataclass(kw_only=True)
class ObjectCatalog(JsonDocument):
    """
    The free objects of the live world.
    """

    objects: List[LiveObject] = document_field(
        ObjectCatalogKey.OBJECTS, default_factory=list
    )
    """
    The free objects.
    """


class ObjectPosesKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.CAPTURED_OBJECTS`.
    """

    OBJECTS = "objects"
    """
    The object poses.
    """


@dataclass(kw_only=True)
class ObjectPoses(JsonDocument):
    """
    The pose of every free object, including moves a person made that the world has not
    applied yet.
    """

    objects: Dict[str, Pose] = document_field(
        ObjectPosesKey.OBJECTS, default_factory=dict
    )
    """
    The pose of every free object by its key.
    """


class SurfaceKey(StrEnum):
    """
    The keys of one surface objects can be placed on or in.
    """

    TYPE = "type"
    """
    The class of the surface's annotation.
    """

    NAME = "name"
    """
    The name of the surface's annotation.
    """


@dataclass(kw_only=True)
class SurfaceDescription(JsonDocument):
    """
    One annotated surface objects can be placed on or in.
    """

    type_name: str = document_field(SurfaceKey.TYPE)
    """
    The identifier of the surface's semantic annotation class, as the plan builder's
    catalog lists surface types and annotation field types, such as ``table``.
    """

    name: str = document_field(SurfaceKey.NAME)
    """
    The name of the surface's semantic annotation.
    """


class SurfacesKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.SURFACES`.
    """

    SURFACES = "surfaces"
    """
    The surfaces.
    """


@dataclass(kw_only=True)
class Surfaces(JsonDocument):
    """
    The annotated surfaces of the live world.
    """

    surfaces: List[SurfaceDescription] = document_field(
        SurfacesKey.SURFACES, default_factory=list
    )
    """
    The surfaces.
    """


class LiveSceneKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.LIVE_SCENE`.
    """

    SCENE = "scene"
    """
    The bundle holding the world's current model.
    """


@dataclass(kw_only=True)
class LiveScene(JsonDocument):
    """
    Names the bundle that shows the live world's current model.
    """

    scene: Optional[str] = document_field(LiveSceneKey.SCENE, default=None)
    """
    The bundle's name, or None while no world is attached.
    """


# %% detected events


class DetectedEventsKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.DETECTED_EVENTS`.
    """

    EVENTS = "events"
    """
    The events seen so far.
    """


@dataclass(kw_only=True)
class DetectedEvents(JsonDocument):
    """
    The events segmind's detectors saw so far in the live run, oldest first.
    """

    events: List[DetectedEvent] = document_field(
        DetectedEventsKey.EVENTS, default_factory=list
    )
    """
    The events, each placed on the frame of the running recording it happened at.
    """


# %% robots


class RobotInstanceKey(StrEnum):
    """
    The keys of one robot of the live world.
    """

    IDENTIFIER = "identifier"
    """
    The robot's identity.
    """

    LABEL = "label"
    """
    The robot's display name.
    """

    MODEL = "model"
    """
    The robot's class.
    """

    POSE = "pose"
    """
    Where the robot's description is rooted.
    """

    JOINT_POSITIONS = "jointPositions"
    """
    The robot's joint values.
    """


@dataclass(kw_only=True)
class RobotInstance(JsonDocument):
    """
    One robot of the live world.
    """

    identifier: str = document_field(RobotInstanceKey.IDENTIFIER)
    """
    The robot's identity: the identity of its semantic annotation.
    """

    label: str = document_field(RobotInstanceKey.LABEL)
    """
    The robot's name.
    """

    model: str = document_field(RobotInstanceKey.MODEL)
    """
    The class name of the robot's annotation.
    """

    pose: Pose = document_field(RobotInstanceKey.POSE)
    """
    Where the robot's description is rooted in the world, which a viewer draws it at.
    """

    joint_positions: Dict[str, float] = document_field(
        RobotInstanceKey.JOINT_POSITIONS, default_factory=dict
    )
    """
    The value of each of the robot's movable joints by its prefixed name.
    """


class RobotRosterKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.ROBOTS`.
    """

    ROBOTS = "robots"
    """
    The robots.
    """

    ACTIVE_IDENTIFIER = "activeIdentifier"
    """
    The robot the live controls act on.
    """


@dataclass(kw_only=True)
class RobotRoster(JsonDocument):
    """
    The robots of the live world and which one is selected.
    """

    robots: List[RobotInstance] = document_field(
        RobotRosterKey.ROBOTS, default_factory=list
    )
    """
    The robots.
    """

    active_identifier: Optional[str] = document_field(
        RobotRosterKey.ACTIVE_IDENTIFIER, default=None
    )
    """
    The identity of the robot the live controls act on, or None without robots.
    """


class RobotSelectionKey(StrEnum):
    """
    The keys of a request selecting a robot.
    """

    IDENTIFIER = "identifier"
    """
    The robot to select.
    """


@dataclass(kw_only=True)
class RobotSelection(JsonDocument):
    """
    Selects the robot the live controls act on.
    """

    identifier: str = document_field(RobotSelectionKey.IDENTIFIER)
    """
    The identity of the robot to select.
    """


# %% recording


class RecordingState(StrEnum):
    """
    Where a recording is in its life.
    """

    IDLE = "idle"
    """
    Nothing is being recorded or waiting to be saved.
    """

    RECORDING = "recording"
    """
    The run is being recorded.
    """

    FINALIZED = "finalized"
    """
    The recording ended and waits to be saved or discarded.
    """


class RecordingStatusKey(StrEnum):
    """
    The keys of the answer to :attr:`~cramera_protocol.live.LiveRoute.RECORDING`.
    """

    STATE = "state"
    """
    Where the recording is in its life.
    """

    FRAME_COUNT = "frameCount"
    """
    How many frames it holds.
    """

    DURATION_SECONDS = "durationSeconds"
    """
    How long it is.
    """

    FRAMES_PER_SECOND = "framesPerSecond"
    """
    Its measured frame rate.
    """

    SCENE_NAME = "sceneName"
    """
    The bundle it was written to.
    """


@dataclass(kw_only=True)
class RecordingStatus(JsonDocument):
    """
    The state of the current recording.
    """

    state: RecordingState = document_field(RecordingStatusKey.STATE)
    """
    Where the recording is in its life.
    """

    frame_count: int = document_field(RecordingStatusKey.FRAME_COUNT, default=0)
    """
    How many frames it holds.
    """

    duration_seconds: float = document_field(
        RecordingStatusKey.DURATION_SECONDS, default=0.0
    )
    """
    The time between its first and last frame.
    """

    frames_per_second: Optional[float] = document_field(
        RecordingStatusKey.FRAMES_PER_SECOND, default=None
    )
    """
    Its measured frame rate, or None with fewer than two frames.
    """

    scene_name: Optional[str] = document_field(
        RecordingStatusKey.SCENE_NAME, default=None
    )
    """
    The bundle it was written to, once it was.
    """


class SceneDestination(StrEnum):
    """
    Where a saved recording goes.
    """

    LOCAL = "local"
    """
    The local scenes directory.
    """

    SHARED = "shared"
    """
    The shared scenes directory.
    """


class RecordingSaveRequestKey(StrEnum):
    """
    The keys of a request saving the finalized recording.
    """

    NAME = "name"
    """
    The name to save it under.
    """

    DESTINATION = "destination"
    """
    Where to save it.
    """

    ROBOT = "robot"
    """
    The robot's display name.
    """

    ENVIRONMENT = "environment"
    """
    The environment's display name.
    """

    TASK = "task"
    """
    What the run did.
    """

    FIRST_FRAME = "firstFrame"
    """
    The first frame to keep.
    """

    LAST_FRAME = "lastFrame"
    """
    The last frame to keep.
    """

    ADDS_A_RUN = "addsARun"
    """
    Whether the recording is a further run of a bundle that already holds the name.
    """


@dataclass(kw_only=True)
class RecordingSaveRequest(JsonDocument):
    """
    Saves the finalized recording as a scene bundle, optionally trimmed.
    """

    name: str = document_field(RecordingSaveRequestKey.NAME)
    """
    The bundle name to save it under.
    """

    destination: SceneDestination = document_field(
        RecordingSaveRequestKey.DESTINATION, default=SceneDestination.LOCAL
    )
    """
    The scenes directory to save it into.
    """

    robot: Optional[str] = document_field(RecordingSaveRequestKey.ROBOT, default=None)
    """
    The robot's display name, or None to keep the recorded one.
    """

    environment: Optional[str] = document_field(
        RecordingSaveRequestKey.ENVIRONMENT, default=None
    )
    """
    The environment's display name, or None to keep the recorded one.
    """

    task: Optional[str] = document_field(RecordingSaveRequestKey.TASK, default=None)
    """
    What the run did.
    """

    first_frame: Optional[int] = document_field(
        RecordingSaveRequestKey.FIRST_FRAME, default=None
    )
    """
    The first frame kept, or None to keep the recording from its start.
    """

    last_frame: Optional[int] = document_field(
        RecordingSaveRequestKey.LAST_FRAME, default=None
    )
    """
    The last frame kept, or None to keep the recording to its end.
    """

    adds_a_run: bool = document_field(RecordingSaveRequestKey.ADDS_A_RUN, default=False)
    """
    Whether a bundle of the same name in the destination is replaced by this run, which
    joins the runs its plan database holds, instead of refusing the name.
    """


class SavedSceneKey(StrEnum):
    """
    The keys of the answer to a request that wrote a bundle.
    """

    SCENE = "scene"
    """
    The bundle's name.
    """


@dataclass(kw_only=True)
class SavedScene(JsonDocument):
    """
    Names the bundle a request wrote.
    """

    scene: str = document_field(SavedSceneKey.SCENE)
    """
    The bundle's name.
    """


# %% changing the world


class AcknowledgementKey(StrEnum):
    """
    The keys of the answer to a request that was accepted.
    """

    OK = "ok"
    """
    Whether the request was accepted.
    """


@dataclass(kw_only=True)
class Acknowledgement(JsonDocument):
    """
    Says that a request was accepted.
    """

    ok: bool = document_field(AcknowledgementKey.OK, default=True)
    """
    Always true; a refused request is answered with an error status.
    """


class MoveRequestKey(StrEnum):
    """
    The keys of a request moving a free object.
    """

    OBJECT = "object"
    """
    The object's key.
    """

    POSITION = "position"
    """
    The target position.
    """

    QUATERNION = "quaternion"
    """
    The target orientation.
    """

    FINAL = "final"
    """
    Whether this is the last update of a drag.
    """


@dataclass(kw_only=True)
class MoveRequest(JsonDocument):
    """
    Moves a free object to a pose in world coordinates.
    """

    object_key: str = document_field(MoveRequestKey.OBJECT)
    """
    The key of the object, as :class:`LiveObject` names it.
    """

    position: Vector3 = document_field(MoveRequestKey.POSITION)
    """
    The target position in world coordinates.
    """

    quaternion: Optional[Quaternion] = document_field(
        MoveRequestKey.QUATERNION, default=None
    )
    """
    The target orientation, or None to keep the current one.
    """

    final: bool = document_field(MoveRequestKey.FINAL, default=False)
    """
    Whether this is the last update of a drag.
    """

    def __post_init__(self) -> None:
        numbers = FiniteNumbers(type(self).__name__)
        numbers.require(MoveRequestKey.POSITION, self.position.to_json())
        if self.quaternion is not None:
            numbers.require(MoveRequestKey.QUATERNION, self.quaternion.to_json())


class JointRequestKey(StrEnum):
    """
    The keys of a request setting a joint.
    """

    JOINT = "joint"
    """
    The joint's name.
    """

    POSITION = "position"
    """
    The joint value.
    """

    FINAL = "final"
    """
    Whether this is the last update of a slider.
    """


@dataclass(kw_only=True)
class JointRequest(JsonDocument):
    """
    Sets the value of one movable joint.
    """

    joint: str = document_field(JointRequestKey.JOINT)
    """
    The prefixed name of the joint's connection.
    """

    position: float = document_field(JointRequestKey.POSITION)
    """
    The joint value: radians for a revolute joint, metres for a prismatic one.
    """

    final: bool = document_field(JointRequestKey.FINAL, default=False)
    """
    Whether this is the last update of a slider.
    """

    def __post_init__(self) -> None:
        FiniteNumbers(type(self).__name__).require(
            JointRequestKey.POSITION, [self.position]
        )


class TeleoperationRequestKey(StrEnum):
    """
    The keys of a request moving a robot's hand.
    """

    ARM = "arm"
    """
    The arm.
    """

    POSITION = "position"
    """
    The hand target in the arm's workspace.
    """

    GRIPPER = "gripper"
    """
    How far the gripper opens.
    """


@dataclass(kw_only=True)
class TeleoperationRequest(JsonDocument):
    """
    Moves one arm's hand towards a target in its workspace.
    """

    arm: str = document_field(TeleoperationRequestKey.ARM)
    """
    The name of the arm's semantic annotation.
    """

    position: Vector3 = document_field(TeleoperationRequestKey.POSITION)
    """
    The target in the arm's workspace box, each coordinate between -1 and 1.
    """

    gripper: Optional[float] = document_field(
        TeleoperationRequestKey.GRIPPER, default=None
    )
    """
    How far the gripper opens, from 0 closed to 1 open, or None to leave it.
    """

    def __post_init__(self) -> None:
        numbers = FiniteNumbers(type(self).__name__)
        numbers.require(TeleoperationRequestKey.POSITION, self.position.to_json())
        if self.gripper is not None:
            numbers.require(TeleoperationRequestKey.GRIPPER, [self.gripper])


class ConstraintMonitorType(StrEnum):
    """
    The monitors a constraint may become.
    """

    VECTORS_ALIGNED = "VectorsAligned"
    """
    Observes whether two vectors are aligned.
    """

    POINTING_AT = "PointingAt"
    """
    Observes whether an axis points at a point.
    """

    HEIGHT = "HeightMonitor"
    """
    Observes whether a point's height lies within limits.
    """

    DISTANCE = "DistanceMonitor"
    """
    Observes whether a point's distance lies within limits.
    """

    IN_FIELD_OF_VIEW = "InFieldOfView"
    """
    Observes whether a body lies inside the field of view of the robot's camera.
    """


class ConstraintActivation(StrEnum):
    """
    When a constraint starts to be observed.
    """

    NEXT_ACTIVATION = "next_activation"
    """
    When the next motion of the plan starts.
    """

    IMMEDIATE = "immediate"
    """
    At the next tick of the world.
    """


class ConstraintOperation(StrEnum):
    """
    What a constraint request does.
    """

    ATTACH_MONITOR = "attach_monitor"
    """
    Attaches a monitor to a plan node.
    """


class PlanNodeReferenceKey(StrEnum):
    """
    The keys naming a plan node in a constraint request.
    """

    IDENTIFIER = "id"
    """
    The node's identity.
    """

    KIND = "kind"
    """
    The node's class name.
    """

    LABEL = "label"
    """
    The node's description.
    """


@dataclass(kw_only=True)
class PlanNodeReference(JsonDocument):
    """
    Names a node of the live plan.
    """

    identifier: str = document_field(PlanNodeReferenceKey.IDENTIFIER)
    """
    The node's identity, as :class:`LivePlanNode` names it.
    """

    kind: Optional[str] = document_field(PlanNodeReferenceKey.KIND, default=None)
    """
    The node's class name.
    """

    label: Optional[str] = document_field(PlanNodeReferenceKey.LABEL, default=None)
    """
    The node's description.
    """


class MonitorSpecificationKey(StrEnum):
    """
    The keys of the monitor a constraint becomes.
    """

    TYPE = "type"
    """
    The monitor class.
    """

    PARAMETERS = "params"
    """
    The monitor's parameters.
    """


class MonitorParameterKey(StrEnum):
    """
    The parameters a constraint request may give its monitor, by their keys in
    :attr:`MonitorSpecification.parameters`.
    """

    ROOT_LINK = "root_link"
    """
    The body the monitor measures from; the world's root when absent.
    """

    TIP_LINK = "tip_link"
    """
    The body the monitor observes.
    """

    GOAL_NORMAL = "goal_normal"
    """
    The direction in the root body the tip's normal should align with.
    """

    TIP_NORMAL = "tip_normal"
    """
    The direction in the tip body that should align.
    """

    THRESHOLD = "threshold"
    """
    The tolerance of an alignment or pointing monitor.
    """

    GOAL_POINT = "goal_point"
    """
    A point in the root body the tip should point at.
    """

    GOAL_POINT_BODY = "goal_point_body"
    """
    A body whose origin the tip should point at.
    """

    POINTING_AXIS = "pointing_axis"
    """
    The axis of the tip that should point.
    """

    LOWER_LIMIT = "lower_limit"
    """
    The lowest allowed height or distance.
    """

    UPPER_LIMIT = "upper_limit"
    """
    The highest allowed height or distance.
    """

    TARGET = "target"
    """
    The body a field of view monitor keeps in view.
    """

    MARGIN = "margin"
    """
    The angle in radians a body kept in view keeps from every edge of the view.
    """


@dataclass(kw_only=True)
class MonitorSpecification(JsonDocument):
    """
    The monitor a constraint becomes, as the browser compiled it.
    """

    monitor_type: ConstraintMonitorType = document_field(MonitorSpecificationKey.TYPE)
    """
    The monitor class.
    """

    parameters: Dict[str, Any] = document_field(
        MonitorSpecificationKey.PARAMETERS, default_factory=dict
    )
    """
    The monitor's parameters by name; body names are resolved in the live world.
    """


class ConstraintRequestKey(StrEnum):
    """
    The keys of a request attaching a constraint to a plan node.
    """

    OPERATION = "op"
    """
    What the request does.
    """

    TEXT = "text"
    """
    The constraint as it was written.
    """

    APPLY = "apply"
    """
    When the constraint starts to be observed.
    """

    TARGET_PLAN_NODE = "target_plan_node"
    """
    The plan node the constraint is attached to.
    """

    MONITOR = "giskard_node"
    """
    The monitor the constraint becomes.
    """


@dataclass(kw_only=True)
class ConstraintRequest(JsonDocument):
    """
    Attaches a constraint, compiled to a statechart monitor, to a node of the plan.
    """

    target_plan_node: PlanNodeReference = document_field(
        ConstraintRequestKey.TARGET_PLAN_NODE
    )
    """
    The plan node the constraint is attached to.
    """

    monitor: MonitorSpecification = document_field(ConstraintRequestKey.MONITOR)
    """
    The monitor the constraint becomes.
    """

    text: str = document_field(ConstraintRequestKey.TEXT, default="")
    """
    The constraint as it was written.
    """

    apply: ConstraintActivation = document_field(
        ConstraintRequestKey.APPLY, default=ConstraintActivation.NEXT_ACTIVATION
    )
    """
    When the constraint starts to be observed.
    """

    operation: ConstraintOperation = document_field(
        ConstraintRequestKey.OPERATION, default=ConstraintOperation.ATTACH_MONITOR
    )
    """
    What the request does.
    """
