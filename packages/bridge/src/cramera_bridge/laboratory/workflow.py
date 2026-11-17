"""
A PR2 works through the small laboratory: it re-sorts the rack, stores a vessel in the
sample refrigerator and loads a balanced pair into the centrifuge, opening and closing
the refrigerator's door and the centrifuge's lid and driving between its stands at the
bench; the run is published, recorded and measured.

The measured outcome: every motion succeeds, every moved vessel was carried and set down
within a millimetre of its new slot, the refrigerator's door and the centrifuge's lid
were opened and are closed again, the vessel and the stopper the workflow does not move
stayed where they were, and the gripper's pads never cut into glass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from typing_extensions import Dict, List, Optional, Tuple

from coraplex.robot_plans.actions.core.pick_up import GraspingAction
from coraplex.robot_plans.actions.core.robot_body import MoveManipulatorAction
from cramph.composites import Sequence
from cramph.data_types import LifeCycleValues
from cramph.node import StatechartNode
from semantic_digital_twin.callbacks.callback import StateChangeCallback
from semantic_digital_twin.collision_checking.collision_rules import (
    AvoidCollisionBetweenGroups,
)
from semantic_digital_twin.datastructures.definitions import GripperState
from semantic_digital_twin.grasping.grasp_candidates import GraspCandidate
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.semantic_annotations.semantic_annotations import Handle
from semantic_digital_twin.specifications.robots import RobotSpecification
from krrood_spatial_math.spatial_types import (
    HomogeneousTransformationMatrix,
    Pose,
    Quaternion,
    RotationMatrix,
)
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF
from semantic_digital_twin.world_description.world_entity import Body

from cramera_bridge.exceptions import TaskFailed
from cramera_bridge.laboratory.grasping import MoveGripperAction, PadContact
from cramera_bridge.laboratory.handling import VesselGrip, VesselTaskDemo
from cramera_bridge.laboratory.mobility import (
    DriveAction,
    HeldBaseJointsAction,
    MechanismAction,
    TuckArmsAction,
)
from cramera_bridge.laboratory.transfer import POSE_TOLERANCE, PENETRATION_TOLERANCE
from cramera_bridge.laboratory.world import LaboratoryEnvironment, LaboratoryHandle
from cramera_bridge.performing import TaskContext
from cramera_bridge.scenes.presentation import ScenePresentation
from cramera_protocol.bundle.acceptance import AcceptanceMetrics
from cramera_protocol.bundle.scene import Camera
from cramera_protocol.geometry import Vector3
from cramera_protocol.laboratory.runs import TaskRecording
from cramera_protocol.live_messages import SavedScene

TASK = "PR2: rack, refrigerator and centrifuge"
"""
What the task does, as the scene picker labels it.
"""

WORKFLOW_CAMERA = Camera(
    position=Vector3(1.9, -3.3, 2.4), target=Vector3(-0.35, -0.25, 0.8)
)
"""
The recording's first view: the PR2, the bench, the centrifuge and the refrigerator.
"""

CLOSED_TOLERANCE = 0.01
"""
How far from its stop a closed door or lid may stand, in radians.
"""

OPENED_TOLERANCE = 0.02
"""
How far short of its goal an opened door or lid may have stopped, in radians.
"""

# %% layout


@dataclass(frozen=True)
class Stand:
    """
    Where the PR2's base stands to work at one station.
    """

    x: float
    """
    The base's x coordinate in the laboratory, in metres.
    """

    y: float
    """
    The base's y coordinate in the laboratory, in metres.
    """

    heading: float = math.pi / 2
    """
    Which way the base faces, in radians about the vertical.
    """

    def pose(self, world: World) -> Pose:
        """
        :param world: A world holding the laboratory.
        :return: The base's pose at the stand.
        """
        return Pose.from_xyz_rpy(
            self.x, self.y, yaw=self.heading, reference_frame=world.root
        )


RACK_STAND = Stand(0.40, -0.72)
"""
In front of the rack, where the PR2 starts.
"""

CENTRIFUGE_STAND = Stand(-0.22, -0.72)
"""
In front of the centrifuge, its left shoulder beside the lid's handle.
"""

CENTRIFUGE_STEP_BACK = Stand(-0.22, -0.88)
"""
A step back from :data:`CENTRIFUGE_STAND`, where the tucked arm is clear of the
centrifuge's housing while the torso rises.
"""

LOADING_STAND = Stand(-0.12, -0.72)
"""
In front of the centrifuge, where the left arm holds a vessel above every holder of the
rotor, its bottom clear of the centrifuge's rim.
"""

DOOR_STAND = Stand(-0.95, -0.95)
"""
In front of the refrigerator's door, clear of its swing, its left arm able to follow the
handle until the door stands beside the refrigerator.
"""

STORING_DEPTH = -1.05
"""
How far from the bench the base drives sideways with a vessel held out level, and stands
while it lowers the vessel in front of the refrigerator, in metres along y.
"""


@dataclass(frozen=True)
class StoringStands:
    """
    Where the PR2 stands while it stores a vessel in the refrigerator, holding it out
    level in front of its left shoulder.
    """

    pick: Stand
    """
    In front of the vessel's rack slot, the left shoulder behind the vessel.
    """

    back: Stand
    """
    Straight back from :attr:`pick`, the vessel in front of the bench's edge.
    """

    front: Stand
    """
    In front of the refrigerator, the vessel in front of its holder.
    """

    inside: Stand
    """
    Driven up to the refrigerator, the vessel above its holder.
    """

    def route(self) -> List[Stand]:
        """
        :return: The stands in the order the PR2 drives to them.
        """
        return [self.pick, self.back, self.front, self.inside]


class Station(StrEnum):
    """
    What holds the vessels at the bench.
    """

    RACK = "rack"
    """
    The sample rack.
    """

    CENTRIFUGE = "centrifuge"
    """
    The centrifuge's rotor.
    """

    REFRIGERATOR = "refrigerator"
    """
    The refrigerator's shelves.
    """

    @property
    def position_error(self) -> WorkflowMeasurement:
        """
        The measurement of how far the vessels set down here ended from their slots.
        """
        return {
            Station.RACK: WorkflowMeasurement.RACK_POSITION_ERROR,
            Station.CENTRIFUGE: WorkflowMeasurement.CENTRIFUGE_POSITION_ERROR,
            Station.REFRIGERATOR: WorkflowMeasurement.REFRIGERATOR_POSITION_ERROR,
        }[self]


@dataclass(frozen=True)
class Slot:
    """
    One place a vessel stands in.
    """

    station: Station
    """
    What holds the slot.
    """

    identifier: str
    """
    The slot's identifier in the bench's layout.
    """

    def pose(self, laboratory: LaboratoryEnvironment, world: World) -> Pose:
        """
        :param laboratory: The laboratory holding the slot.
        :param world: A world holding the laboratory.
        :return: Where a vessel's bottom rests in the slot.
        """
        pose_of = {
            Station.RACK: laboratory.slot_pose,
            Station.CENTRIFUGE: laboratory.centrifuge_holder_pose,
            Station.REFRIGERATOR: laboratory.refrigerator_holder_pose,
        }[self.station]
        return pose_of(self.identifier, world)


@dataclass(frozen=True)
class VesselMove:
    """
    One vessel taken from the rack and set into a slot.
    """

    source: str
    """
    The rack slot the vessel is taken from.
    """

    target: Slot
    """
    The slot it is set into.
    """


# %% names


class WorkflowMeasurement(StrEnum):
    """
    What the workflow measures.
    """

    RACK_POSITION_ERROR = "rackPositionError"
    """
    The largest distance between a re-sorted vessel and its target slot, in metres.
    """

    CENTRIFUGE_POSITION_ERROR = "centrifugePositionError"
    """
    The largest distance between a vessel loaded into the centrifuge and its holder, in
    metres.
    """

    REFRIGERATOR_POSITION_ERROR = "refrigeratorPositionError"
    """
    The largest distance between a vessel stored in the refrigerator and its holder, in
    metres.
    """

    WIDEST_DOOR = "widestDoorAngle"
    """
    How far the refrigerator's door was opened, in radians.
    """

    WIDEST_LID = "widestLidAngle"
    """
    How far the centrifuge's lid was opened, in radians.
    """

    DOOR = "doorAngle"
    """
    The refrigerator door's angle at the end, in radians.
    """

    LID = "lidAngle"
    """
    The centrifuge lid's angle at the end, in radians.
    """

    SMALLEST_PAD_CLEARANCE = "smallestPadClearance"
    """
    The smallest clearance between a pad and the outer surface of a carried vessel's
    glass, in metres.
    """

    SMALLEST_COLLISION_DISTANCE = "smallestCollisionDistance"
    """
    The smallest distance between bodies whose collisions are avoided, in metres.
    """


class WorkflowFailure(StrEnum):
    """
    Why the workflow failed.
    """

    MOTIONS = "not every motion succeeded: {statuses}"
    """
    An action ended in another state, or the plan holds none.
    """

    NOT_CARRIED = "the gripper never carried {vessel}"
    """
    A handled vessel was never attached to the gripper.
    """

    NOT_RELEASED = "{vessel} is still held"
    """
    A handled vessel is not attached to the world at the end.
    """

    NOT_SET_DOWN = "the vessels were not set down in the workflow's order: {released}"
    """
    The gripper let go of other vessels, or in another order, than the workflow sets
    down.
    """

    MISPLACED = "{vessel} ended {error:.4f} m from where it belongs"
    """
    A handled vessel ended too far from its target.
    """

    NOT_OPENED = "{part} opened only to {angle:.3f} rad"
    """
    The door or the lid was not opened far enough.
    """

    LEFT_OPEN = "{part} was left open at {angle:.3f} rad"
    """
    The door or the lid is not closed at the end.
    """

    DISTURBED = "a vessel or the stopper the workflow does not handle moved"
    """
    An object that was not handled moved.
    """

    PADS = "a gripper pad cut into the glass by {depth:.6f} m"
    """
    A pad intersected a carried vessel.
    """

    COLLISION = "bodies came {distance:.5f} m into each other"
    """
    A checked collision pair penetrated.
    """


@dataclass(frozen=True)
class VesselPlacing:
    """
    One vessel set down where the workflow takes it.
    """

    vessel: Body
    """
    The vessel.
    """

    target: Pose
    """
    Where the vessel's bottom ends.
    """

    station: Station
    """
    What holds the vessel there.
    """

    def error(self, world: World) -> float:
        """
        :param world: The world holding the vessel.
        :return: The distance between the vessel's bottom and its target, in metres.
        """
        desired = world.transform(self.target.homogeneous_matrix, world.root).to_np()
        return float(
            np.linalg.norm(self.vessel.global_pose.to_np()[:3, 3] - desired[:3, 3])
        )


# %% observation


@dataclass(eq=False)
class WorkflowObservation(StateChangeCallback):
    """
    Measures the handled vessels, the untouched objects, the door and the lid after each
    state update of the run.
    """

    context: TaskContext = field(kw_only=True)
    """
    The world and the robot.
    """

    pads: Dict[Body, PadContact] = field(kw_only=True)
    """
    Measures the pads' clearance from each handled vessel.
    """

    placings: List[VesselPlacing] = field(kw_only=True)
    """
    Where the vessels are set down, in order.
    """

    untouched: List[Body] = field(kw_only=True)
    """
    The objects that must stay where they are.
    """

    door: ActiveConnection1DOF = field(kw_only=True)
    """
    The refrigerator door's hinge.
    """

    lid: ActiveConnection1DOF = field(kw_only=True)
    """
    The centrifuge lid's hinge.
    """

    starts: List[np.ndarray] = field(init=False)
    """
    The untouched objects' transforms before the run, in the order of :attr:`untouched`.
    """

    carried_ticks: Dict[Body, int] = field(init=False)
    """
    How many observed state updates the gripper carried each handled vessel.
    """

    smallest_pad_clearance: Optional[float] = field(init=False, default=None)
    """
    The smallest pad clearance from a carried vessel's glass, in metres.
    """

    smallest_collision_distance: Optional[float] = field(init=False, default=None)
    """
    The smallest distance of a checked collision pair, in metres.
    """

    widest_door: float = field(init=False, default=0.0)
    """
    The door's largest angle so far, in radians.
    """

    widest_lid: float = field(init=False, default=0.0)
    """
    The lid's largest angle so far, in radians.
    """

    untouched_still: bool = field(init=False, default=True)
    """
    Whether the untouched objects stayed where they were on every state update.
    """

    holding: Optional[Body] = field(init=False, default=None)
    """
    The vessel the gripper carried on the previous state update, if any.
    """

    let_go: Optional[Body] = field(init=False, default=None)
    """
    The vessel the gripper let go of on the previous state update, measured on this
    one: the update that moves it to the world comes before its pose is final.
    """

    released: List[Tuple[Body, float]] = field(init=False, default_factory=list)
    """
    Every vessel the gripper let go of, in order, with its distance from the target of
    the placing it belongs to when it was let go, in metres.
    """

    def __post_init__(self) -> None:
        self.starts = [body.global_pose.to_np().copy() for body in self.untouched]
        self.carried_ticks = {vessel: 0 for vessel in self.pads}
        super().__post_init__()

    def carried(self, vessel: Body) -> bool:
        """
        :param vessel: A handled vessel.
        :return: Whether it hangs from the left gripper's tool frame.
        """
        return (
            vessel.parent_connection.parent
            is self.context.robot.left_arm.end_effector.tool_frame
        )

    def on_state_change(self, **kwargs) -> None:
        """
        Measure the world after the controller updates its state.
        """
        if self.let_go is not None:
            self.record_release(self.let_go)
            self.let_go = None
        carried = next((vessel for vessel in self.pads if self.carried(vessel)), None)
        if self.holding is not None and carried is not self.holding:
            self.let_go = self.holding
        self.holding = carried
        for vessel, pads in self.pads.items():
            if not self.carried(vessel):
                continue
            self.carried_ticks[vessel] += 1
            clearance = pads.glass_clearances().smallest
            self.smallest_pad_clearance = (
                clearance
                if self.smallest_pad_clearance is None
                else min(self.smallest_pad_clearance, clearance)
            )
        self.widest_door = max(self.widest_door, float(self.door.position))
        self.widest_lid = max(self.widest_lid, float(self.lid.position))
        self.untouched_still = self.untouched_still and self.still()
        if not hasattr(self.context.world.collision_manager, "collision_matrix"):
            return
        contacts = self.context.world.collision_manager.compute_collisions().contacts
        if contacts:
            distance = min(float(contact.distance) for contact in contacts)
            self.smallest_collision_distance = (
                distance
                if self.smallest_collision_distance is None
                else min(self.smallest_collision_distance, distance)
            )

    def record_release(self, vessel: Body) -> None:
        """
        :param vessel: The vessel the gripper just let go of.
        """
        placing = self.placings[min(len(self.released), len(self.placings) - 1)]
        self.released.append((vessel, placing.error(self.context.world)))

    def still(self) -> bool:
        """
        :return: Whether every untouched object is where it started.
        """
        return all(
            np.allclose(body.global_pose.to_np(), start, atol=POSE_TOLERANCE, rtol=0)
            for body, start in zip(self.untouched, self.starts)
        )


# %% demo


@dataclass
class LaboratoryWorkflowDemo(VesselTaskDemo):
    """
    A PR2 re-sorts the rack, stores a vessel in the refrigerator and loads a balanced
    pair into the centrifuge, opening and closing the refrigerator's door and the
    centrifuge's lid, with its left arm.
    """

    moves: Tuple[VesselMove, ...] = (
        VesselMove("A1", Slot(Station.RACK, "A3")),
        VesselMove("B1", Slot(Station.RACK, "A1")),
        VesselMove("A2", Slot(Station.RACK, "B1")),
        VesselMove("A1", Slot(Station.REFRIGERATOR, "R6")),
        VesselMove("A3", Slot(Station.CENTRIFUGE, "C4")),
        VesselMove("B1", Slot(Station.CENTRIFUGE, "C2")),
    )
    """
    The vessels moved, in order: within the rack first, then into the refrigerator, then
    into the centrifuge.

    A vessel stored in the refrigerator is taken from the rack's front row, which the
    level grip reaches; a vessel taken from above has no neighbour beside it, across
    which the fingers open.
    """

    loading_tilt: float = math.radians(30)
    """
    How far the tool leans forwards about the fingers' closing direction while it takes
    a vessel for the centrifuge from above, in radians: so the wrist bends less and the
    vessel's bottom reaches over the centrifuge's rim.
    """

    loading_grasp_height: float = 0.135
    """
    How high above the vessel's bottom the tool takes a vessel for the centrifuge, in
    metres: the fingers stay above the rotor holder's walls.
    """

    loading_lowering: float = 0.18
    """
    How far above its holder a vessel is lowered into the centrifuge from, in metres:

    its bottom clear of the centrifuge's rim.
    """

    storing_grasp_height: float = 0.125
    """
    How high above the vessel's bottom the level tool takes a vessel for the
    refrigerator, in metres: the wrist stays clear of the rack below it.
    """

    storing_approach_margin: float = 0.048
    """
    How far beyond a vessel's radius the level approach to it starts, in metres: as far
    back as the arm reaches with its fingers level above the rack's front row.
    """

    centring_tolerance: float = 0.0001
    """
    How far the tool may stop from the grasp before the fingers close on a vessel for
    the centrifuge or the refrigerator, in metres: well within the pads' clearance, as
    the coupled fingers close about the tool.
    """

    storing_lowering: float = 0.045
    """
    How far above its holder a vessel is pushed into the refrigerator and lowered from,
    in metres: its bottom just clear of the holder's collar.
    """

    level_posture: Dict[str, float] = field(
        default_factory=lambda: {
            "l_shoulder_pan_joint": 0.6621,
            "l_shoulder_lift_joint": -0.0193,
            "l_upper_arm_roll_joint": 1.5708,
            "l_elbow_flex_joint": -2.0445,
            "l_forearm_roll_joint": 3.1261,
            "l_wrist_flex_joint": -1.3824,
            "l_wrist_roll_joint": -1.5798,
        }
    )
    """
    The left arm's joint positions with level fingers held out in front of its shoulder
    at the rack's height, elbow raised, from which the arm reaches into the rack's front
    row with level fingers.
    """

    door_handle_orientation: Quaternion = field(
        default_factory=lambda: Quaternion.from_rpy(0, 0, math.pi / 2)
    )
    """
    The tool's orientation at the refrigerator door's upright handle bar: level,
    pointing towards the door, the fingers closing across the bar.
    """

    lid_handle_orientation: Quaternion = field(
        default_factory=lambda: Quaternion.from_rpy(math.pi / 2, 0, math.pi / 2)
    )
    """
    The tool's orientation at the lid's handle bar: level, towards the lid's hinge, the
    fingers closing above and below the bar.
    """

    lid_approach_orientation: Quaternion = field(
        default_factory=lambda: Quaternion.from_rpy(math.pi, -math.pi / 2, 0)
    )
    """
    The closed gripper's orientation on its way under the lid, relative to the lid,
    pointing straight into the lid's underside with the fingers across its width.
    """

    lid_push_orientation: Quaternion = field(
        default_factory=lambda: Quaternion.from_rpy(
            math.pi / 2, -math.radians(75), math.pi / 2
        )
    )
    """
    The closed gripper's orientation while it moves the lid, relative to the lid: tilted
    15 degrees from :attr:`lid_approach_orientation` towards the lid's hinge, so the arm
    stays clear of the housing in front of the lid.
    """

    lid_contact: Vector3 = field(default_factory=lambda: Vector3(0.0, -0.29, -0.012))
    """
    Where the closed gripper touches the lid from below, in the lid's frame.
    """

    lid_contact_approach: Vector3 = field(
        default_factory=lambda: Vector3(0.0, -0.36, -0.07)
    )
    """
    Where the gripper comes from to touch the lid, in front of and below its edge, in
    the lid's frame.
    """

    door_open: float = 1.45
    """
    How far the refrigerator's door is opened, in radians: beside the refrigerator, as
    far as the left arm follows the handle from :data:`DOOR_STAND`.
    """

    lid_pulled: float = 0.6
    """
    How far the lid is pulled open by its handle, and lowered back to before it is
    closed by its handle, in radians.
    """

    lid_pushed: float = 1.3
    """
    How far the lid is pushed open from below, clearing the rotor's holders, in radians.
    """

    handle_clearance: float = 0.06
    """
    How far in front of a handle the approach to it starts, in metres.
    """

    back_off: float = 0.07
    """
    How far the gripper backs away from a handle or the lid it let go of, in metres.
    """

    arm_clearance: float = 0.02
    """
    How far the left arm, apart from its gripper, keeps from the centrifuge it works at,
    in metres.
    """

    refrigerator_arm_clearance: float = 0.01
    """
    How far the left arm, apart from its gripper, keeps from the refrigerator it works
    at, in metres: close enough to set a vessel into the upper compartment.
    """

    observation: Optional[WorkflowObservation] = field(init=False, default=None)
    """
    The measurements of the current run, once its plan is built.
    """

    # %% vessels

    def moved_vessels(self) -> List[str]:
        """
        :return: The vessel each move carries, in the order of :attr:`moves`: whatever
            stands in its source slot once the moves before it are done.
        """
        occupied = {
            placement.slot: placement.key for placement in self.laboratory.bench.tubes
        }
        keys = []
        for move in self.moves:
            key = occupied.pop(move.source)
            if move.target.station is Station.RACK:
                occupied[move.target.identifier] = key
            keys.append(key)
        return keys

    def handled(self) -> List[str]:
        """
        :return: The vessels the workflow picks up at least once.
        """
        return list(dict.fromkeys(self.moved_vessels()))

    def moves_to(self, station: Station) -> List[Tuple[VesselMove, str]]:
        """
        :param station: What holds the vessels at their targets.
        :return: The moves into the station, in order, each with the vessel it carries.
        """
        return [
            (move, key)
            for move, key in zip(self.moves, self.moved_vessels())
            if move.target.station is station
        ]

    def stands(self, context: TaskContext) -> List[Stand]:
        """
        :param context: The world and the robot.
        :return: Where the PR2 stands at its stations.
        """
        stands = [
            RACK_STAND,
            CENTRIFUGE_STAND,
            CENTRIFUGE_STEP_BACK,
            LOADING_STAND,
            DOOR_STAND,
        ]
        for move, _ in self.moves_to(Station.REFRIGERATOR):
            stands += self.storing_stands(context, move).route()
        return stands

    @staticmethod
    def shoulder_offset(context: TaskContext) -> float:
        """
        :param context: The world and the robot.
        :return: How far the left shoulder sits left of the base's centre, in metres.
        """
        shoulder = context.robot.left_arm.active_connections[0].child
        return float(
            context.world.compute_forward_kinematics_np(context.robot.root, shoulder)[
                1, 3
            ]
        )

    def storing_stands(self, context: TaskContext, move: VesselMove) -> StoringStands:
        """
        :param context: The world and the robot.
        :param move: A move into the refrigerator.
        :return: Where the PR2 stands for it: the vessel stays as far ahead of the base
            as it was taken.
        """
        world = context.world
        shoulder = self.shoulder_offset(context)
        source = world.transform(
            self.laboratory.slot_pose(move.source, world).homogeneous_matrix, world.root
        ).to_np()[:3, 3]
        holder = world.transform(
            move.target.pose(self.laboratory, world).homogeneous_matrix, world.root
        ).to_np()[:3, 3]
        reach = source[1] - RACK_STAND.y
        pick = Stand(float(source[0]) + shoulder, RACK_STAND.y)
        return StoringStands(
            pick=pick,
            back=Stand(pick.x, STORING_DEPTH),
            front=Stand(float(holder[0]) + shoulder, STORING_DEPTH),
            inside=Stand(float(holder[0]) + shoulder, float(holder[1]) - reach),
        )

    # %% grips

    @property
    def loading_grip(self) -> VesselGrip:
        """
        Taking a vessel for the centrifuge from above, the fingers closing across the
        bench and the tool leaning forwards by :attr:`loading_tilt`, and lifting it
        upright.
        """
        top = RotationMatrix.from_rpy(0, math.pi / 2, -math.pi / 2)
        return VesselGrip(
            orientation=(
                RotationMatrix.from_rpy(self.loading_tilt, 0, 0) @ top
            ).quaternion,
            height=self.loading_grasp_height,
            approach_clearance=self.approach_clearance,
            grasp_tolerance=self.centring_tolerance,
            lift=self.lift,
            lowering=self.loading_lowering,
            upright=True,
        )

    @property
    def storing_grip(self) -> VesselGrip:
        """
        Taking a vessel for the refrigerator from the front with level fingers closing
        across the bench, and lifting it upright.
        """
        return VesselGrip(
            orientation=Quaternion.from_rpy(math.pi, 0, math.pi / 2),
            height=self.storing_grasp_height,
            approach_clearance=self.laboratory.vessels.outer_radius
            + self.storing_approach_margin,
            grasp_tolerance=self.centring_tolerance,
            lift=self.lift,
            lowering=self.storing_lowering,
            upright=True,
        )

    # %% context

    def build_context(self, world: World) -> TaskContext:
        """
        Park the arms, raise the torso, and keep the gripper and the handled vessels
        clear of the laboratory, except for the intended contacts of the grasps.

        :param world: The world built for the task.
        :return: The context with tight tolerances at the slots.
        """
        robot = self.posture(world)
        self.keep_clear(
            world, robot, [world.get_body_by_name(key) for key in self.handled()]
        )
        self.reach_into_instruments(world, robot)
        return self.tight_context(world, robot)

    def reach_into_instruments(self, world: World, robot: PR2) -> None:
        """
        Let the left arm come within :attr:`arm_clearance` of the centrifuge, so it
        reaches under the centrifuge's lid, and within
        :attr:`refrigerator_arm_clearance` of the refrigerator, so it sets a vessel into
        the upper compartment.

        :param world: The world built for the task.
        :param robot: The robot.
        """
        arm = robot.left_arm
        bench = self.laboratory.bench
        links = [
            body
            for body in arm.bodies_with_collision
            if body not in arm.end_effector.bodies_with_collision
        ]
        for names, clearance in (
            (
                (
                    bench.centrifuge.body,
                    bench.centrifuge.lid_body,
                    bench.centrifuge.rotor_body,
                ),
                self.arm_clearance,
            ),
            (
                (bench.refrigerator.body, bench.refrigerator.door_body),
                self.refrigerator_arm_clearance,
            ),
        ):
            with world.modify_world():
                world.collision_manager.add_default_rule(
                    AvoidCollisionBetweenGroups(
                        body_group_a=links,
                        body_group_b=[world.get_body_by_name(name) for name in names],
                        buffer_zone_distance=clearance,
                        violated_distance=0.0,
                    )
                )

    # %% plan

    def build_plan(self, context: TaskContext) -> StatechartNode:
        """
        :param context: The world and the robot.
        :return: The rack moves, the refrigerator and the centrifuge, in order.
        """
        world = context.world
        bench = self.laboratory.bench
        handled = [world.get_body_by_name(key) for key in self.handled()]
        untouched = [
            world.get_body_by_name(placement.key)
            for placement in bench.tubes
            if placement.key not in self.handled()
        ]
        if bench.stopper is not None:
            untouched.append(world.get_body_by_name(bench.stopper.key))
        plan = Sequence(
            nodes=[
                *self.rack_stage(context),
                *self.refrigerator_stage(context),
                *self.centrifuge_stage(context),
                self.drive(context, RACK_STAND),
            ]
        )
        if self.observation is not None:
            self.observation.stop()
        self.observation = WorkflowObservation(
            _world=world,
            context=context,
            pads={
                vessel: PadContact(
                    world,
                    context.robot.left_arm.end_effector,
                    vessel,
                    clearance=self.pad_clearance,
                )
                for vessel in handled
            },
            placings=self.placings(world),
            untouched=untouched,
            door=world.get_connection_by_name(bench.refrigerator.door_joint),
            lid=world.get_connection_by_name(bench.centrifuge.lid_joint),
        )
        self.plan = plan
        return plan

    def rack_stage(self, context: TaskContext) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :return: Every move within the rack, picked from and set into the rack.
        """
        steps = []
        for move, key in self.moves_to(Station.RACK):
            grasp = self.grasp(context, context.world.get_body_by_name(key))
            steps += [
                self.pick_up(context, grasp),
                self.place(grasp, move.target.pose(self.laboratory, context.world)),
            ]
        return steps

    def refrigerator_stage(self, context: TaskContext) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :return: Open the refrigerator's door by its handle, store the vessels, and close
            the door again, then tuck the arm in and raise the torso, so the centrifuge
            starts from the same posture as the PR2 did.
        """
        handle = self.handle(context.world, LaboratoryHandle.REFRIGERATOR_DOOR)
        door_grasp = self.grasp_at(
            context,
            handle,
            Pose.from_xyz_quaternion(
                0,
                0,
                0,
                *self.door_handle_orientation.to_np(),
                reference_frame=handle.root,
            ),
        )
        steps = [
            self.park(context),
            self.drive(context, DOOR_STAND),
            *self.operate(context, door_grasp, self.door_open),
            self.park(context, raise_torso=True),
        ]
        for move, key in self.moves_to(Station.REFRIGERATOR):
            steps += self.store(context, move, context.world.get_body_by_name(key))
        return [
            *steps,
            self.drive(context, DOOR_STAND),
            *self.operate(context, door_grasp, 0.0),
            self.park(context, raise_torso=True),
        ]

    def store(
        self, context: TaskContext, move: VesselMove, vessel: Body
    ) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :param move: A move into the refrigerator.
        :param vessel: The vessel it carries.
        :return: Take the vessel from the front with level fingers, back away from the
            bench, drive to the refrigerator, lower the vessel in front of its holder,
            drive it in above the holder and set it into the holder, then drive back.
        """
        world = context.world
        grip = self.storing_grip
        grasp = self.grasp(context, vessel, grip)
        stands = self.storing_stands(context, move)
        holder = world.transform(
            move.target.pose(self.laboratory, world).homogeneous_matrix, world.root
        ).to_np()[:3, 3]
        source = world.transform(
            self.laboratory.slot_pose(move.source, world).homogeneous_matrix, world.root
        ).to_np()[:3, 3]
        in_front = Pose.from_xyz_quaternion(
            holder[0],
            stands.front.y + source[1] - RACK_STAND.y,
            holder[2] + grip.lowering + grip.height,
            *grip.orientation.to_np(),
            reference_frame=world.root,
        )
        torso = context.robot.torso.active_connections[0]
        return [
            self.park(context),
            self.drive(context, stands.pick),
            HeldBaseJointsAction(
                names=[
                    world.get_connection_by_name(name).name.name
                    for name in self.level_posture
                ],
                positions=list(self.level_posture.values()),
            ),
            self.pick_up(context, grasp, grip),
            self.drive(context, stands.back),
            self.drive(context, stands.front),
            HeldBaseJointsAction(
                names=[torso.name.name], positions=[torso.dof.limits.lower.position]
            ),
            self.move_tool(context, in_front),
            self.drive(context, stands.inside),
            self.place(grasp, move.target.pose(self.laboratory, world), grip),
            self.drive(context, stands.front),
            self.park(context, raise_torso=True),
        ]

    def load(
        self, context: TaskContext, move: VesselMove, vessel: Body
    ) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :param move: A move into the centrifuge.
        :param vessel: The vessel it carries.
        :return: Take the vessel from above, leaning forwards, carry it to the
            centrifuge and lower it into its holder.
        """
        grip = self.loading_grip
        grasp = self.grasp(context, vessel, grip)
        return [
            self.park(context),
            self.drive(context, RACK_STAND),
            self.pick_up(context, grasp, grip),
            self.drive(context, LOADING_STAND),
            self.place(grasp, move.target.pose(self.laboratory, context.world), grip),
        ]

    def centrifuge_stage(self, context: TaskContext) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :return: Open the lid, by its handle and then from below, load the vessels, and
            close the lid again the same way; before it takes the handle again, the PR2
            steps back to raise its torso, so it closes the lid from the posture it
            opened it from.
        """
        world = context.world
        handle = self.handle(world, LaboratoryHandle.CENTRIFUGE_LID)
        lid_grasp = self.grasp_at(
            context,
            handle,
            Pose.from_xyz_quaternion(
                0,
                0,
                0,
                *self.lid_handle_orientation.to_np(),
                reference_frame=handle.root,
            ),
        )
        lid = world.get_body_by_name(self.laboratory.bench.centrifuge.lid_body)
        loading = []
        for move, key in self.moves_to(Station.CENTRIFUGE):
            loading += self.load(context, move, world.get_body_by_name(key))
        return [
            self.drive(context, CENTRIFUGE_STAND),
            *self.operate(context, lid_grasp, self.lid_pulled),
            *self.support_lid(context, lid, self.lid_pushed),
            *loading,
            self.park(context),
            self.drive(context, CENTRIFUGE_STAND),
            *self.support_lid(context, lid, self.lid_pulled),
            self.drive(context, CENTRIFUGE_STEP_BACK),
            self.park(context, raise_torso=True),
            self.drive(context, CENTRIFUGE_STAND),
            *self.operate(context, lid_grasp, 0.0),
            self.park(context),
        ]

    # %% steps

    @staticmethod
    def handle(world: World, name: LaboratoryHandle) -> Handle:
        """
        :param world: A world holding the laboratory.
        :param name: One of the laboratory's handles.
        :return: The handle's annotation.
        """
        return next(
            handle
            for handle in world.get_semantic_annotations_by_type(Handle)
            if handle.root.name.name == name.value
        )

    def drive(self, context: TaskContext, stand: Stand) -> DriveAction:
        """
        :param context: The world and the robot.
        :param stand: Where the PR2 drives to.
        :return: The drive.
        """
        return DriveAction(target=stand.pose(context.world))

    @staticmethod
    def park(context: TaskContext, raise_torso: bool = False) -> TuckArmsAction:
        """
        :param context: The world and the robot.
        :param raise_torso: Whether the torso is raised to the top of its travel as well.
        :return: Tucking the left arm in, for driving or out of the way.
        """
        return TuckArmsAction(arms=[context.robot.left_arm], raise_torso=raise_torso)

    def operate(
        self, context: TaskContext, grasp: GraspCandidate, position: float
    ) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :param grasp: The left gripper's grasp on a handle.
        :param position: Where the handle's joint ends.
        :return: Take the handle, move its joint, let go and back away.
        """
        arm = context.robot.left_arm
        return [
            GraspingAction(
                grasp=grasp, arm=arm, approach_clearance=self.handle_clearance
            ),
            MechanismAction(part=grasp.graspable.root, arm=arm, position=position),
            *self.let_go(context),
        ]

    def support_lid(
        self, context: TaskContext, lid: Body, position: float
    ) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :param lid: The centrifuge's lid.
        :param position: Where the lid's hinge ends.
        :return: Tuck the arm in, put the closed gripper under the lid's front edge from
            below, move the lid on it, back away and tuck the arm in again.
        """
        arm = context.robot.left_arm
        gripper = arm.end_effector

        def under_the_lid(point: Vector3, orientation: Quaternion) -> Pose:
            return Pose.from_xyz_quaternion(
                point.x, point.y, point.z, *orientation.to_np(), reference_frame=lid
            )

        return [
            self.park(context),
            MoveGripperAction(gripper=gripper, state=GripperState.CLOSE),
            self.move_tool(
                context,
                under_the_lid(self.lid_contact_approach, self.lid_approach_orientation),
            ),
            self.move_tool(
                context,
                under_the_lid(self.lid_contact, self.lid_push_orientation),
                touching=True,
            ),
            MechanismAction(part=lid, arm=arm, position=position),
            self.move_tool(
                context,
                Pose.from_xyz_rpy(-self.back_off, reference_frame=gripper.tool_frame),
                touching=True,
                precise=False,
            ),
            self.park(context),
        ]

    def let_go(self, context: TaskContext) -> List[StatechartNode]:
        """
        :param context: The world and the robot.
        :return: Open the gripper and back away from what it held.
        """
        gripper = context.robot.left_arm.end_effector
        return [
            MoveGripperAction(gripper=gripper, state=GripperState.OPEN),
            self.move_tool(
                context,
                Pose.from_xyz_rpy(-self.back_off, reference_frame=gripper.tool_frame),
                touching=True,
                precise=False,
            ),
        ]

    def move_tool(
        self,
        context: TaskContext,
        target: Pose,
        touching: bool = False,
        precise: bool = True,
    ) -> MoveManipulatorAction:
        """
        :param context: The world and the robot.
        :param target: Where the left gripper's tool frame ends.
        :param touching: Whether the gripper may touch its surroundings on the way.
        :param precise: Whether the tool ends at the tight tolerances of a slot, or at
            CRAM's own, as backing away from something does.
        :return: Moving the tool there with the base held.
        """
        return MoveManipulatorAction(
            target_pose=target,
            end_effector=context.robot.left_arm.end_effector,
            allow_gripper_collision=touching,
            **(self.tool_tolerances() if precise else {}),
        )

    # %% running

    def run(self) -> SavedScene:
        """
        :return: The measured recording; release observation on failure as well as
            success.
        """
        try:
            return super().run()
        finally:
            if self.observation is not None:
                self.observation.stop()

    def placings(self, world: World) -> List[VesselPlacing]:
        """
        :param world: A world holding the laboratory.
        :return: Where the workflow sets every vessel down, in order.
        """
        return [
            VesselPlacing(
                world.get_body_by_name(key),
                move.target.pose(self.laboratory, world),
                move.target.station,
            )
            for move, key in zip(self.moves, self.moved_vessels())
        ]

    def measure(self, context: TaskContext) -> AcceptanceMetrics:
        """
        :param context: The world and the robot after the run.
        :return: The measurements.
        :raises TaskFailed: When a vessel was not carried, released and set down where
            it belongs, the door or the lid was not opened and closed again, or anything
            else moved.
        """
        world = context.world
        observation = self.observation
        self.require_success(context)
        for vessel, ticks in observation.carried_ticks.items():
            if ticks == 0:
                raise TaskFailed(
                    WorkflowFailure.NOT_CARRIED.value.format(vessel=vessel.name.name)
                )
            if vessel.parent_connection.parent is not world.root:
                raise TaskFailed(
                    WorkflowFailure.NOT_RELEASED.value.format(vessel=vessel.name.name)
                )
        placings = self.placings(world)
        if [vessel for vessel, _ in observation.released] != [
            placing.vessel for placing in placings
        ]:
            raise TaskFailed(
                WorkflowFailure.NOT_SET_DOWN.value.format(
                    released=[vessel.name.name for vessel, _ in observation.released]
                )
            )
        largest_errors = {placing.station.position_error: 0.0 for placing in placings}
        final = {placing.vessel: placing for placing in placings}
        for placing, (vessel, released) in zip(placings, observation.released):
            errors = (
                (released, placing.error(world))
                if final[vessel] is placing
                else (released,)
            )
            for error in errors:
                if error > 2 * self.position_tolerance:
                    raise TaskFailed(
                        WorkflowFailure.MISPLACED.value.format(
                            vessel=vessel.name.name, error=error
                        )
                    )
            measurement = placing.station.position_error
            largest_errors[measurement] = max(largest_errors[measurement], released)
        bench = self.laboratory.bench
        for part, widest, goal, joint in (
            (
                bench.refrigerator.door_body,
                observation.widest_door,
                self.door_open,
                observation.door,
            ),
            (
                bench.centrifuge.lid_body,
                observation.widest_lid,
                self.lid_pushed,
                observation.lid,
            ),
        ):
            if widest < goal - OPENED_TOLERANCE:
                raise TaskFailed(
                    WorkflowFailure.NOT_OPENED.value.format(part=part, angle=widest)
                )
            if abs(float(joint.position)) > CLOSED_TOLERANCE:
                raise TaskFailed(
                    WorkflowFailure.LEFT_OPEN.value.format(
                        part=part, angle=float(joint.position)
                    )
                )
        if not (observation.untouched_still and observation.still()):
            raise TaskFailed(WorkflowFailure.DISTURBED.value)
        if (observation.smallest_pad_clearance or 0.0) < -PENETRATION_TOLERANCE:
            raise TaskFailed(
                WorkflowFailure.PADS.value.format(
                    depth=-observation.smallest_pad_clearance
                )
            )
        if (observation.smallest_collision_distance or 0.0) < -self.pad_clearance:
            raise TaskFailed(
                WorkflowFailure.COLLISION.value.format(
                    distance=-observation.smallest_collision_distance
                )
            )
        return AcceptanceMetrics(
            measurements={
                **{
                    measurement.value: error
                    for measurement, error in largest_errors.items()
                },
                WorkflowMeasurement.WIDEST_DOOR.value: observation.widest_door,
                WorkflowMeasurement.WIDEST_LID.value: observation.widest_lid,
                WorkflowMeasurement.DOOR.value: float(observation.door.position),
                WorkflowMeasurement.LID.value: float(observation.lid.position),
                WorkflowMeasurement.SMALLEST_PAD_CLEARANCE.value: observation.smallest_pad_clearance,
                WorkflowMeasurement.SMALLEST_COLLISION_DISTANCE.value: observation.smallest_collision_distance,
            }
        )

    def require_success(self, context: TaskContext) -> None:
        """
        Check every action of the plan.

        :param context: The world and the robot after the run.
        :raises TaskFailed: When the plan holds no action or an action did not succeed.
        """
        statuses = [action.life_cycle_state for action in self.performed_actions()]
        if not statuses or any(
            status is not LifeCycleValues.SUCCEEDED for status in statuses
        ):
            raise TaskFailed(
                WorkflowFailure.MOTIONS.value.format(
                    statuses=sorted({status.name for status in statuses})
                )
            )

    @classmethod
    def at_the_bench(cls, directory: Path, **fields) -> LaboratoryWorkflowDemo:
        """
        :param directory: The laboratory bundle supplied by the caller.
        :param fields: Fields that differ from the shipped bench's workflow.
        :return: The workflow in the shipped bench laboratory.
        """
        environment = LaboratoryEnvironment(directory, allow_support_contacts=True)
        defaults = dict(
            environment=environment,
            robot=RobotSpecification(
                semantic_annotation_type=PR2,
                odometry_T_robot_start=HomogeneousTransformationMatrix.from_xyz_rpy(
                    x=RACK_STAND.x, y=RACK_STAND.y, yaw=RACK_STAND.heading
                ),
            ),
            name=TaskRecording.WORKFLOW.value,
            task=TASK,
            environment_name=environment.contents.scene.environment_name,
            presentation=ScenePresentation.of_bundle(environment.directory),
            camera=WORKFLOW_CAMERA,
            collision_avoidance=True,
        )
        defaults.update(fields)
        return cls(**defaults)
