"""Execute and record a simulated PR2 transfer between laboratory rack slots."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np

from coraplex.datastructures.dataclasses import Context, MotionToleranceConfig
from coraplex.datastructures.enums import (
    Arms,
    ApproachDirection,
    TaskStatus,
    VerticalAlignment,
)
from coraplex.datastructures.grasp import GraspDescription
from coraplex.execution_environment import simulated_robot_advanced
from coraplex.plans.factories import sequential
from coraplex.plans.plan import Plan
from coraplex.plans.plan_callbacks import PlanCallback
from coraplex.plans.plan_node import MotionNode, PlanNode
from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from coraplex.robot_plans.actions.core.placing import PlaceAction
from coraplex.robot_plans.motions.placement import MovePlacementMotion, PlacementStage
from coraplex.utils import translate_pose_along_local_axis
from cramera import paths
from cramera.generated_json import write_json_atomically
from cramera.laboratory_grasp import LaboratoryPickUpAction
from cramera.laboratory_world import LaboratoryAsset, LaboratoryWorld
from cramera.live.bridge import Bridge
from cramera.live.live_bundle import build_live_scene
from cramera.live.recording_bundle import write_recording_bundle
from cramera.live.visualization import LiveVisualization
from cramera.onboard.scene_index import write_scene_index
from cramera.pr2_gripper import Pr2GripperContact
from cramera.scene_presentation import ScenePresentation
from giskardpy.motion_statechart.goals.collision_avoidance import (
    ExternalCollisionAvoidance,
)
from giskardpy.motion_statechart.motion_statechart import MotionStatechart
from semantic_digital_twin.collision_checking.collision_rules import (
    AllowCollisionBetweenGroups,
    AvoidCollisionBetweenGroups,
)
from semantic_digital_twin.datastructures.definitions import (
    StaticJointState,
    TorsoState,
)
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.spatial_types import (
    HomogeneousTransformationMatrix,
    Pose,
    Quaternion,
)
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body


# %% tube grasp
@dataclass
class TubeGrasp(GraspDescription):
    """Place the tool above a tube's bottom frame at a selected gripping height."""

    grasp_height: float = 0.14
    """Tool height measured from the rounded tube bottom, in metres."""
    tool_orientation: Quaternion | None = None
    """Optional tool orientation relative to the upright tube."""
    approach_distance: float | None = None
    """Optional approach clearance independent of the glass extraction height."""

    def grasp_orientation(self) -> Quaternion:
        """Resolve the authored tool orientation or the ordinary side grasp."""
        return (
            self.tool_orientation
            if self.tool_orientation is not None
            else super().grasp_orientation()
        )

    def pose_sequence(
        self, target_T_grasp_pose: Pose, body: Body | None = None, reverse: bool = False
    ) -> list[Pose]:
        """Approach the tube wall and lift the tube clear of its supporting rack."""
        target_T_tool_height = (
            target_T_grasp_pose.to_homogeneous_matrix()
            @ HomogeneousTransformationMatrix.from_xyz_rpy(z=self.grasp_height)
        )
        sequence = super().pose_sequence(target_T_tool_height.to_pose(), body, False)
        if self.approach_distance is not None:
            sequence[0] = translate_pose_along_local_axis(
                sequence[1], self.manipulation_axis(), -self.approach_distance
            )
        return list(reversed(sequence)) if reverse else sequence


@dataclass
class LaboratoryPlaceAction(PlaceAction):
    """Use a free-space approach tolerance while retaining precise rack insertion."""

    approach_position_threshold: float = field(default=0.003, kw_only=True)
    """Tool tolerance above the rack; insertion retains the context's tighter value."""

    @property
    def _action_plan(self) -> PlanNode:
        """Configure the native approach motion before the plan is compiled."""
        plan = super()._action_plan
        for node in plan.descendants:
            if isinstance(node, MotionNode) and isinstance(
                node.designator, MovePlacementMotion
            ):
                if node.designator.stage is PlacementStage.APPROACH:
                    node.designator.position_threshold = (
                        self.approach_position_threshold
                    )
        return plan


