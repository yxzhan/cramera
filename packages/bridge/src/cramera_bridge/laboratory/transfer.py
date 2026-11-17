"""
A PR2 moves a vessel from one rack slot to another with CRAM's pickup and placing, in
the laboratory as a CRAM world, while the run is published, recorded and measured.

The measured outcome: both actions succeed, the vessel was carried and lifted clear of
the rack, it was released into the target slot within a millimetre, the other vessels
and the stopper did not move, and the gripper's pads never cut into the glass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from typing_extensions import Dict, List, Optional

from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from coraplex.robot_plans.actions.core.placing import PlaceAction
from cramph.composites import Sequence
from cramph.data_types import LifeCycleValues
from cramph.node import StatechartNode
from krrood.entity_query_language.factories import an, entity, the, variable
from semantic_digital_twin.callbacks.callback import StateChangeCallback
from semantic_digital_twin.specifications.robots import RobotSpecification
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.semantic_annotations.semantic_annotations import Handle
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

from cramera_bridge.exceptions import TaskFailed
from cramera_bridge.scenes.presentation import ScenePresentation
from cramera_bridge.laboratory.grasping import PadContact
from cramera_bridge.laboratory.handling import VesselTaskDemo
from cramera_protocol.laboratory.runs import TaskRecording
from cramera_bridge.laboratory.world import LaboratoryEnvironment
from cramera_bridge.performing import TaskContext
from cramera_protocol.bundle.acceptance import AcceptanceMetrics
from cramera_protocol.bundle.scene import Camera
from cramera_protocol.geometry import Vector3
from cramera_protocol.live_messages import SavedScene

TASK = "PR2: vessel transfer A1 → A3"
"""
What the task does, as the scene picker labels it.
"""

ROBOT_START = (0.40, -0.72, math.pi / 2)
"""
Where the PR2 starts: x and y in metres and its heading, facing the bench.
"""

TRANSFER_CAMERA = Camera(
    position=Vector3(3.5, -1.8, 2.8), target=Vector3(0.2, -0.3, 1.1)
)
"""
The recording's first view: the whole PR2 and the small workbench from the side.
"""

POSE_TOLERANCE = 1e-10
"""
How far an untouched object may have moved, in metres.
"""

PENETRATION_TOLERANCE = 0.000001
"""
How far a pad may numerically reach into the glass, in metres.
"""

SINGLE_ACTION = 1
"""
How many nodes of each action the plan holds.
"""

# %% names


class TransferMeasurement(StrEnum):
    """
    What the transfer demo measures.
    """

    POSITION_ERROR = "positionError"
    """
    The distance between the vessel's bottom and the target slot, in metres.
    """

    ORIENTATION_ERROR = "orientationError"
    """
    The angle between the vessel and the target slot's orientation, in radians.
    """

    MAXIMUM_LIFT = "maximumLift"
    """
    How high the vessel was lifted above its slot, in metres.
    """

    CARRIED_TICKS = "carriedTicks"
    """
    How many observed state updates the gripper carried the vessel.
    """

    SMALLEST_PAD_CLEARANCE = "smallestPadClearance"
    """
    The smallest clearance between a pad and the glass while carried, in metres.
    """

    SMALLEST_COLLISION_DISTANCE = "smallestCollisionDistance"
    """
    The smallest distance between bodies whose collisions are avoided, in metres.
    """


class TransferFailure(StrEnum):
    """
    Why a transfer failed.
    """

    ACTIONS = "the pickup or the placing did not succeed: {statuses}"
    """
    An action ended in another state.
    """

    NOT_CARRIED = "the gripper never carried the vessel"
    """
    The vessel was never attached to the gripper.
    """

    NOT_LIFTED = "the vessel rose only {lift:.4f} m above its slot"
    """
    The vessel was not lifted clear of the rack.
    """

    NOT_RELEASED = "the vessel is still held"
    """
    The vessel is not attached to the world after placing.
    """

    MISPLACED = "the vessel ended {error:.4f} m from its target slot"
    """
    The vessel ended too far from the target.
    """

    TURNED = "the vessel ended turned by {error:.4f} rad"
    """
    The vessel ended turned.
    """

    DISTURBED = "another vessel or the stopper moved"
    """
    An object that was not picked moved.
    """

    PADS = "a gripper pad cut into the glass by {depth:.6f} m"
    """
    A pad intersected the glass while carrying it.
    """

    COLLISION = "bodies came {distance:.5f} m into each other"
    """
    A checked collision pair penetrated.
    """


# %% observation


@dataclass(eq=False)
class TransferObservation(StateChangeCallback):
    """
    Measures the vessel and its neighbours after each state update of the run.
    """

    context: TaskContext = field(kw_only=True)
    """
    The world and the robot.
    """

    body: Body = field(kw_only=True)
    """
    The vessel moved.
    """

    pads: PadContact = field(kw_only=True)
    """
    Measures the pads' clearance from the glass.
    """

    others: List[Body] = field(kw_only=True)
    """
    The objects that must stay where they are.
    """

    start: np.ndarray = field(init=False)
    """
    The vessel's transform before the run.
    """

    other_starts: Dict[int, np.ndarray] = field(init=False)
    """
    The other objects' transforms before the run, by their position in :attr:`others`.
    """

    carried_ticks: int = field(init=False, default=0)
    """
    How many observed state updates the gripper carried the vessel.
    """

    maximum_lift: float = field(init=False, default=0.0)
    """
    How high the vessel rose above its start, in metres.
    """

    smallest_pad_clearance: Optional[float] = field(init=False, default=None)
    """
    The smallest pad clearance while carried, in metres.
    """

    smallest_collision_distance: Optional[float] = field(init=False, default=None)
    """
    The smallest distance of a checked collision pair, in metres.
    """

    others_still: bool = field(init=False, default=True)
    """
    Whether the other objects stayed where they were on every state update.
    """

    def __post_init__(self) -> None:
        self.start = self.body.global_pose.to_np().copy()
        self.other_starts = {
            index: other.global_pose.to_np().copy()
            for index, other in enumerate(self.others)
        }
        super().__post_init__()

    def carried(self) -> bool:
        """
        :return: Whether the vessel hangs from the left gripper's tool frame.
        """
        return (
            self.body.parent_connection.parent
            is self.context.robot.left_arm.end_effector.tool_frame
        )

    def on_state_change(self, **kwargs) -> None:
        """
        Measure the world after the controller updates its state.
        """
        if self.carried():
            self.carried_ticks += 1
            lift = float(self.body.global_pose.to_np()[2, 3]) - self.start[2, 3]
            self.maximum_lift = max(self.maximum_lift, lift)
            clearance = self.pads.clearances().smallest
            self.smallest_pad_clearance = (
                clearance
                if self.smallest_pad_clearance is None
                else min(self.smallest_pad_clearance, clearance)
            )
        self.others_still = self.others_still and self.untouched()
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

    def untouched(self) -> bool:
        """
        :return: Whether every other object is where it started.
        """
        return all(
            np.allclose(
                other.global_pose.to_np(),
                self.other_starts[index],
                atol=POSE_TOLERANCE,
                rtol=0,
            )
            for index, other in enumerate(self.others)
        )


# %% demo


@dataclass
class VesselTransferDemo(VesselTaskDemo):
    """
    A PR2 at the bench moves a vessel between two rack slots with its left arm.
    """

    source_slot: str = "A1"
    """
    The slot the vessel starts in.
    """

    target_slot: str = "A3"
    """
    The slot the vessel is placed in.
    """

    observation: Optional[TransferObservation] = field(init=False, default=None)
    """
    The measurements of the current run, once its plan is built.
    """

    handles: List[Handle] = field(init=False, default_factory=list)
    """
    The handles at the bench, once a query found them.
    """

    vessel: Optional[Body] = field(init=False, default=None)
    """
    The vessel the run carries, once a query found it.
    """

    def the_handles(self, world: World) -> List[Handle]:
        """
        :param world: The world built for the task.
        :return: Every handle at the bench, found by an entity query; the transfer
            opens none of them, so their answers only show where they are.
        """
        handle = variable(Handle, world.semantic_annotations)
        return an(entity(handle)).tolist()

    def the_vessel(self, world: World) -> Body:
        """
        :param world: The world built for the task.
        :return: The vessel standing in the source slot, found by an entity query.
        """
        key = self.vessel_key()
        body = variable(Body, world.bodies)
        return the(entity(body).where(body.name.name == key)).first()

    def vessel_key(self) -> str:
        """
        :return: The vessel starting in the source slot.
        """
        return self.vessel_in(self.source_slot)

    def build_context(self, world: World) -> TaskContext:
        """
        Find the robot, the handles and the vessel with entity queries, park the arms,
        raise the torso, and keep the gripper and the vessel clear of the laboratory,
        except for the intended contacts of the grasp.

        This arm-only task excludes the drive from the controller, so collision
        avoidance cannot move the base between approach, closing and attachment.

        :param world: The world built for the task.
        :return: The context with tight tolerances at the rack.
        """
        robot = self.posture(world)
        self.handles = self.the_handles(world)
        self.vessel = self.the_vessel(world)
        self.keep_clear(world, robot, [self.vessel])
        return self.tight_context(world, robot)

    def build_plan(self, context: TaskContext) -> StatechartNode:
        """
        :param context: The world and the robot.
        :return: Pickup from the source slot and placing into the target slot.
        """
        key = self.vessel_key()
        body = context.world.get_body_by_name(key)
        bench = self.laboratory.bench
        grasp = self.grasp(context, body)
        others = [
            context.world.get_body_by_name(placement.key)
            for placement in bench.tubes
            if placement.key != key
        ]
        if bench.stopper is not None:
            others.append(context.world.get_body_by_name(bench.stopper.key))
        plan = Sequence(
            nodes=[
                self.pick_up(context, grasp),
                self.place(
                    grasp, self.laboratory.slot_pose(self.target_slot, context.world)
                ),
            ]
        )
        if self.observation is not None:
            self.observation.stop()
        self.observation = TransferObservation(
            _world=context.world,
            context=context,
            body=body,
            pads=PadContact(
                context.world,
                context.robot.left_arm.end_effector,
                body,
                clearance=self.pad_clearance,
            ),
            others=others,
        )
        self.plan = plan
        return plan

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

    def measure(self, context: TaskContext) -> AcceptanceMetrics:
        """
        :param context: The world and the robot after the run.
        :return: The measurements.
        :raises TaskFailed: When the vessel was not carried, lifted, released and placed
            as required, or anything else moved.
        """
        observation = self.observation
        body = observation.body
        actual = body.global_pose.to_np()
        desired = self.laboratory.slot_pose(self.target_slot, context.world).to_np()
        position_error = float(np.linalg.norm(actual[:3, 3] - desired[:3, 3]))
        orientation_error = float(
            np.arccos(
                np.clip((np.trace(desired[:3, :3].T @ actual[:3, :3]) - 1) / 2, -1, 1)
            )
        )
        self.require_success(context)
        if observation.carried_ticks == 0:
            raise TaskFailed(TransferFailure.NOT_CARRIED.value)
        if observation.maximum_lift < self.lift - self.position_tolerance:
            raise TaskFailed(
                TransferFailure.NOT_LIFTED.value.format(lift=observation.maximum_lift)
            )
        if body.parent_connection.parent is not context.world.root:
            raise TaskFailed(TransferFailure.NOT_RELEASED.value)
        if position_error > 2 * self.position_tolerance:
            raise TaskFailed(
                TransferFailure.MISPLACED.value.format(error=position_error)
            )
        if orientation_error > 2 * self.orientation_tolerance:
            raise TaskFailed(
                TransferFailure.TURNED.value.format(error=orientation_error)
            )
        if not (observation.others_still and observation.untouched()):
            raise TaskFailed(TransferFailure.DISTURBED.value)
        if (observation.smallest_pad_clearance or 0.0) < -PENETRATION_TOLERANCE:
            raise TaskFailed(
                TransferFailure.PADS.value.format(
                    depth=-observation.smallest_pad_clearance
                )
            )
        if (observation.smallest_collision_distance or 0.0) < -self.pad_clearance:
            raise TaskFailed(
                TransferFailure.COLLISION.value.format(
                    distance=-observation.smallest_collision_distance
                )
            )
        return AcceptanceMetrics(
            measurements={
                TransferMeasurement.POSITION_ERROR.value: position_error,
                TransferMeasurement.ORIENTATION_ERROR.value: orientation_error,
                TransferMeasurement.MAXIMUM_LIFT.value: float(observation.maximum_lift),
                TransferMeasurement.CARRIED_TICKS.value: observation.carried_ticks,
                TransferMeasurement.SMALLEST_PAD_CLEARANCE.value: observation.smallest_pad_clearance,
                TransferMeasurement.SMALLEST_COLLISION_DISTANCE.value: observation.smallest_collision_distance,
            }
        )

    def require_success(self, context: TaskContext) -> None:
        """
        Check that the plan holds one pickup and one placing, both expanded, and that
        they and every action they hold succeeded.

        :param context: The world and the robot after the run.
        :raises TaskFailed: When the pickup or the placing did not succeed.
        """
        actions = self.performed_actions()
        statuses = [action.life_cycle_state for action in actions]
        pickups = [action for action in actions if isinstance(action, PickUpAction)]
        placings = [action for action in actions if isinstance(action, PlaceAction)]
        if (
            len(pickups) != SINGLE_ACTION
            or len(placings) != SINGLE_ACTION
            or any(not action.nodes for action in pickups + placings)
            or any(status is not LifeCycleValues.SUCCEEDED for status in statuses)
        ):
            raise TaskFailed(
                TransferFailure.ACTIONS.value.format(
                    statuses=[status.name for status in statuses]
                )
            )

    @classmethod
    def at_the_bench(cls, directory: Path, **fields) -> VesselTransferDemo:
        """
        :param directory: The laboratory bundle supplied by the caller.
        :param fields: Fields that differ from the shipped bench's transfer.
        :return: The transfer in the shipped bench laboratory.
        """
        environment = LaboratoryEnvironment(directory, allow_support_contacts=True)
        x, y, heading = ROBOT_START
        defaults = dict(
            environment=environment,
            robot=RobotSpecification(
                semantic_annotation_type=PR2,
                odometry_T_robot_start=HomogeneousTransformationMatrix.from_xyz_rpy(
                    x=x, y=y, yaw=heading
                ),
            ),
            name=TaskRecording.TRANSFER.value,
            task=TASK,
            environment_name=environment.contents.scene.environment_name,
            presentation=ScenePresentation.of_bundle(environment.directory),
            camera=TRANSFER_CAMERA,
            collision_avoidance=True,
        )
        defaults.update(fields)
        return cls(**defaults)
