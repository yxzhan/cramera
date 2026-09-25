"""Pour two laboratory liquids using measured PR2 fingertip contacts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation
from typing_extensions import Any, ClassVar

from coraplex.datastructures.swirling import SwirlProfile, SwirlTrajectory
from cramera.laboratory_physics_program import (
    JointReference,
    ProgramField,
    ProgramState,
)
from cramera.laboratory_physics_robot import LaboratoryRobotPhysics
from cramera.laboratory_world import LaboratoryBody, LaboratoryField, LaboratorySlot


# %% recipe and manipulation stages
class MixingPhase(StrEnum):
    """Physical stages of the two-source recipe."""

    APPROACH = "approach"
    """The open gripper approaches a tube through a safe upper waypoint."""
    GRASP = "grasp"
    """Opposing contact must persist before the tube may be lifted."""
    LIFT = "lift"
    """The fingers lift the tube clear of the rack."""
    POUR = "pour"
    """The held donor tilts with its lowest lip above the receiver."""
    RETURN = "return"
    """The emptied donor returns upright above its destination."""
    SWIRL = "swirl"
    """The held receiver circles its lower end around a stationary grip."""
    PLACE = "place"
    """The held tube aligns with and descends into its rack slot."""
    RELEASE = "release"
    """The fingers open after placement."""
    SETTLE = "settle"
    """The open gripper withdraws while the tube settles."""
    COMPLETE = "complete"
    """All measured pouring and placement requirements passed."""


class MixingField(StrEnum):
    """Additional measured recipe status fields."""

    CAPTURED = "capturedMl"
    """Receiver volume increase measured for each separate donor."""
    SWIRL_COMPLETED = "swirlCompleted"
    """Whether the held receiver completed measured circular motion."""
    SWIRL_TURNS = "swirlTurns"
    """Net measured revolutions of the tilted container axis."""
    OBJECT = "object"
    """Tube currently manipulated by the robot."""
    SOURCE = "laboratory_liquid_mixing"
    """Origin label for this physical recipe controller."""
    RACK_SLOTS = "rackSlots"
    """Rack insertion poses in the renderer scene schema."""


class SeatingGeometry(StrEnum):
    """Authored collision support that bears a fully inserted tube."""

    RACK_BASE = "laboratory_rack_rack_base"
    """Horizontal base beneath the rack slots."""


@dataclass(frozen=True)
class MixingParameters:
    """Bounded joint motion and measured recipe acceptance limits."""

    donor_volume_ml: float = 5.0
    """Initial liquid in each source tube, in milliliters."""
    control_period: float = 0.01
    """Time between Cartesian feedback updates in seconds."""
    cartesian_gain: float = 5.0
    """Rate of convergence toward the interpolated physical pose."""
    maximum_joint_speed: float = 0.8
    """Maximum independently actuated arm speed in radians per second."""
    maximum_finger_speed: float = 0.3
    """Maximum finger-target speed to avoid impulsive glass contact, in radians per second."""
    grasp_estimation_rate: float = 1.0
    """Rate for updating the observed grasp transform during settling, per second."""
    maximum_grasp_translation_rate: float = 0.004
    """Maximum translation correction of the grasp estimate, in meters per second."""
    maximum_grasp_rotation_rate: float = 0.05
    """Maximum orientation correction of the grasp estimate, in radians per second."""
    jacobian_regularization: float = 0.00001
    """Damping applied when converting tool motion into joint motion."""
    shoulder_posture: float = 0.1
    """Shoulder elevation preserving the collision-checked elbow-up pouring branch."""
    posture_gain: float = 2.0
    """Rate of redundant shoulder-posture convergence during Cartesian motion, per second."""
    open_finger: float = 0.25
    """Open finger joint demand in radians."""
    teal_open_finger: float = 0.18
    """Narrower approach aperture clearing the neighboring receiver at A2."""
    teal_heading: float = -25.0
    """Horizontal front-approach rotation around world vertical, in degrees."""
    teal_insertion_time: float = 8.0
    """Gentle descent duration through the rear rack aperture, in seconds."""
    closed_finger: float = 0.045
    """Contact-limited closing demand in radians."""
    contact_threshold: float = 0.005
    """Minimum opposing normal force indicating a held tube in Newtons."""
    grasp_confirmation: float = 0.15
    """Continuous opposing contact required before transport, in seconds."""
    grasp_timeout: float = 4.0
    """Maximum closing duration before a failed grasp is reported."""
    destination_tolerance: float = 0.008
    """Maximum released receiver error relative to rack A2, in meters."""
    minimum_capture_ml: float = 4.0
    """Required measured gain from each separate donor in milliliters."""
    tool_height: float = 0.105
    """Calibrated tool-frame height above an upright tube root, in meters."""
    lift_height: float = 0.15
    """Vertical clearance above a rack position for transport, in meters."""
    secondary_staging_height: float = 0.18
    """Second-donor clearance above the emptied amber tube during pouring setup."""
    pour_gap: float = 0.02
    """Vertical gap between donor lip and receiver rim in meters."""
    pour_alignment_gap: float = 0.08
    """Rim clearance while the tilted gripper moves above the receiver."""
    front_transition_time: float = 8.0
    """Duration of the collision-checked transition to a horizontal front grip."""
    approach_distance: float = 0.06
    """Horizontal clearance before advancing the fingers toward a tube, in meters."""
    position_tolerance: float = 0.002
    """Required held-pose convergence before pouring or placement advances."""
    axis_tolerance: float = 0.025
    """Required tube-axis alignment before a held pose advances, in radians."""
    stage_timeout: float = 15.0
    """Additional settling time before an unreachable intention fails."""
    lost_contact_timeout: float = 0.5
    """Maximum interrupted bilateral contact during transport, in seconds."""
    seating_confirmation: float = 0.1
    """Continuous stable rack support required before opening, in seconds."""
    seating_depth: float = 0.0005
    """Maximum downward contact-seeking offset from a slot root, in meters."""
    seating_linear_speed: float = 0.004
    """Maximum tube translation speed accepted as seated, in meters per second."""
    seating_angular_speed: float = 0.05
    """Maximum tube angular speed accepted as seated, in radians per second."""
    seating_normal_alignment: float = 0.9
    """Minimum upward component of a load-bearing rack contact normal."""
    seating_weight_fraction: float = 0.5
    """Minimum fraction of the tube weight borne by the rack base."""
    minimum_lift: float = 0.1
    """Measured receiver clearance required to accept swirling, in meters."""
    swirl_acceptance_angle: float = 0.035
    """Minimum measured tilt for a well-defined orbital azimuth, in radians."""
    swirl_minimum_turns: float = 2.5
    """Required net measured revolutions, excluding near-upright entry and exit."""
    swirl_profile: SwirlProfile = field(
        default_factory=lambda: SwirlProfile(tilt_angle=0.16, cycles=3.0, duration=12.0)
    )
    """Shared held-container trajectory with smooth tilt entry and exit."""
    upright_tolerance: float = 0.15
    """Maximum final receiver-axis deviation from vertical in radians."""


@dataclass(frozen=True)
class MixingStage:
    """A smooth physical pose intention and its observable purpose."""

    phase: MixingPhase
    """Manipulation stage displayed in progress feedback."""
    object_key: str
    """Tube manipulated by this intention."""
    position: tuple[float, float, float]
    """Desired tube-root or calibrated virtual-root position in world coordinates."""
    rotation: tuple[float, float, float, float]
    """Desired tube-root orientation in XYZW order."""
    duration: float
    """Intended interpolation and settling duration in seconds."""
    closed: bool
    """Whether the gripper remains closed while moving."""
    joint_targets: dict[str, float] | None = None
    """Optional calibrated arm destination for the initial free-space transition."""
    open_finger: float | None = None
    """Optional approach aperture for a tube with close rack neighbors."""
    receiver_slot: LaboratorySlot | None = None
    """Receiver slot whose measured rim refines a donor's pouring intention."""