# %% execution evidence
class LaboratoryRecording(StrEnum):
    """Files published after the native transfer finishes."""

    SCENE = "precision_lab_pr2"
    """Recorded PR2 laboratory scene."""
    METRICS = "acceptance_metrics.json"
    """Measured native execution outcome."""
    SCENE_FILE = "scene.json"
    """Viewer scene manifest."""
    INDEX = "index.json"
    """Local scene catalog."""


class LaboratoryTransferFailed(RuntimeError):
    """Raised when the recorded execution fails the rack placement checks."""


@dataclass(frozen=True)
class LaboratoryResult:
    """Measurements taken from the actual robot and object trajectory."""

    success: bool
    """Whether all manipulation acceptance conditions were met."""
    attached_frames: int
    """Controller ticks with the glass attached to the left tool."""
    maximum_lift: float
    """Largest glass-bottom rise above the source slot, in metres."""
    position_error: float
    """Final three-dimensional displacement from the target slot, in metres."""
    orientation_error: float
    """Final angular displacement from the target orientation, in radians."""
    minimum_collision_avoidance_goals: int
    """Fewest external collision avoidance goals in any observed controller tick."""
    minimum_collision_distance: float | None
    """Smallest checked clearance during motion, excluding intended allowed contacts."""
    released: bool
    """Whether the glass is independently attached to the world after placement."""
    other_objects_unchanged: bool
    """Whether all unselected objects retain their initial poses."""
    action_statuses: list[str]
    """Final statuses of the native pickup and placement actions."""
    minimum_finger_clearance: float | None = None
    """Smallest signed fingertip-to-glass distance while attached, in metres."""
    first_grasp_maximum_clearance: float | None = None
    """More distant fingertip's clearance at the first attached tick, in metres."""
    first_grasp_aperture: float | None = None
    """Measured pad separation at the first attached tick, in metres."""
    gripper_geometry_verified: bool = False
    """Whether both initial pad proximity and nonpenetrating carry were observed."""


