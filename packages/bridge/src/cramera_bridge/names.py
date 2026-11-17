"""
The one table between CRAM's names and the protocol's: every CRAM class whose name a
saved plan, a recording or a check relies on is pinned here to an identifier of the
protocol's own, so renaming the class in CRAM changes this table and nothing else.

A class is pinned by its dotted path. When CRAM renames or moves it, the path is updated
here and its identifier stays; :attr:`PinnedClass.saved_as` keeps the names files saved
before the protocol had its own names, so they are read once through this table. A class
the workspace offers that is not pinned yet still works under an identifier derived from
its name, and the bridge's tests fail until it is pinned.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum

from typing_extensions import Dict, List, Optional, Tuple

from cramera.cram_imports import CramImports
from cramera_protocol.bundle.scene import DetectedEventKind, PlanNodeKind

# %% pinned classes


def class_path(cls: type) -> str:
    """
    :param cls: A class.
    :return: Where it is defined: its module and name, ``module.Class``.
    """
    return f"{cls.__module__}.{cls.__qualname__}"


@dataclass(frozen=True)
class PinnedClass:
    """
    A CRAM class pinned to a protocol identifier.
    """

    identifier: str
    """
    The protocol's name of the class, which never changes.
    """

    path: str
    """
    Where the class is defined: its module and name, ``module.Class``.
    """

    saved_as: Tuple[str, ...] = ()
    """
    The names files saved for the class before the protocol named it.
    """

    def resolve(self) -> type:
        """
        :return: The class.
        :raises ImportError: When its module is gone.
        :raises AttributeError: When its module defines no such class.
        """
        module, _, name = self.path.rpartition(".")
        return getattr(CramImports().module(module), name)

    def resolve_if_installed(self) -> Optional[type]:
        """
        :return: The class, or None when its module needs a package that is not
            installed, such as a robot's ROS interfaces.
        :raises AttributeError: When its module defines no such class.
        """
        try:
            return self.resolve()
        except ModuleNotFoundError:
            return None


@dataclass(frozen=True)
class PinnedEnum:
    """
    A CRAM enumeration pinned to a protocol identifier, with an identifier for each of
    its members.
    """

    identifier: str
    """
    The protocol's name of the enumeration, which never changes.
    """

    path: str
    """
    Where the enumeration is defined: its module and name, ``module.Enum``.
    """

    members: Tuple[str, ...]
    """
    The identifiers of its members: their names in lower case when it was pinned.
    """

    renamed: Tuple[Tuple[str, str], ...] = ()
    """
    The member identifiers whose member CRAM renamed since, with the member's name now.
    """

    def resolve(self) -> type:
        """
        :return: The enumeration.
        """
        return PinnedClass(self.identifier, self.path).resolve()

    def member_name(self, identifier: str) -> str:
        """
        :param identifier: A member identifier.
        :return: The name of the member it stands for now.
        """
        return dict(self.renamed).get(identifier, identifier.upper())

    def saved_name(self, identifier: str) -> str:
        """
        :param identifier: A member identifier.
        :return: The name files saved the member under before the protocol named it.
        """
        return identifier.upper()


ACTIONS = (
    PinnedClass(
        "close",
        "coraplex.robot_plans.actions.core.container.CloseAction",
        ("CloseAction",),
    ),
    PinnedClass(
        "container",
        "coraplex.robot_plans.actions.core.container.MovesContainerByItsHandle",
        ("ContainerAction",),
    ),
    PinnedClass(
        "detect",
        "coraplex.robot_plans.actions.core.misc.DetectAction",
        ("DetectAction",),
    ),
    PinnedClass(
        "elevator_navigation",
        "coraplex.robot_plans.actions.core.navigation.ElevatorNavigation",
        ("ElevatorNavigation",),
    ),
    PinnedClass(
        "face_at",
        "coraplex.robot_plans.actions.core.navigation.FaceAtAction",
        ("FaceAtAction",),
    ),
    PinnedClass(
        "follow_tool_center_point_path",
        "coraplex.robot_plans.actions.core.robot_body.FollowToolCenterPointPathAction",
        ("FollowToolCenterPointPathAction",),
    ),
    PinnedClass(
        "grasping",
        "coraplex.robot_plans.actions.core.pick_up.GraspingAction",
        ("GraspingAction",),
    ),
    PinnedClass(
        "look_at",
        "coraplex.robot_plans.actions.core.navigation.LookAtAction",
        ("LookAtAction",),
    ),
    PinnedClass(
        "move_manipulator",
        "coraplex.robot_plans.actions.core.robot_body.MoveManipulatorAction",
        ("MoveManipulatorAction",),
    ),
    PinnedClass(
        "move_to_reach",
        "coraplex.robot_plans.actions.core.misc.MoveToReach",
        ("MoveToReach",),
    ),
    PinnedClass(
        "move_fingers_to_width",
        "coraplex.robot_plans.actions.core.robot_body.MoveFingersToWidthAction",
        ("MoveFingersToWidthAction",),
    ),
    PinnedClass(
        "move_torso",
        "coraplex.robot_plans.actions.core.robot_body.MoveTorsoAction",
        ("MoveTorsoAction",),
    ),
    PinnedClass(
        "navigate",
        "coraplex.robot_plans.actions.core.navigation.NavigateAction",
        ("NavigateAction",),
    ),
    PinnedClass(
        "open",
        "coraplex.robot_plans.actions.core.container.OpenAction",
        ("OpenAction",),
    ),
    PinnedClass(
        "park_arms",
        "coraplex.robot_plans.actions.core.robot_body.ParkArmsAction",
        ("ParkArmsAction",),
    ),
    PinnedClass(
        "path_planning_navigate",
        "coraplex.robot_plans.actions.core.navigation.PathPlanningNavigateAction",
        ("PathPlanningNavigateAction",),
    ),
    PinnedClass(
        "pick_up",
        "coraplex.robot_plans.actions.core.pick_up.PickUpAction",
        ("PickUpAction",),
    ),
    PinnedClass(
        "place",
        "coraplex.robot_plans.actions.core.placing.PlaceAction",
        ("PlaceAction",),
    ),
    PinnedClass(
        "reach",
        "coraplex.robot_plans.actions.core.pick_up.ReachAction",
        ("ReachAction",),
    ),
    PinnedClass(
        "set_gripper",
        "coraplex.robot_plans.actions.core.robot_body.SetGripperAction",
        ("SetGripperAction",),
    ),
    PinnedClass(
        "carry",
        "coraplex.robot_plans.actions.composite.carrying.CarryAction",
        ("CarryAction",),
    ),
    PinnedClass(
        "cutting",
        "coraplex.robot_plans.actions.composite.tool_based.CuttingAction",
        ("CuttingAction",),
    ),
    PinnedClass(
        "face_and_look_at",
        "coraplex.robot_plans.actions.composite.facing.FaceAndLookAtAction",
        ("FaceAndLookAtAction",),
    ),
    PinnedClass(
        "mixing",
        "coraplex.robot_plans.actions.composite.tool_based.MixingAction",
        ("MixingAction",),
    ),
    PinnedClass(
        "move_and_open",
        "coraplex.robot_plans.actions.composite.transporting.MoveAndOpenAction",
        ("MoveAndOpenAction",),
    ),
    PinnedClass(
        "move_and_pick_up",
        "coraplex.robot_plans.actions.composite.transporting.MoveAndPickUpAction",
        ("MoveAndPickUpAction",),
    ),
    PinnedClass(
        "move_and_place",
        "coraplex.robot_plans.actions.composite.transporting.MoveAndPlaceAction",
        ("MoveAndPlaceAction",),
    ),
    PinnedClass(
        "look_at_and_detect",
        "coraplex.robot_plans.actions.composite.searching.LookAtAndDetectAction",
        ("LookAtAndDetectAction",),
    ),
    PinnedClass(
        "pick_and_place",
        "coraplex.robot_plans.actions.composite.transporting.PickAndPlaceAction",
        ("PickAndPlaceAction",),
    ),
    PinnedClass(
        "pouring",
        "coraplex.robot_plans.actions.composite.tool_based.PouringAction",
        ("PouringAction",),
    ),
    PinnedClass(
        "search",
        "coraplex.robot_plans.actions.composite.searching.SearchAction",
        ("SearchAction",),
    ),
    PinnedClass(
        "transport",
        "coraplex.robot_plans.actions.composite.transporting.TransportAction",
        ("TransportAction",),
    ),
    PinnedClass(
        "wiping",
        "coraplex.robot_plans.actions.composite.tool_based.WipingAction",
        ("WipingAction",),
    ),
)
"""
The native actions a plan step names.
"""

ROBOTS = (
    PinnedClass("armar7", "semantic_digital_twin.robots.armar7.Armar7", ("Armar7",)),
    PinnedClass("daisy", "semantic_digital_twin.robots.daisy.DAiSy", ("DAiSy",)),
    PinnedClass("garmi", "semantic_digital_twin.robots.garmi.Garmi", ("Garmi",)),
    PinnedClass("hsrb", "semantic_digital_twin.robots.hsrb.HSRB", ("HSRB",)),
    PinnedClass("icub3", "semantic_digital_twin.robots.icub3.ICub3", ("ICub3",)),
    PinnedClass("justin", "semantic_digital_twin.robots.justin.Justin", ("Justin",)),
    PinnedClass(
        "mmp_dresden",
        "semantic_digital_twin.robots.mmp_dresden.MMPDresden",
        ("MMPDresden",),
    ),
    PinnedClass("pr2", "semantic_digital_twin.robots.pr2.PR2", ("PR2",)),
    PinnedClass(
        "stretch", "semantic_digital_twin.robots.stretch.Stretch", ("Stretch",)
    ),
    PinnedClass("tiago", "semantic_digital_twin.robots.tiago.Tiago", ("Tiago",)),
    PinnedClass(
        "tiago_mujoco",
        "semantic_digital_twin.robots.tiago.TiagoMujoco",
        ("TiagoMujoco",),
    ),
    PinnedClass("tracy", "semantic_digital_twin.robots.tracy.Tracy", ("Tracy",)),
    PinnedClass(
        "unitree_g1",
        "semantic_digital_twin.robots.unitree_g1.UnitreeG1",
        ("UnitreeG1",),
    ),
)
"""
The robot annotations a plan's robot names as its model.
"""

PLAN_NODE_KINDS = (
    ("coraplex.robot_plans.actions.base.Action", PlanNodeKind.ACTION),
    ("cramph.composites.StatementNode", PlanNodeKind.UNDERSPECIFIED),
    (
        "giskardpy.motion_statechart.graph_node.MotionStatechartNode",
        PlanNodeKind.MOTION,
    ),
    ("cramph.composites.MonitoredCompositeNode", PlanNodeKind.MONITOR),
    ("cramph.composites.Sequence", PlanNodeKind.SEQUENCE),
    ("cramph.composites.TryInOrder", PlanNodeKind.SEQUENCE),
    ("cramph.composites.RepeatUntil", PlanNodeKind.SEQUENCE),
    ("cramph.composites.Parallel", PlanNodeKind.PARALLEL),
    ("cramph.composites.TryAll", PlanNodeKind.PARALLEL),
    ("cramph.threaded_nodes.FunctionCall", PlanNodeKind.CODE),
    ("cramph.world_modification_nodes.MoveBranch", PlanNodeKind.ATTACHMENT),
)
"""
The statechart node classes by the kind the protocol names them, tried in order: an
action is of its own kind although it is also the composite of its steps, and a node is
of the kind of the first class it is an instance of, else :attr:`PlanNodeKind.OTHER`.
"""

SEGMIND_EVENTS = "segmind.datastructures.events"
"""
The module segmind defines its events in.
"""

DETECTED_EVENT_KINDS = (
    (f"{SEGMIND_EVENTS}.AbstractContactEvent", DetectedEventKind.CONTACT),
    (f"{SEGMIND_EVENTS}.MotionEvent", DetectedEventKind.MOTION),
    (f"{SEGMIND_EVENTS}.SupportEvent", DetectedEventKind.SUPPORT),
    (f"{SEGMIND_EVENTS}.LossOfSupportEvent", DetectedEventKind.SUPPORT),
    (f"{SEGMIND_EVENTS}.ContainmentEvent", DetectedEventKind.CONTAINMENT),
    (f"{SEGMIND_EVENTS}.LossOfContainmentEvent", DetectedEventKind.CONTAINMENT),
    (f"{SEGMIND_EVENTS}.InsertionEvent", DetectedEventKind.CONTAINMENT),
    (f"{SEGMIND_EVENTS}.GraspEvent", DetectedEventKind.GRASP),
    (f"{SEGMIND_EVENTS}.LossOfGraspEvent", DetectedEventKind.GRASP),
    (f"{SEGMIND_EVENTS}.PickUpEvent", DetectedEventKind.PICK_AND_PLACE),
    (f"{SEGMIND_EVENTS}.PlacingEvent", DetectedEventKind.PICK_AND_PLACE),
)
"""
The event classes of segmind by the kind the protocol names them, tried in order; an
event is of the kind of the first class it is an instance of, else
:attr:`DetectedEventKind.OTHER`.
"""

ANNOTATIONS = (
    PinnedClass(
        "bowl",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Bowl",
        ("Bowl",),
    ),
    PinnedClass(
        "cabinet",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Cabinet",
        ("Cabinet",),
    ),
    PinnedClass(
        "coffee_table",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.CoffeeTable",
        ("CoffeeTable",),
    ),
    PinnedClass(
        "counter_top",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.CounterTop",
        ("CounterTop",),
    ),
    PinnedClass(
        "cupboard",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Cupboard",
        ("Cupboard",),
    ),
    PinnedClass(
        "desk",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Desk",
        ("Desk",),
    ),
    PinnedClass(
        "dining_table",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.DiningTable",
        ("DiningTable",),
    ),
    PinnedClass(
        "dishwasher",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Dishwasher",
        ("Dishwasher",),
    ),
    PinnedClass(
        "drawer",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Drawer",
        ("Drawer",),
    ),
    PinnedClass(
        "dresser",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Dresser",
        ("Dresser",),
    ),
    PinnedClass(
        "elevator",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Elevator",
        ("Elevator",),
    ),
    PinnedClass(
        "floor",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Floor",
        ("Floor",),
    ),
    PinnedClass(
        "fridge",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Fridge",
        ("Fridge",),
    ),
    PinnedClass(
        "handle",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Handle",
        ("Handle",),
    ),
    PinnedClass(
        "level",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Level",
        ("Level",),
    ),
    PinnedClass(
        "mounting_table",
        "semantic_digital_twin.robots.robot_parts.MountingTable",
        ("MountingTable",),
    ),
    PinnedClass(
        "plate",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Plate",
        ("Plate",),
    ),
    PinnedClass(
        "shelf",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Shelf",
        ("Shelf",),
    ),
    PinnedClass(
        "shelf_layer",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.ShelfLayer",
        ("ShelfLayer",),
    ),
    PinnedClass(
        "side_table",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.SideTable",
        ("SideTable",),
    ),
    PinnedClass(
        "sofa",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Sofa",
        ("Sofa",),
    ),
    PinnedClass(
        "table",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Table",
        ("Table",),
    ),
    PinnedClass(
        "tool",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Tool",
        ("Tool",),
    ),
    PinnedClass(
        "tracy_table", "semantic_digital_twin.robots.tracy.TracyTable", ("TracyTable",)
    ),
    PinnedClass(
        "tumble_dryer",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.TumbleDryer",
        ("TumbleDryer",),
    ),
    PinnedClass(
        "trash_can",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.TrashCan",
        ("TrashCan",),
    ),
    PinnedClass(
        "washing_machine",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.WashingMachine",
        ("WashingMachine",),
    ),
    PinnedClass(
        "wardrobe",
        "semantic_digital_twin.semantic_annotations.semantic_annotations.Wardrobe",
        ("Wardrobe",),
    ),
)
"""
The semantic annotation classes a plan names: the surfaces objects are put on or in, and
the classes action fields take an annotation of.
"""

ENUMS = (
    PinnedEnum(
        "arms",
        "cramera_bridge.plans.arms.Arms",
        (
            "left",
            "right",
            "both",
            "automatic",
        ),
    ),
    PinnedEnum(
        "color",
        "semantic_digital_twin.world_description.color_names.ColorName",
        (
            "red",
            "yellow",
            "green",
            "cyan",
            "blue",
            "magenta",
            "white",
            "black",
            "grey",
        ),
    ),
    PinnedEnum(
        "object_type",
        "cramera_bridge.plans.object_types.ObjectType",
        (
            "object",
            "block",
        ),
    ),
    PinnedEnum(
        "cutting_technique",
        "coraplex.datastructures.enums.CuttingTechnique",
        (
            "slice",
            "saw",
            "halving",
        ),
    ),
    PinnedEnum(
        "detection_state",
        "coraplex.datastructures.enums.DetectionState",
        (
            "start",
            "stop",
            "pause",
        ),
    ),
    PinnedEnum(
        "detection_technique",
        "coraplex.datastructures.enums.DetectionTechnique",
        (
            "all",
            "human",
            "types",
            "region",
            "human_attributes",
            "human_waving",
        ),
    ),
    PinnedEnum(
        "pouring_side",
        "coraplex.datastructures.enums.PouringSide",
        (
            "left",
            "right",
        ),
    ),
    PinnedEnum(
        "slicing_priority",
        "coraplex.datastructures.enums.SlicingPriority",
        (
            "thickness",
            "cut_count",
        ),
    ),
    PinnedEnum(
        "wiping_technique",
        "coraplex.datastructures.enums.WipingTechnique",
        (
            "wipe",
            "shear",
            "spread",
        ),
    ),
    PinnedEnum(
        "gripper_state",
        "semantic_digital_twin.datastructures.definitions.GripperState",
        (
            "open",
            "close",
            "medium",
        ),
    ),
    PinnedEnum(
        "torso_state",
        "semantic_digital_twin.datastructures.definitions.TorsoState",
        (
            "high",
            "mid",
            "low",
        ),
    ),
)
"""
The enumerations action fields take a member of, with the identifier of each member.
"""

FIELDS = {
    "close": ("approach_clearance", "arm", "release_clearance"),
    "container": ("approach_clearance", "arm", "release_clearance"),
    "detect": (
        "technique",
        "state",
        "object_sem_annotation",
        "region",
        "trust_detected_orientation",
        "accept_first_if_multiple",
        "color",
        "attributes",
    ),
    "elevator_navigation": (
        "elevator",
        "target_floor",
        "exit_clearance",
        "arrival_threshold",
    ),
    "face_at": ("target",),
    "follow_tool_center_point_path": (
        "position_threshold",
        "orientation_threshold",
        "target_locations",
        "arm",
    ),
    "grasping": (
        "position_threshold",
        "orientation_threshold",
        "grasp_detection_threshold",
        "pre_approach_linear_velocity",
        "final_approach_linear_velocity",
        "grasp_closing_velocity",
        "lift_linear_velocity",
        "grasp_stall_minimum_time",
        "object_friction",
        "approach_clearance",
        "retreat_distance",
        "grasp",
        "arm",
        "tolerate_grasp_stall",
    ),
    "look_at": ("target", "camera"),
    "move_manipulator": (
        "position_threshold",
        "orientation_threshold",
        "target_pose",
        "end_effector",
        "allow_gripper_collision",
    ),
    "move_to_reach": (
        "position_threshold",
        "orientation_threshold",
        "approach_clearance",
        "retreat_distance",
        "target_pose_offset_robot",
        "hip_rotation",
        "reference_T_grasp",
        "end_effector",
    ),
    "move_torso": ("torso_state",),
    "navigate": ("target_location",),
    "open": (
        "approach_clearance",
        "arm",
        "release_clearance",
    ),
    "park_arms": ("max_joint_velocity", "arms"),
    "path_planning_navigate": ("target",),
    "pick_up": (
        "position_threshold",
        "orientation_threshold",
        "grasp_detection_threshold",
        "pre_approach_linear_velocity",
        "final_approach_linear_velocity",
        "grasp_closing_velocity",
        "lift_linear_velocity",
        "grasp_stall_minimum_time",
        "object_friction",
        "approach_clearance",
        "retreat_distance",
        "grasp",
        "arm",
        "tolerate_grasp_stall",
    ),
    "place": (
        "position_threshold",
        "orientation_threshold",
        "grasp_detection_threshold",
        "placing_linear_velocity",
        "transport_linear_velocity",
        "release_opening_velocity",
        "retract_linear_velocity",
        "approach_clearance",
        "retreat_distance",
        "object_designator",
        "target_location",
        "grasp_release_threshold",
    ),
    "reach": (
        "position_threshold",
        "orientation_threshold",
        "grasp_detection_threshold",
        "pre_approach_linear_velocity",
        "final_approach_linear_velocity",
        "approach_clearance",
        "retreat_distance",
        "arm",
        "grasp",
        "reverse_reach_order",
        "open_gripper_at_pre_pose",
    ),
    "set_gripper": ("gripper", "motion"),
    "cutting": (
        "position_threshold",
        "orientation_threshold",
        "arm",
        "tool",
        "pointer_stride",
        "object_to_cut",
        "technique",
        "slice_thickness",
        "number_of_cuts_on_local_x_axis",
        "slicing_priority",
    ),
    "face_and_look_at": ("face_at", "look_at"),
    "mixing": (
        "position_threshold",
        "orientation_threshold",
        "arm",
        "tool",
        "pointer_stride",
        "container",
        "mix_duration",
    ),
    "move_and_open": ("navigate", "face_and_look_at", "open_container"),
    "move_and_pick_up": ("navigate", "face_and_look_at", "pick_up"),
    "move_and_place": ("face_and_look_at", "place"),
    "pick_and_place": ("pick_up", "place"),
    "pouring": (
        "position_threshold",
        "orientation_threshold",
        "target_container",
        "source_container",
        "arm",
        "tilt_angle",
        "pour_side",
        "pour_side_offset",
        "pour_approach_offset",
        "pour_height",
    ),
    "transport": ("pick_up", "place"),
    "wiping": (
        "position_threshold",
        "orientation_threshold",
        "arm",
        "tool",
        "pointer_stride",
        "surface",
        "target_pose",
        "technique",
        "length",
        "cycles",
        "final_waypoint_success_tolerance",
    ),
}
"""
The identifier of every field of each pinned action, by the action's identifier: the
field names when the action was pinned.
"""

FIELD_RENAMES: Dict[str, Dict[str, str]] = {"detect": {"region": "detection_box"}}
"""
The field each identifier stands for where CRAM renamed the field since the action was
pinned, by the action's identifier and the field's identifier.
"""

# %% identifiers of classes not pinned yet

WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
"""
Where a class name's words meet.
"""

ACTION_SUFFIX = "Action"
"""
The ending of most action class names, left out of their identifiers.
"""


def derived_identifier(class_name: str, suffix: str = "") -> str:
    """
    :param class_name: A class name, such as ``PickUpAction``.
    :param suffix: An ending left out, such as ``Action``.
    :return: Its words in lower case joined by underscores, such as ``pick_up``.
    """
    words = class_name.removesuffix(suffix) or class_name
    return WORD_BOUNDARY.sub("_", words).lower()


# %% tables


@dataclass(frozen=True)
class ClassNames:
    """
    Translates between the classes of one table and their protocol identifiers.
    """

    pinned: Tuple[PinnedClass, ...]
    """
    The pinned classes.
    """

    suffix: str = ""
    """
    The ending derived identifiers leave out.
    """

    def entry_of(self, cls: type) -> Optional[PinnedClass]:
        """
        :param cls: A CRAM class.
        :return: Its pinned entry, or None when it is not pinned.
        """
        path = class_path(cls)
        return next((entry for entry in self.pinned if entry.path == path), None)

    def identifier_of(self, cls: type) -> str:
        """
        :param cls: A CRAM class.
        :return: Its pinned identifier, else one derived from its name.
        """
        entry = self.entry_of(cls)
        if entry is None:
            return derived_identifier(cls.__name__, self.suffix)
        return entry.identifier

    def saved_as(self, cls: type) -> List[str]:
        """
        :param cls: A CRAM class.
        :return: The names files saved for it before the protocol named it; for a class
            not pinned yet, its class name.
        """
        entry = self.entry_of(cls)
        return [cls.__name__] if entry is None else list(entry.saved_as)

    def matches(self, cls: type, name: str) -> bool:
        """
        :param cls: A CRAM class.
        :param name: A name a plan or a file uses for a class.
        :return: Whether the name is the class's identifier or a name it was saved as.
        """
        return name == self.identifier_of(cls) or name in self.saved_as(cls)


ACTION_NAMES = ClassNames(ACTIONS, ACTION_SUFFIX)
"""
The identifiers of the native actions.
"""

ROBOT_NAMES = ClassNames(ROBOTS)
"""
The identifiers of the robot annotations.
"""


@functools.cache
def plan_node_classes() -> Tuple[Tuple[type, PlanNodeKind], ...]:
    """
    :return: The classes of :data:`PLAN_NODE_KINDS` with their kinds, imported once.
    """
    return tuple(
        (PinnedClass(kind, path).resolve(), kind) for path, kind in PLAN_NODE_KINDS
    )


def plan_node_kind(node: object) -> PlanNodeKind:
    """
    :param node: A plan node.
    :return: The kind the protocol names it.
    """
    for cls, kind in plan_node_classes():
        if isinstance(node, cls):
            return kind
    return PlanNodeKind.OTHER


@functools.cache
def detected_event_classes() -> Tuple[Tuple[type, DetectedEventKind], ...]:
    """
    :return: The classes of :data:`DETECTED_EVENT_KINDS` with their kinds, imported
        once.
    """
    return tuple(
        (PinnedClass(kind, path).resolve(), kind) for path, kind in DETECTED_EVENT_KINDS
    )


def detected_event_kind(event: object) -> DetectedEventKind:
    """
    :param event: An event segmind detected.
    :return: The kind the protocol names it.
    """
    for cls, kind in detected_event_classes():
        if isinstance(event, cls):
            return kind
    return DetectedEventKind.OTHER


ANNOTATION_NAMES = ClassNames(ANNOTATIONS)
"""
The identifiers of the semantic annotation classes.
"""


# %% enumerations


@dataclass(frozen=True)
class EnumNames:
    """
    Translates between enumerations and their members and protocol identifiers.
    """

    pinned: Tuple[PinnedEnum, ...]
    """
    The pinned enumerations.
    """

    def entry_of(self, enumeration: type) -> Optional[PinnedEnum]:
        """
        :param enumeration: An enumeration.
        :return: Its pinned entry, or None when it is not pinned.
        """
        path = f"{enumeration.__module__}.{enumeration.__name__}"
        return next((entry for entry in self.pinned if entry.path == path), None)

    def identifier_of(self, enumeration: type) -> str:
        """
        :param enumeration: An enumeration.
        :return: Its pinned identifier, else one derived from its name.
        """
        entry = self.entry_of(enumeration)
        if entry is None:
            return derived_identifier(enumeration.__name__)
        return entry.identifier

    def member_identifier(self, enumeration: type, member: Enum) -> str:
        """
        :param enumeration: An enumeration.
        :param member: One of its members.
        :return: The member's identifier: pinned, else its name in lower case.
        """
        entry = self.entry_of(enumeration)
        if entry is not None:
            for identifier in entry.members:
                if entry.member_name(identifier) == member.name:
                    return identifier
        return member.name.lower()

    def member(self, enumeration: type, name: str) -> Enum:
        """
        :param enumeration: An enumeration.
        :param name: A member's identifier, or the name a file saved it under before.
        :return: The member.
        :raises KeyError: When the enumeration has no such member.
        """
        for member in enumeration:
            identifier = self.member_identifier(enumeration, member)
            if name in (identifier, self.saved_name(enumeration, identifier)):
                return member
        raise KeyError(name)

    def saved_name(self, enumeration: type, identifier: str) -> str:
        """
        :param enumeration: An enumeration.
        :param identifier: A member identifier.
        :return: The name files saved the member under before the protocol named it.
        """
        entry = self.entry_of(enumeration)
        return identifier.upper() if entry is None else entry.saved_name(identifier)

    def saved_members(self, enumeration: type) -> Dict[str, str]:
        """
        :param enumeration: An enumeration.
        :return: The identifier of every member by the name files saved it under before.
        """
        identifiers = [
            self.member_identifier(enumeration, member) for member in enumeration
        ]
        return {
            self.saved_name(enumeration, identifier): identifier
            for identifier in identifiers
        }


ENUM_NAMES = EnumNames(ENUMS)
"""
The identifiers of the enumerations and their members.
"""


# %% fields


@dataclass(frozen=True)
class FieldNames:
    """
    Translates between the fields of actions and their protocol identifiers.
    """

    renamed: Dict[str, Dict[str, str]]
    """
    The field each identifier stands for where CRAM renamed it, by action identifier.
    """

    def identifier_of(self, action: str, field_name: str) -> str:
        """
        :param action: An action's identifier.
        :param field_name: One of its fields as CRAM names it.
        :return: The field's identifier.
        """
        for identifier, renamed in self.renamed.get(action, {}).items():
            if renamed == field_name:
                return identifier
        return field_name

    def field_name(self, action: str, identifier: str) -> str:
        """
        :param action: An action's identifier.
        :param identifier: A field identifier.
        :return: The field as CRAM names it now.
        """
        return self.renamed.get(action, {}).get(identifier, identifier)


FIELD_NAMES = FieldNames(FIELD_RENAMES)
"""
The identifiers of the actions' fields.
"""