@dataclass(frozen=True)
class MixingTransfer:
    """A physical tube transfer and its optional pouring or swirling purpose."""

    object_key: LaboratoryBody
    """Tube manipulated by this transfer."""
    source: LaboratorySlot
    """Rack slot occupied before pickup."""
    destination: LaboratorySlot
    """Rack slot used after manipulation."""
    receiver: LaboratorySlot | None = None
    """Location of the clear receiver when a donor is poured."""
    swirl: bool = False
    """Whether this transfer includes a circular held-container motion."""


@dataclass
class LaboratoryMixingProgram:
    """Execute a contact-driven pouring recipe without prescribing object motion."""

    physics: LaboratoryRobotPhysics
    """Authoritative articulated contact simulation and liquid model."""
    parameters: MixingParameters = field(default_factory=MixingParameters)
    """Recipe quantities, servo rates and measured completion limits."""
    state: ProgramState = field(init=False, default=ProgramState.IDLE)
    """Execution lifecycle shared by the laboratory programs."""
    stages: list[MixingStage] = field(init=False, default_factory=list)
    """Ordered collision-aware approach, pouring and placement intentions."""
    stage_index: int = field(init=False, default=0)
    """Current stage index in the recipe."""
    stage_time: float = field(init=False, default=0.0)
    """Physical time consumed by the current intention."""
    elapsed: float = field(init=False, default=0.0)
    """Total active execution time in seconds."""
    control_elapsed: float = field(init=False, default=0.0)
    """Accumulated time since the last joint-target feedback update."""
    start_position: np.ndarray = field(init=False)
    """Measured virtual tube-root position at stage entry."""
    start_rotation: Rotation = field(init=False)
    """Measured virtual tube-root orientation at stage entry."""
    start_joint_targets: dict[str, float] = field(init=False, default_factory=dict)
    """Observed arm positions at the beginning of the calibrated transition."""
    grip_rotation: Rotation = field(init=False)
    """Calibrated world gripper rotation when a tube is upright."""
    grasp_position: np.ndarray = field(init=False, default_factory=lambda: np.zeros(3))
    """Tube-root position in the tool frame measured after closing."""
    grasp_rotation: Rotation = field(init=False, default_factory=Rotation.identity)
    """Tube orientation relative to the tool measured after closing."""
    contact_duration: float = field(init=False, default=0.0)
    """Uninterrupted measured bilateral contact duration."""
    lost_contact_duration: float = field(init=False, default=0.0)
    """Duration without bilateral contact during an accepted transport grasp."""
    seating_duration: float = field(init=False, default=0.0)
    """Uninterrupted measured support at the intended rack destination."""
    seating_pose: np.ndarray | None = field(init=False, default=None)
    """Measured control pose held while rack support is being confirmed."""
    holding: bool = field(init=False, default=False)
    """Whether both fingertips currently exert force on the selected tube."""
    grasp_verified: bool = field(init=False, default=False)
    """Whether the current grasp passed sustained bilateral contact."""
    grasped_objects: set[str] = field(init=False, default_factory=set)
    """Objects whose opposing fingertip contacts were verified during this recipe."""
    gripper_force: float = field(init=False, default=0.0)
    """Current total fingertip normal force in Newtons."""
    maximum_gripper_force: float = field(init=False, default=0.0)
    """Peak measured finger normal force during the recipe."""
    maximum_lift: float = field(init=False, default=0.0)
    """Greatest observed tube-root lift above the rack in meters."""
    released: bool = field(init=False, default=False)
    """Whether the receiver was released at the destination."""
    swirl_completed: bool = field(init=False, default=False)
    """Whether the measured receiver completed its gentle swirling sequence."""
    swirl_angle: float = field(init=False, default=0.0)
    """Accumulated signed azimuth change of the physically held container axis."""
    previous_swirl_azimuth: float | None = field(init=False, default=None)
    """Last sufficiently tilted and contact-supported container azimuth."""
    captured_ml: dict[str, float] = field(init=False, default_factory=dict)
    """Receiver volume gains measured separately for each source."""
    receiver_before: float = field(init=False, default=0.0)
    """Receiver volume before pouring the active donor."""
    error: str | None = field(init=False, default=None)
    """A measured failure preventing successful completion."""
    arm_names: list[str] = field(init=False)
    """Independently actuated left-arm joints participating in Cartesian motion."""
    arm_indices: list[int] = field(init=False)
    """MuJoCo velocity indices for Cartesian Jacobian columns."""
    destination: np.ndarray = field(init=False)
    """World tube-root position of the requested A2 rack slot."""

    TOOL_FRAME: ClassVar[str] = "pr2/l_gripper_tool_frame"
    """Physical tool body controlled through the arm Jacobian."""
    FINGERTIPS: ClassVar[tuple[str, str]] = (
        "pr2/l_gripper_l_finger_tip_link",
        "pr2/l_gripper_r_finger_tip_link",
    )
    """Opposing fingertips required for a verified grasp."""
    FRONT_TRANSITION: ClassVar[dict[str, float]] = {
        "pr2/l_shoulder_pan_joint": 0.6620746711,
        "pr2/l_shoulder_lift_joint": -0.01927773375,
        "pr2/l_upper_arm_roll_joint": 1.5708059738,
        "pr2/l_elbow_flex_joint": -2.0445152632,
        "pr2/l_forearm_roll_joint": 9.4093053213,
        "pr2/l_wrist_flex_joint": -1.3823868505,
        "pr2/l_wrist_roll_joint": -1.5797575049,
    }
    """Collision-checked arm posture with horizontal fingers above the front of A2."""

    def __post_init__(self) -> None:
        """Read the calibrated gripper approach and authored rack coordinates."""
        self.arm_names = [
            name
            for name in self.physics.joint_targets
            if name.startswith("pr2/l_") and "gripper" not in name
        ]
        self.arm_indices = [
            int(self.physics.model.jnt_dofadr[self.physics.model.joint(name).id])
            for name in self.arm_names
        ]
        self.grip_rotation = Rotation.from_matrix(
            np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]])
        )
        self.destination = self._slot(LaboratorySlot.A2)
        self.reset()

    def _slot(self, slot: LaboratorySlot) -> np.ndarray:
        """Read a named rack slot from the same manifest as the physical world."""
        return np.asarray(
            next(
                item[LaboratoryField.POSE][:3]
                for item in self.physics.scene[LaboratoryField.LABORATORY][
                    MixingField.RACK_SLOTS
                ]
                if item[LaboratoryField.ID] == slot
            ),
            dtype=float,
        )

    def reset(self) -> None:
        """Clear all measured recipe progress without changing physical contents."""
        self.state = ProgramState.IDLE
        self.stage_index = 0
        self.stage_time = self.elapsed = self.control_elapsed = 0.0
        self.contact_duration = self.maximum_gripper_force = self.maximum_lift = 0.0
        self.lost_contact_duration = 0.0
        self.gripper_force = 0.0
        self.holding = self.grasp_verified = self.released = self.swirl_completed = (
            False
        )
        self.grasped_objects.clear()
        self.swirl_angle = 0.0
        self.previous_swirl_azimuth = None
        self.captured_ml = {
            LaboratoryBody.AMBER_TUBE: 0.0,
            LaboratoryBody.TEAL_TUBE: 0.0,
        }
        self.error = None
        self.stages = self._recipe()
        self._enter_stage()

    def _recipe(self) -> list[MixingStage]:
        """Free A2, clear the frontal B1 approach, and finish with the receiver in A2."""
        stages = []
        upright = Rotation.identity()
        lift = np.array([0.0, 0.0, self.parameters.lift_height])
        front = np.array([0.0, -self.parameters.approach_distance, 0.0])
        aperture = None
        receiver_slot = None

        def append(
            phase: MixingPhase,
            key: str,
            position: np.ndarray,
            rotation: Rotation,
            duration: float,
            closed: bool,
            joint_targets: dict[str, float] | None = None,
        ) -> None:
            stages.append(
                MixingStage(
                    phase,
                    key,
                    tuple(position),
                    tuple(rotation.as_quat()),
                    duration,
                    closed,
                    joint_targets,
                    aperture,
                    receiver_slot,
                )
            )

        append(
            MixingPhase.APPROACH,
            LaboratoryBody.AMBER_TUBE,
            np.array([0.22, -0.10, 1.12 - self.parameters.tool_height]),
            upright,
            self.parameters.front_transition_time,
            False,
            self.FRONT_TRANSITION,
        )

        for transfer in (
            MixingTransfer(
                LaboratoryBody.AMBER_TUBE,
                LaboratorySlot.A2,
                LaboratorySlot.A3,
                LaboratorySlot.A1,
            ),
            MixingTransfer(
                LaboratoryBody.CLEAR_TUBE, LaboratorySlot.A1, LaboratorySlot.A2
            ),
            MixingTransfer(
                LaboratoryBody.TEAL_TUBE,
                LaboratorySlot.B1,
                LaboratorySlot.B1,
                LaboratorySlot.A2,
            ),
            MixingTransfer(
                LaboratoryBody.CLEAR_TUBE,
                LaboratorySlot.A2,
                LaboratorySlot.A2,
                swirl=True,
            ),
        ):
            key = transfer.object_key
            receiver_slot = transfer.receiver
            source = self._slot(transfer.source)
            target = self._slot(transfer.destination)
            heading = Rotation.from_euler(
                "z",
                (
                    self.parameters.teal_heading
                    if key == LaboratoryBody.TEAL_TUBE
                    else 0.0
                ),
                degrees=True,
            )
            approach = heading.apply(front)
            aperture = (
                self.parameters.teal_open_finger
                if key == LaboratoryBody.TEAL_TUBE
                else None
            )
            append(MixingPhase.APPROACH, key, source + approach, heading, 5.0, False)
            append(MixingPhase.APPROACH, key, source, heading, 4.0, False)
            append(MixingPhase.GRASP, key, source, heading, 2.0, True)
            append(MixingPhase.LIFT, key, source + lift, upright, 4.0, True)
            if transfer.receiver is not None:
                receiver = self._slot(transfer.receiver)
                interior = self.physics.liquid.interior
                lip = receiver + np.array(
                    [0.0, 0.0, interior.rim + self.parameters.pour_gap]
                )
                local_lip = np.array([-interior.radius, 0.0, interior.rim])
                staging_height = (
                    self.parameters.secondary_staging_height
                    if key == LaboratoryBody.TEAL_TUBE
                    else self.parameters.lift_height
                )
                ready = receiver + np.array([0.06, 0.0, staging_height])
                if key == LaboratoryBody.TEAL_TUBE:
                    append(MixingPhase.LIFT, key, ready, upright, 4.0, True)
                for angle in (30, 60):
                    rotation = Rotation.from_euler("y", -angle, degrees=True)
                    append(MixingPhase.POUR, key, ready, rotation, 3.0, True)
                for angle in (60, 80, 95):
                    rotation = Rotation.from_euler("y", -angle, degrees=True)
                    high_lip = lip + np.array(
                        [
                            0.0,
                            0.0,
                            (
                                self.parameters.pour_alignment_gap
                                - self.parameters.pour_gap
                            )
                            * max(0.0, (80.0 - angle) / 20.0),
                        ]
                    )
                    append(
                        MixingPhase.POUR,
                        key,
                        high_lip - rotation.apply(local_lip),
                        rotation,
                        4.0,
                        True,
                    )
                rotation = Rotation.from_euler("y", -95, degrees=True)
                append(
                    MixingPhase.POUR,
                    key,
                    lip - rotation.apply(local_lip),
                    rotation,
                    6.0,
                    True,
                )
                append(
                    MixingPhase.RETURN,
                    key,
                    ready,
                    Rotation.from_euler("y", -60, degrees=True),
                    5.0,
                    True,
                )
                append(MixingPhase.RETURN, key, ready, heading, 4.0, True)
            elif transfer.swirl:
                center = source + lift
                append(
                    MixingPhase.SWIRL,
                    key,
                    center,
                    upright,
                    self.parameters.swirl_profile.duration,
                    True,
                )
            append(MixingPhase.PLACE, key, target + lift, heading, 4.0, True)
            append(
                MixingPhase.PLACE,
                key,
                target,
                heading,
                (
                    self.parameters.teal_insertion_time
                    if key == LaboratoryBody.TEAL_TUBE
                    else 5.0
                ),
                True,
            )
            append(MixingPhase.RELEASE, key, target, heading, 1.0, False)
            append(MixingPhase.SETTLE, key, target + approach, heading, 3.0, False)
        return stages

    @property
    def stage(self) -> MixingStage:
        """Current recipe intention, including after completion."""
        return self.stages[min(self.stage_index, len(self.stages) - 1)]

    def _stage_position(self) -> np.ndarray:
        """Resolve measured rim targets and hand-relative release and withdrawal."""
        position = np.asarray(self.stage.position)
        if self._placing_for_release():
            return position - np.array([0.0, 0.0, self.parameters.seating_depth])
        if self.stage.phase == MixingPhase.RELEASE:
            return self.start_position.copy()
        if self.stage.phase == MixingPhase.SETTLE:
            return (
                self.start_position
                + position
                - np.asarray(self.stages[self.stage_index - 1].position)
            )
        if self.stage.phase != MixingPhase.POUR or self.stage.receiver_slot is None:
            return position
        identifier = self.physics.objects[LaboratoryBody.CLEAR_TUBE].body_id
        rotation = self.physics.data.xmat[identifier].reshape(3, 3)
        local_rim = np.array([0.0, 0.0, self.physics.liquid.interior.rim])
        measured_rim = self.physics.data.xpos[identifier] + rotation @ local_rim
        nominal_rim = self._slot(self.stage.receiver_slot) + local_rim
        return position + measured_rim - nominal_rim

    def _swirl_pose(self, elapsed: float) -> np.ndarray:
        """Evaluate the shared circular container trajectory around its grip height."""
        transform = np.eye(4)
        transform[:3, :3] = Rotation.from_quat(self.stage.rotation).as_matrix()
        transform[:3, 3] = self.stage.position
        return SwirlTrajectory(
            transform,
            np.array([0.0, 0.0, self.parameters.tool_height]),
            self.parameters.swirl_profile,
        ).pose_at(elapsed)

    def _pose(self) -> tuple[np.ndarray, Rotation]:
        """Measure the manipulated tube or the open gripper's calibrated virtual root."""
        if self.grasp_verified and self.stage.closed:
            identifier = self.physics.objects[self.stage.object_key].body_id
            return self.physics.data.xpos[identifier].copy(), Rotation.from_matrix(
                self.physics.data.xmat[identifier].reshape(3, 3)
            )
        return self._virtual_pose()

    def _virtual_pose(self) -> tuple[np.ndarray, Rotation]:
        """Measure the fixed calibration frame used while approaching and closing."""
        tool = self.physics.model.body(self.TOOL_FRAME).id
        rotation = (
            Rotation.from_matrix(self.physics.data.xmat[tool].reshape(3, 3))
            * self.grip_rotation.inv()
        )
        position = self.physics.data.xpos[tool] - rotation.apply(
            [0.0, 0.0, self.parameters.tool_height]
        )
        return position, rotation

    def _enter_stage(self) -> None:
        """Capture physical interpolation origins and per-pour volume baselines."""
        self.stage_time = 0.0
        self.seating_duration = 0.0
        self.seating_pose = None
        if self.stage.phase == MixingPhase.APPROACH:
            self.grasp_verified = False
        if self.stage.phase == MixingPhase.LIFT:
            tool = self.physics.model.body(self.TOOL_FRAME).id
            tool_rotation = Rotation.from_matrix(
                self.physics.data.xmat[tool].reshape(3, 3)
            )
            position, rotation = self._pose()
            self.grasp_position = tool_rotation.inv().apply(
                position - self.physics.data.xpos[tool]
            )
            calibration = self.grip_rotation.inv()
            observed = tool_rotation.inv() * rotation
            self.grasp_rotation = (
                self._axis_alignment(calibration, observed) * calibration
            )
        self.start_position, self.start_rotation = self._control_pose()
        self.start_joint_targets = {
            name: float(
                self.physics.data.qpos[self.physics.robot_joints[name].position_index]
            )
            for name in self.arm_names
        }
        if self.stage.phase == MixingPhase.GRASP:
            self.contact_duration = 0.0
            self.lost_contact_duration = 0.0
        if self.stage.phase == MixingPhase.LIFT:
            self.receiver_before = self._receiver_volume()

    def start(self) -> None:
        """Prepare the recipe once or resume with all measured contents preserved."""
        if self.state not in (ProgramState.IDLE, ProgramState.PAUSED):
            return
        if self.state == ProgramState.IDLE:
            self.physics.fill_liquid(LaboratoryBody.CLEAR_TUBE, 0.0)
            self.physics.fill_liquid(
                LaboratoryBody.AMBER_TUBE, self.parameters.donor_volume_ml
            )
            self.physics.fill_liquid(
                LaboratoryBody.TEAL_TUBE, self.parameters.donor_volume_ml
            )
        self.physics.release()
        self.state = ProgramState.RUNNING

    def pause(self) -> None:
        """Stop advancing intentions while ordinary physical dynamics continue."""
        if self.state == ProgramState.RUNNING:
            self.state = ProgramState.PAUSED

    def advance(self) -> None:
        """Drive bounded arm and finger actuators toward smooth physical intentions."""
        if self.state != ProgramState.RUNNING:
            return
        self.stage_time += self.physics.timestep
        self.elapsed += self.physics.timestep
        self.control_elapsed += self.physics.timestep
        if self.control_elapsed < self.parameters.control_period:
            return
        duration = self.control_elapsed
        self.control_elapsed = 0.0
        self._estimate_grasp(duration)
        fraction = min(1.0, self.stage_time / max(0.1, self.stage.duration - 0.4))
        fraction = fraction * fraction * (3.0 - 2.0 * fraction)
        if self.stage.joint_targets is not None:
            self._set_joint_targets(
                {
                    name: start + fraction * (target - start)
                    for name, target in self.stage.joint_targets.items()
                    for start in [self.start_joint_targets[name]]
                },
                duration,
            )
            return
        target_position = self.start_position + fraction * (
            self._stage_position() - self.start_position
        )
        final_rotation = (
            self.start_rotation
            if self.stage.phase in (MixingPhase.RELEASE, MixingPhase.SETTLE)
            else Rotation.from_quat(self.stage.rotation)
        )
        rotation_delta = (final_rotation * self.start_rotation.inv()).as_rotvec()
        target_rotation = (
            Rotation.from_rotvec(fraction * rotation_delta) * self.start_rotation
        )
        if self.stage.phase == MixingPhase.POUR:
            target_position, target_rotation = self._pour_approach(fraction)
        if self.stage.phase == MixingPhase.SWIRL:
            target = self._swirl_pose(self.stage_time)
            target_position = target[:3, 3]
            target_rotation = Rotation.from_matrix(target[:3, :3])
        if self.seating_pose is not None:
            target_position = self.seating_pose[:3, 3]
            target_rotation = Rotation.from_matrix(self.seating_pose[:3, :3])
        position, rotation = self._control_pose()
        angular_error = (target_rotation * rotation.inv()).as_rotvec()
        if self.stage.phase == MixingPhase.POUR:
            interior = self.physics.liquid.interior
            local_lip = interior.lowest_lip(rotation.inv().apply([0.0, 0.0, 1.0]))
            desired_lip = interior.lowest_lip(
                target_rotation.inv().apply([0.0, 0.0, 1.0])
            )
            position = position + rotation.apply(local_lip)
            target_position = target_position + target_rotation.apply(desired_lip)
        velocity = self.parameters.cartesian_gain * np.concatenate(
            (target_position - position, angular_error)
        )
        position_jacobian = np.zeros((3, self.physics.model.nv))
        rotation_jacobian = np.zeros((3, self.physics.model.nv))
        mujoco.mj_jac(
            self.physics.model,
            self.physics.data,
            position_jacobian,
            rotation_jacobian,
            position,
            self.physics.model.body(self.TOOL_FRAME).id,
        )
        jacobian = np.vstack(
            (
                position_jacobian[:, self.arm_indices],
                rotation_jacobian[:, self.arm_indices],
            )
        )
        active = list(range(len(self.arm_names)))
        joint_velocity = np.zeros(len(self.arm_names))
        for _ in self.arm_names:
            selected = jacobian[:, active]
            proposal = selected.T @ np.linalg.solve(
                selected @ selected.T
                + self.parameters.jacobian_regularization * np.eye(6),
                velocity,
            )
            shoulder = self.arm_names.index("pr2/l_shoulder_lift_joint")
            if shoulder in active:
                column = active.index(shoulder)
                nullspace = np.eye(len(active)) - np.linalg.pinv(selected) @ selected
                direction = nullspace[:, column]
                if direction[column] > 0.001:
                    joint = self.physics.robot_joints[self.arm_names[shoulder]]
                    desired_speed = self.parameters.posture_gain * (
                        self.parameters.shoulder_posture
                        - self.physics.data.qpos[joint.position_index]
                    )
                    proposal += (
                        direction
                        * (desired_speed - proposal[column])
                        / direction[column]
                    )
            blocked = []
            for index, speed in zip(active, proposal):
                name = self.arm_names[index]
                joint = self.physics.robot_joints[name]
                target = self.physics.joint_targets[name]
                if (speed < 0 and target <= joint.lower + 0.002) or (
                    speed > 0 and target >= joint.upper - 0.002
                ):
                    blocked.append(index)
                else:
                    joint_velocity[index] = speed
            if not blocked:
                break
            for index in blocked:
                active.remove(index)
                joint_velocity[index] = 0.0
        joint_velocity /= max(
            1.0, np.max(np.abs(joint_velocity)) / self.parameters.maximum_joint_speed
        )
        targets = {
            name: float(
                np.clip(
                    self.physics.joint_targets[name] + speed * duration,
                    self.physics.robot_joints[name].lower,
                    self.physics.robot_joints[name].upper,
                )
            )
            for name, speed in zip(self.arm_names, joint_velocity)
        }
        self._set_joint_targets(targets, duration)

    def _pour_approach(self, fraction: float) -> tuple[np.ndarray, Rotation]:
        """Complete a lowering pour's tilt before descending beside the receiver."""
        final_position = self._stage_position()
        final_rotation = Rotation.from_quat(self.stage.rotation)
        angular = (final_rotation * self.start_rotation.inv()).as_rotvec()
        if (
            final_position[2]
            >= self.start_position[2] - self.parameters.position_tolerance
            or np.linalg.norm(angular) < self.parameters.axis_tolerance
        ):
            return (
                self.start_position + fraction * (final_position - self.start_position),
                Rotation.from_rotvec(fraction * angular) * self.start_rotation,
            )
        turning = min(1.0, 2.0 * fraction)
        lowering = max(0.0, 2.0 * fraction - 1.0)
        turning = turning * turning * (3.0 - 2.0 * turning)
        lowering = lowering * lowering * (3.0 - 2.0 * lowering)
        rotation = Rotation.from_rotvec(turning * angular) * self.start_rotation
        interior = self.physics.liquid.interior
        start_lip = self.start_position + self.start_rotation.apply(
            interior.lowest_lip(self.start_rotation.inv().apply([0.0, 0.0, 1.0]))
        )
        final_lip = final_position + final_rotation.apply(
            interior.lowest_lip(final_rotation.inv().apply([0.0, 0.0, 1.0]))
        )
        lip = start_lip + lowering * (final_lip - start_lip)
        return (
            lip - rotation.apply(interior.lowest_lip(rotation.inv().apply([0, 0, 1]))),
            rotation,
        )

    def _set_joint_targets(self, targets: dict[str, float], duration: float) -> None:
        """Send arm targets and a slew-limited finger intention to the real actuators."""
        finger_target = (
            self.parameters.closed_finger
            if self.stage.closed
            else (
                self.stage.open_finger
                if self.stage.open_finger is not None
                else self.parameters.open_finger
            )
        )
        previous_finger = self.physics.joint_targets[JointReference.GRIPPER_JOINT]
        finger_increment = self.parameters.maximum_finger_speed * duration
        targets[JointReference.GRIPPER_JOINT] = float(
            np.clip(
                finger_target,
                previous_finger - finger_increment,
                previous_finger + finger_increment,
            )
        )
        self.physics.set_joint_targets(targets)

    def _control_pose(self) -> tuple[np.ndarray, Rotation]:
        """Measure a grasp-frame intention rigidly attached to the actuated tool."""
        if (
            not self.grasp_verified
            or not self.stage.closed
            or self.stage.phase == MixingPhase.GRASP
        ):
            return self._virtual_pose()
        tool = self.physics.model.body(self.TOOL_FRAME).id
        rotation = Rotation.from_matrix(self.physics.data.xmat[tool].reshape(3, 3))
        return (
            self.physics.data.xpos[tool] + rotation.apply(self.grasp_position),
            rotation * self.grasp_rotation,
        )

    def _estimate_grasp(self, duration: float) -> None:
        """Relocalize the tube axis and position while preserving front-grasp heading."""
        if (
            not self.grasp_verified
            or not self.holding
            or not self.stage.closed
            or self.stage.phase == MixingPhase.GRASP
            or self.stage_time < self.stage.duration - 0.4
        ):
            return
        tool = self.physics.model.body(self.TOOL_FRAME).id
        tool_rotation = Rotation.from_matrix(self.physics.data.xmat[tool].reshape(3, 3))
        position, rotation = self._pose()
        observed_position = tool_rotation.inv().apply(
            position - self.physics.data.xpos[tool]
        )
        observed_rotation = tool_rotation.inv() * rotation
        blend = 1.0 - math.exp(-self.parameters.grasp_estimation_rate * duration)
        translation = blend * (observed_position - self.grasp_position)
        translation_length = np.linalg.norm(translation)
        maximum_translation = self.parameters.maximum_grasp_translation_rate * duration
        if translation_length > maximum_translation:
            translation *= maximum_translation / translation_length
        angular = (
            blend
            * self._axis_alignment(self.grasp_rotation, observed_rotation).as_rotvec()
        )
        angular_length = np.linalg.norm(angular)
        maximum_angular = self.parameters.maximum_grasp_rotation_rate * duration
        if angular_length > maximum_angular:
            angular *= maximum_angular / angular_length
        self.grasp_position += translation
        self.grasp_rotation = Rotation.from_rotvec(angular) * self.grasp_rotation

    @staticmethod
    def _axis_alignment(reference: Rotation, observed: Rotation) -> Rotation:
        """Align the glass axes with no correction of unobservable axial rotation."""
        return Rotation.align_vectors(
            observed.apply([0.0, 0.0, 1.0])[None, :],
            reference.apply([0.0, 0.0, 1.0])[None, :],
        )[0]

    def _receiver_volume(self) -> float:
        """Read current receiver contents from the authoritative reduced fluid model."""
        return float(self.physics.liquid.tubes[LaboratoryBody.CLEAR_TUBE].volume_ml)

    def _placing_for_release(self) -> bool:
        """Identify the final rack descent immediately preceding finger opening."""
        return (
            self.stage.phase == MixingPhase.PLACE
            and self.stage.closed
            and self.stage_index + 1 < len(self.stages)
            and self.stages[self.stage_index + 1].phase == MixingPhase.RELEASE
        )

    def _supported_seating(self, object_id: int) -> bool:
        """Require quiet upright placement with measured weight on the rack base."""
        if not self._placing_for_release() or not self.grasp_verified:
            return False
        position, rotation = self._pose()
        if np.linalg.norm(
            position - np.asarray(self.stage.position)
        ) > self.parameters.position_tolerance or rotation.apply([0.0, 0.0, 1.0])[
            2
        ] < math.cos(
            self.parameters.upright_tolerance
        ):
            return False
        velocity = np.zeros(6)
        mujoco.mj_objectVelocity(
            self.physics.model,
            self.physics.data,
            mujoco.mjtObj.mjOBJ_BODY,
            object_id,
            velocity,
            0,
        )
        if (
            np.linalg.norm(velocity[:3]) > self.parameters.seating_angular_speed
            or np.linalg.norm(velocity[3:]) > self.parameters.seating_linear_speed
        ):
            return False
        support = 0.0
        base = self.physics.model.geom(SeatingGeometry.RACK_BASE).id
        for index in range(self.physics.data.ncon):
            contact = self.physics.data.contact[index]
            if base not in (contact.geom1, contact.geom2) or contact.efc_address < 0:
                continue
            other = contact.geom2 if contact.geom1 == base else contact.geom1
            if self.physics.model.geom_bodyid[other] != object_id:
                continue
            upward = contact.frame[2] * (1 if contact.geom1 == base else -1)
            if upward < self.parameters.seating_normal_alignment:
                continue
            force = np.zeros(6)
            mujoco.mj_contactForce(self.physics.model, self.physics.data, index, force)
            support += max(0.0, float(force[0])) * upward
        weight = self.physics.model.body_mass[object_id] * np.linalg.norm(
            self.physics.model.opt.gravity
        )
        return support >= weight * self.parameters.seating_weight_fraction

    def observe(self) -> None:
        """Accept grasp, capture and completion only from measured physical evidence."""
        if self.state != ProgramState.RUNNING:
            return
        object_id = self.physics.objects[self.stage.object_key].body_id
        force_by_finger = {name: 0.0 for name in self.FINGERTIPS}
        for index in range(self.physics.data.ncon):
            contact = self.physics.data.contact[index]
            first = int(self.physics.model.geom_bodyid[contact.geom1])
            second = int(self.physics.model.geom_bodyid[contact.geom2])
            if object_id not in (first, second) or contact.efc_address < 0:
                continue
            other = self.physics.model.body(
                second if first == object_id else first
            ).name
            if other in force_by_finger:
                force = np.zeros(6)
                mujoco.mj_contactForce(
                    self.physics.model, self.physics.data, index, force
                )
                force_by_finger[other] += max(0.0, float(force[0]))
        self.holding = all(
            value > self.parameters.contact_threshold
            for value in force_by_finger.values()
        )
        self.gripper_force = sum(force_by_finger.values())
        self.maximum_gripper_force = max(self.maximum_gripper_force, self.gripper_force)
        self.maximum_lift = max(
            self.maximum_lift,
            float(self.physics.data.xpos[object_id, 2]) - self.destination[2],
        )
        if self._supported_seating(object_id):
            if self.seating_pose is None:
                position, rotation = self._control_pose()
                self.seating_pose = np.eye(4)
                self.seating_pose[:3, 3] = position
                self.seating_pose[:3, :3] = rotation.as_matrix()
            self.seating_duration += self.physics.timestep
        else:
            self.seating_duration = 0.0
            self.seating_pose = None
        if self.seating_duration >= self.parameters.seating_confirmation:
            self._complete_stage()
            return
        if (
            self.grasp_verified
            and self.stage.closed
            and self.stage.phase != MixingPhase.GRASP
        ):
            self.lost_contact_duration = (
                self.lost_contact_duration + self.physics.timestep
                if not self.holding
                else 0.0
            )
            if self.lost_contact_duration > self.parameters.lost_contact_timeout:
                self._fail(
                    "The gripper lost opposing contacts with " + self.stage.object_key
                )
                return
        if self.stage.phase == MixingPhase.GRASP:
            self.contact_duration = (
                self.contact_duration + self.physics.timestep if self.holding else 0.0
            )
            if self.contact_duration >= self.parameters.grasp_confirmation:
                self.grasp_verified = True
                self.grasped_objects.add(self.stage.object_key)
            if (
                self.stage_time >= self.parameters.grasp_timeout
                and not self.grasp_verified
            ):
                self._fail(
                    "No sustained opposing fingertip contacts on "
                    + self.stage.object_key
                )
            if not self.grasp_verified:
                return
        if self.stage.phase == MixingPhase.SWIRL:
            self._observe_swirl()
        if self._placing_for_release():
            if self.stage_time > self.stage.duration + self.parameters.stage_timeout:
                self._fail("The tube did not settle on its intended rack support")
            return
        if self.stage_time < self.stage.duration:
            return
        if self.stage.closed and self.stage.phase in (
            MixingPhase.POUR,
            MixingPhase.PLACE,
        ):
            position, rotation = self._pose()
            desired_position = self._stage_position()
            desired_rotation = Rotation.from_quat(self.stage.rotation)
            if self.stage.phase == MixingPhase.POUR:
                interior = self.physics.liquid.interior
                position = position + rotation.apply(
                    interior.lowest_lip(rotation.inv().apply([0.0, 0.0, 1.0]))
                )
                desired_position = desired_position + desired_rotation.apply(
                    interior.lowest_lip(desired_rotation.inv().apply([0.0, 0.0, 1.0]))
                )
            translation_error = np.linalg.norm(desired_position - position)
            rotation_error = math.acos(
                float(
                    np.clip(
                        np.dot(
                            Rotation.from_quat(self.stage.rotation).apply(
                                [0.0, 0.0, 1.0]
                            ),
                            rotation.apply([0.0, 0.0, 1.0]),
                        ),
                        -1.0,
                        1.0,
                    )
                )
            )
            if (
                translation_error > self.parameters.position_tolerance
                or rotation_error > self.parameters.axis_tolerance
            ):
                if (
                    self.stage_time
                    > self.stage.duration + self.parameters.stage_timeout
                ):
                    self._fail("The held tube did not converge to its intended pose")
                return
        if self.stage.phase == MixingPhase.RETURN:
            self.captured_ml[self.stage.object_key] = (
                self._receiver_volume() - self.receiver_before
            )
        if self.stage.phase == MixingPhase.RELEASE:
            self.grasp_verified = False
        self._complete_stage()

    def _complete_stage(self) -> None:
        """Capture the next intention's physical origin or validate final completion."""
        self.stage_index += 1
        if self.stage_index >= len(self.stages):
            self._finish()
        else:
            self._enter_stage()

    def _observe_swirl(self) -> None:
        """Count continuous measured azimuth only while the lifted glass is held."""
        position, rotation = self._pose()
        axis = rotation.apply([0.0, 0.0, 1.0])
        tilt = math.atan2(float(np.linalg.norm(axis[:2])), float(axis[2]))
        if (
            not self.holding
            or position[2] <= self.destination[2] + self.parameters.minimum_lift
            or tilt < self.parameters.swirl_acceptance_angle
        ):
            self.previous_swirl_azimuth = None
            return
        azimuth = math.atan2(float(axis[1]), float(axis[0]))
        if self.previous_swirl_azimuth is not None:
            difference = azimuth - self.previous_swirl_azimuth
            self.swirl_angle += math.atan2(math.sin(difference), math.cos(difference))
        self.previous_swirl_azimuth = azimuth
        self.swirl_completed = (
            abs(self.swirl_angle) / math.tau >= self.parameters.swirl_minimum_turns
        )

    def _fail(self, message: str) -> None:
        """Stop recipe progression after a measured failure."""
        self.error = message
        self.state = ProgramState.FAILED

    def _finish(self) -> None:
        """Require two captured portions, a completed swirl and a released A2 receiver."""
        self.released = self.gripper_force < self.parameters.contact_threshold
        if min(self.captured_ml.values()) < self.parameters.minimum_capture_ml:
            self._fail("Both donors did not deliver the required measured volume")
        elif (
            not self.released
            or self.destination_error > self.parameters.destination_tolerance
        ):
            self._fail("The mixed receiver did not settle released in rack A2")
        elif not self.swirl_completed:
            self._fail("The receiver did not complete its swirling motion")
        elif self.physics.data.xmat[
            self.physics.objects[LaboratoryBody.CLEAR_TUBE].body_id
        ].reshape(3, 3)[2, 2] < math.cos(self.parameters.upright_tolerance):
            self._fail("The receiver did not settle upright")
        else:
            self.state = ProgramState.SUCCEEDED

    @property
    def destination_error(self) -> float:
        """Current measured distance from the receiver root to rack A2."""
        position = self.physics.data.xpos[
            self.physics.objects[LaboratoryBody.CLEAR_TUBE].body_id
        ]
        return float(np.linalg.norm(position - self.destination))

    def snapshot(self) -> dict[str, Any]:
        """Publish actual contact and fluid evidence for the two-source recipe."""
        return {
            ProgramField.STATE: self.state,
            ProgramField.PHASE: (
                MixingPhase.COMPLETE
                if self.state == ProgramState.SUCCEEDED
                else self.stage.phase
            ),
            ProgramField.SOURCE: MixingField.SOURCE,
            ProgramField.TIME: self.elapsed,
            ProgramField.DURATION: sum(stage.duration for stage in self.stages),
            ProgramField.PROGRESS: min(
                1.0,
                (self.stage_index + min(1.0, self.stage_time / self.stage.duration))
                / len(self.stages),
            ),
            ProgramField.GRASP_VERIFIED: bool(self.grasped_objects),
            ProgramField.HOLDING: self.holding,
            ProgramField.GRIPPER_FORCE: self.gripper_force,
            ProgramField.MAXIMUM_GRIPPER_FORCE: self.maximum_gripper_force,
            ProgramField.MAXIMUM_LIFT: self.maximum_lift,
            ProgramField.DESTINATION_ERROR: self.destination_error,
            ProgramField.RELEASED: self.released,
            ProgramField.ERROR: self.error,
            MixingField.CAPTURED: dict(self.captured_ml),
            MixingField.SWIRL_COMPLETED: self.swirl_completed,
            MixingField.SWIRL_TURNS: abs(self.swirl_angle) / math.tau,
            MixingField.OBJECT: self.stage.object_key,
        }