@dataclass
class LaboratoryObservation(PlanCallback):
    """Observe executed motion ticks without substituting robot or object poses."""

    context: Context = field(kw_only=True)
    """World and robot whose executed states are measured."""
    body: Body = field(kw_only=True)
    """Glass selected for the transfer."""
    position_tolerance: float = field(kw_only=True)
    """Allowed final placement displacement per controller tolerance."""
    required_lift: float = field(kw_only=True)
    """Authored extraction distance before transfer."""
    gripper_contact: Pr2GripperContact | None = field(default=None, kw_only=True)
    """Optional explicit fingertip check independent of allowed grasp collisions."""
    finger_penetration_tolerance: float = field(default=0.000001, kw_only=True)
    """Numerical allowance for signed fingertip distance, in metres."""
    finger_contact_tolerance: float = field(default=0.00005, kw_only=True)
    """Additional initial contact allowance for joint convergence, in metres."""
    initial_pose: np.ndarray = field(init=False)
    """Source glass transform before execution."""
    other_poses: dict[Body, np.ndarray] = field(init=False)
    """Initial transforms of the other tubes and stopper."""
    attached_frames: int = field(init=False, default=0)
    """Number of native controller ticks with a grasp attachment."""
    maximum_lift: float = field(init=False, default=0.0)
    """Largest observed rise of the source glass."""
    avoidance_counts: list[int] = field(init=False, default_factory=list)
    """External collision avoidance goal counts during execution."""
    minimum_collision_distance: float | None = field(init=False, default=None)
    """Smallest distance in the active collision policy across all executed ticks."""
    unselected_objects_unchanged: bool = field(init=False, default=True)
    """Whether all observed ticks kept unselected objects at their initial poses."""
    minimum_finger_clearance: float | None = field(init=False, default=None)
    """Smallest directly measured fingertip clearance during attached motion."""
    first_grasp_maximum_clearance: float | None = field(init=False, default=None)
    """More distant pad's object clearance at the first attached measurement."""
    first_grasp_aperture: float | None = field(init=False, default=None)
    """Separation of the two pad surfaces at the first attached measurement."""
    gripper_measurements_finite: bool = field(init=False, default=True)
    """Whether every attached fingertip query produced finite measurements."""

    def __post_init__(self) -> None:
        """Capture the untouched scene as the acceptance reference."""
        self.initial_pose = self.body.global_pose.to_np().copy()
        self.other_poses = {
            body: body.global_pose.to_np().copy()
            for name in ("tube_amber", "tube_teal", "stopper")
            for body in self.context.world.get_bodies_by_name(name)
            if body is not self.body
        }

    def on_motion_tick(self, statechart: MotionStatechart) -> None:
        """Measure attachment and lift from the state produced by a motion tick."""
        if (
            self.body.parent_connection.parent
            is self.context.robot.left_arm.end_effector.tool_frame
        ):
            self.attached_frames += 1
            self.maximum_lift = max(
                self.maximum_lift,
                float(self.body.global_pose.z) - self.initial_pose[2, 3],
            )
            self._observe_gripper_contact()
        self.unselected_objects_unchanged = self.unselected_objects_unchanged and all(
            np.allclose(body.global_pose.to_np(), original, atol=1e-10, rtol=0)
            for body, original in self.other_poses.items()
        )
        self.avoidance_counts.append(
            len(statechart.get_nodes_by_type(ExternalCollisionAvoidance))
        )
        contacts = self.context.world.collision_manager.compute_collisions().contacts
        if contacts:
            distance = min(float(contact.distance) for contact in contacts)
            self.minimum_collision_distance = (
                distance
                if self.minimum_collision_distance is None
                else min(self.minimum_collision_distance, distance)
            )

    def _observe_gripper_contact(self) -> None:
        """Record both signed fingertip clearances during attached motion."""
        if self.gripper_contact is None:
            return
        distances = self.gripper_contact.distances()
        if not all(
            math.isfinite(value) for value in (distances.first, distances.second)
        ):
            self.gripper_measurements_finite = False
            return
        self.minimum_finger_clearance = (
            distances.minimum
            if self.minimum_finger_clearance is None
            else min(self.minimum_finger_clearance, distances.minimum)
        )
        if self.first_grasp_maximum_clearance is None:
            self.first_grasp_maximum_clearance = distances.maximum
            aperture = self.gripper_contact.aperture()
            if math.isfinite(aperture):
                self.first_grasp_aperture = aperture
            else:
                self.gripper_measurements_finite = False

    def _verify_gripper_geometry(self) -> bool:
        """Require two initially close pads and no measured finger penetration."""
        if (
            self.gripper_contact is None
            or self.minimum_finger_clearance is None
            or self.first_grasp_maximum_clearance is None
            or self.first_grasp_aperture is None
        ):
            return False
        return bool(
            self.gripper_measurements_finite
            and self.first_grasp_aperture > 0
            and self.minimum_finger_clearance >= -self.finger_penetration_tolerance
            and self.first_grasp_maximum_clearance
            <= self.gripper_contact.clearance
            + self.gripper_contact.maximum_contact_difference
            + self.finger_contact_tolerance
        )

    def validate(self, plan: Plan, target: Pose) -> LaboratoryResult:
        """Require native action completion, extraction, accurate placement and release."""
        actual = self.body.global_pose.to_np()
        desired = target.to_np()
        position_error = float(np.linalg.norm(actual[:3, 3] - desired[:3, 3]))
        orientation_error = float(
            np.arccos(
                np.clip((np.trace(desired[:3, :3].T @ actual[:3, :3]) - 1) / 2, -1, 1)
            )
        )
        nodes = plan.get_nodes_by_designator_type(
            PickUpAction
        ) + plan.get_nodes_by_designator_type(PlaceAction)
        statuses = [node.status.name for node in nodes]
        actions_succeeded = len(nodes) == 2 and all(
            node.status is TaskStatus.SUCCEEDED for node in nodes
        )
        released = self.body.parent_connection.parent is self.context.world.root
        others_unchanged = self.unselected_objects_unchanged and all(
            np.allclose(body.global_pose.to_np(), original, atol=1e-10, rtol=0)
            for body, original in self.other_poses.items()
        )
        minimum_avoidance = min(self.avoidance_counts, default=0)
        gripper_geometry_verified = self._verify_gripper_geometry()
        success = bool(
            actions_succeeded
            and released
            and others_unchanged
            and self.attached_frames > 0
            and self.maximum_lift >= self.required_lift - self.position_tolerance
            and position_error <= self.position_tolerance * 2
            and orientation_error
            <= self.context.motion_tolerances.tool_orientation_threshold * 2
            and minimum_avoidance > 0
            and (
                self.minimum_collision_distance is None
                or self.minimum_collision_distance >= -0.0001
            )
            and (self.gripper_contact is None or gripper_geometry_verified)
        )
        return LaboratoryResult(
            success,
            self.attached_frames,
            float(self.maximum_lift),
            position_error,
            orientation_error,
            minimum_avoidance,
            self.minimum_collision_distance,
            released,
            others_unchanged,
            statuses,
            self.minimum_finger_clearance,
            self.first_grasp_maximum_clearance,
            self.first_grasp_aperture,
            gripper_geometry_verified,
        )


# %% native manipulation plan
@dataclass
class LaboratoryDemo:
    """Move an authored laboratory glass with a simulated PR2 and native CRAM actions."""

    laboratory: LaboratoryWorld = field(default_factory=LaboratoryWorld)
    """Authored laboratory geometry and rack slot poses."""
    grasp_lift: float = 0.16
    """Vertical extraction distance clearing the neighbouring tube tops."""
    position_tolerance: float = 0.0005
    """Tool position tolerance for the rack's millimetre-scale clearance."""
    orientation_tolerance: float = 0.003
    """Tool orientation tolerance during insertion, in radians."""
    gripper_clearance: float = 0.003
    """Avoidance margin near laboratory fixtures, in metres."""
    finger_closing_velocity: float = 0.1
    """Maximum PR2 finger closing angular speed, in radians per second."""
    finger_position_threshold: float = 0.0001
    """Angular completion tolerance for the glass-specific finger stop, in radians."""
    finger_contact_clearance: float = 0.00025
    """Desired non-penetrating fingertip-to-glass collision clearance, in metres."""

    def build_simulated_world(self) -> World:
        """Load the PR2 and independently articulated laboratory bodies."""
        return self.laboratory.build()

    def build_context(self, world: World) -> Context:
        """Prepare the robot posture and millimetre-scale manipulation policy."""
        robot = world.get_semantic_annotations_by_type(PR2)[0]
        robot.mobile_base.full_body_controlled = False
        for arm in (robot.left_arm, robot.right_arm):
            arm.get_joint_state_by_type(StaticJointState.PARK).apply_to(world)
        robot.get_torso().get_joint_state_by_type(TorsoState.HIGH).apply_to(world)
        for connection in robot.get_torso().active_connections:
            connection.position = connection.dof.limits.upper.position
        body = world.get_body_by_name(self.laboratory.source_body_name)
        gripper = robot.left_arm.end_effector.bodies_with_collision
        obstacles = [
            item
            for item in world.bodies
            if item not in robot.bodies and item is not body and item.collision
        ]
        with world.modify_world():
            world.collision_manager.add_ignore_collision_rule(
                AllowCollisionBetweenGroups(body_group_a=gripper, body_group_b=[body])
            )
            world.collision_manager.add_default_rule(
                AvoidCollisionBetweenGroups(
                    body_group_a=gripper,
                    body_group_b=obstacles,
                    buffer_zone_distance=self.gripper_clearance,
                    violated_distance=0.0,
                )
            )
            world.collision_manager.add_default_rule(
                AvoidCollisionBetweenGroups(
                    body_group_a=[body],
                    body_group_b=obstacles,
                    buffer_zone_distance=self.gripper_clearance,
                    violated_distance=0.0,
                )
            )
        return Context(
            world=world,
            robot=robot,
            _debug=False,
            motion_tolerances=MotionToleranceConfig(
                default_tcp_position_threshold=self.position_tolerance,
                tool_orientation_threshold=self.orientation_tolerance,
            ),
        )

    def build_plan(self, context: Context) -> Plan:
        """Compose native pickup and placement with a vertical extraction clearance."""
        body = context.world.get_body_by_name(self.laboratory.source_body_name)
        target = self.laboratory.target_pose
        target.reference_frame = context.world.root
        grasp = TubeGrasp(
            ApproachDirection.RIGHT,
            VerticalAlignment.NoAlignment,
            context.robot.left_arm.end_effector,
            manipulation_offset=self.grasp_lift,
            grasp_height=self.laboratory.grasp_height,
            approach_distance=self.laboratory.tube_radius + 0.1,
            tool_orientation=Quaternion.from_rpy(0, math.pi / 2, -math.pi / 4),
        )
        return sequential(
            [
                LaboratoryPickUpAction(
                    body,
                    Arms.LEFT,
                    grasp,
                    pre_approach_linear_velocity=0.12,
                    final_approach_linear_velocity=0.025,
                    lift_linear_velocity=0.04,
                    grasp_closing_velocity=self.finger_closing_velocity,
                    grasp_joint_position_threshold=self.finger_position_threshold,
                    contact_clearance=self.finger_contact_clearance,
                ),
                LaboratoryPlaceAction(
                    body,
                    target,
                    Arms.LEFT,
                    transport_linear_velocity=0.08,
                    placing_linear_velocity=0.02,
                    retract_linear_velocity=0.08,
                ),
            ],
            context=context,
        ).plan

    def observe(self, plan: Plan, context: Context) -> LaboratoryObservation:
        """Attach acceptance measurements to the actual plan execution."""
        observation = LaboratoryObservation(
            context=context,
            body=context.world.get_body_by_name(self.laboratory.source_body_name),
            position_tolerance=self.position_tolerance,
            required_lift=self.grasp_lift,
            gripper_contact=Pr2GripperContact(
                context.world,
                context.robot.left_arm.end_effector,
                context.world.get_body_by_name(self.laboratory.source_body_name),
                clearance=self.finger_contact_clearance,
            ),
        )
        plan.node_callbacks.append(observation)
        return observation

    def create_visualization(self, world: World) -> LiveVisualization:
        """Carry the source laboratory's presentation into live viewing and replay."""
        source = json.loads(
            (self.laboratory.bundle_directory / LaboratoryAsset.SCENE).read_text()
        )
        return LiveVisualization(
            world, bridge=Bridge(presentation=ScenePresentation.from_json(source))
        )

    def run(self) -> LaboratoryResult:
        """Execute once, expose live progress and save the measured replay."""
        world = self.build_simulated_world()
        context = self.build_context(world)
        plan = self.build_plan(context)
        observation = self.observe(plan, context)
        visualization = self.create_visualization(world).start()
        plan.node_callbacks.append(visualization.plan_callback(plan))
        try:
            build_live_scene(visualization.bridge)
            with simulated_robot_advanced:
                plan.perform()
            result = observation.validate(plan, self.laboratory.target_pose)
            if not result.success:
                raise LaboratoryTransferFailed(json.dumps(asdict(result)))
            self.save_recording(visualization, result)
            return result
        finally:
            visualization.stop()

    def save_recording(
        self, visualization: LiveVisualization, result: LaboratoryResult
    ) -> Path:
        """Publish a replay and its acceptance evidence from captured world states."""
        recording = visualization.bridge.recording
        frames = recording.stop()
        destination = paths.local_scenes_directory() / LaboratoryRecording.SCENE
        scene = write_recording_bundle(
            visualization.bridge,
            frames,
            recording.frames_per_second(),
            destination,
            LaboratoryRecording.SCENE,
        )
        scene.update(
            {
                "environmentName": "Precision Laboratory",
                "task": "PR2 · Glas A1 → A3",
            }
        )
        write_json_atomically(
            destination / LaboratoryRecording.SCENE_FILE, scene, indent=2
        )
        write_json_atomically(
            destination / LaboratoryRecording.METRICS, asdict(result), indent=2
        )
        write_scene_index(
            paths.local_scenes_directory() / LaboratoryRecording.INDEX,
            LaboratoryRecording.SCENE,
        )
        recording.scene_name = LaboratoryRecording.SCENE
        return destination


def main() -> None:
    """Run one simulated PR2 transfer and print its measured outcome."""
    logging.basicConfig(level=logging.INFO)
    result = LaboratoryDemo().run()
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
