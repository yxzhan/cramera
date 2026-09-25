"""Run recorded CRAM joint intentions against measured MuJoCo grasp contacts."""

from __future__ import annotations

import json
import importlib
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import mujoco
import numpy as np
from typing_extensions import Any, ClassVar, TYPE_CHECKING

from cramera.laboratory_liquid import LiquidField
from cramera.laboratory_physics import InvalidPhysicsTarget

if TYPE_CHECKING:
    from cramera.laboratory_mixing_program import LaboratoryMixingProgram
    from cramera.laboratory_physics_robot import LaboratoryRobotPhysics


# %% recorded joint reference
class ReferenceField(StrEnum):
    """Fields from native CRAMERA recording files."""

    SCENE = "scene.json"
    """Native scene manifest filename."""
    TRAJECTORY = "trajectory.json"
    """Native recorded state filename."""
    FRAMES = "frames"
    """Recorded robot joint positions."""
    FRAME_RATE = "framesPerSecond"
    """Samples per second."""
    OBJECTS = "objects"
    """Recorded object poses used to read the intended destination."""
    SEGMENTS = "segments"
    """Recorded action intervals."""
    PICKS = "picks"
    """The object acted on by a pickup interval."""
    ATTACH = "attach"
    """Native grasp transition frame."""
    DETACH = "detach"
    """Native release transition frame."""


class ProgramState(StrEnum):
    """Execution state independent of the physics worker lifecycle."""

    IDLE = "idle"
    """The program has not started."""
    RUNNING = "running"
    """Joint intentions advance with the contact simulation."""
    PAUSED = "paused"
    """Joint intentions are held at their current values."""
    SUCCEEDED = "succeeded"
    """Measured physical placement met all acceptance criteria."""
    FAILED = "failed"
    """A required physical manipulation condition was not satisfied."""


class ProgramPhase(StrEnum):
    """Measured stages of a physical laboratory transfer."""

    APPROACH = "approach"
    """The robot approaches the tube."""
    GRASP = "grasp"
    """The program waits for sustained opposing fingertip contacts."""
    TRANSPORT = "transport"
    """The tube is lifted and moved toward its destination."""
    RELEASE = "release"
    """The gripper opens after placing the tube."""
    SETTLE = "settle"
    """Physics settles after the last recorded joint target."""
    COMPLETE = "complete"
    """The physical transfer passed its acceptance criteria."""


class ProgramField(StrEnum):
    """Robot execution feedback shared with the browser."""

    ROBOT = "robot"
    """The nested robot program status."""
    STATE = "state"
    """Program lifecycle state."""
    PHASE = "phase"
    """Current physical manipulation stage."""
    PROGRESS = "progress"
    """Fraction of the recorded joint reference consumed."""
    SOURCE = "source"
    """Origin of the robot joint intentions."""
    SOURCE_RECORDING = "recorded_cram_joint_targets"
    """Joint intentions come from a previously executed native CRAM plan."""
    GRASP_VERIFIED = "graspVerified"
    """Whether opposing fingertip contacts were sustained before lifting."""
    HOLDING = "holding"
    """Whether both fingertips currently exert a contact force."""
    MAXIMUM_LIFT = "maximumLift"
    """Largest measured tube height increase in meters."""
    MAXIMUM_GRIPPER_FORCE = "maximumGripperForce"
    """Peak sum of fingertip normal forces in Newtons."""
    GRIPPER_FORCE = "gripperForce"
    """Current sum of fingertip normal forces in Newtons."""
    DESTINATION_ERROR = "destinationError"
    """Current distance to the intended placement in meters."""
    RELEASED = "released"
    """Whether opening removed the fingertip contacts."""
    ERROR = "error"
    """Explanation of a measured failure."""
    DURATION = "duration"
    """Reference motion duration in seconds."""
    TIME = "time"
    """Current reference time in seconds."""


@dataclass(frozen=True)
class JointReference:
    """A recorded joint trajectory and its intended transfer destination."""

    frames: tuple[dict[str, float], ...]
    """Recorded robot joint positions, with no object poses."""
    frame_rate: float
    """Recording samples per second."""
    object_key: str
    """The authored object named by the recorded pickup segment."""
    destination: tuple[float, float, float]
    """Intended final object position, used only for success evaluation."""
    grasp_time: float
    """Start of the recorded closed-gripper plateau before lifting."""
    release_time: float
    """Recorded transfer completion time before gripper opening."""
    gripper_position: float
    """The contact-clearance finger target in the native recording."""

    GRIPPER_JOINT: ClassVar[str] = "pr2/l_gripper_l_finger_joint"
    """The independently driven left gripper finger joint."""
    PLATEAU_TOLERANCE: ClassVar[float] = 0.0001
    """Angular tolerance for finding the start of a settled grasp target."""

    @classmethod
    def load(cls, directory: Path) -> JointReference:
        """Read finite joint targets and transfer metadata from a local recording."""
        trajectory = json.loads((directory / ReferenceField.TRAJECTORY).read_text())
        scene = json.loads((directory / ReferenceField.SCENE).read_text())
        frame_rate = float(trajectory[ReferenceField.FRAME_RATE])
        frames = tuple(
            {
                key: float(value)
                for key, value in frame.items()
                if key.startswith("pr2/")
            }
            for frame in trajectory[ReferenceField.FRAMES]
        )
        if not frames or not math.isfinite(frame_rate) or frame_rate <= 0:
            raise ValueError("The PR2 recording needs frames and a positive frame rate")
        names = frames[0].keys()
        if cls.GRIPPER_JOINT not in names or any(
            frame.keys() != names
            or not all(math.isfinite(value) for value in frame.values())
            for frame in frames
        ):
            raise ValueError(
                "The PR2 recording contains incomplete or nonfinite joints"
            )
        segment = next(
            item
            for item in scene[ReferenceField.SEGMENTS]
            if ReferenceField.PICKS in item
        )
        object_key = segment[ReferenceField.PICKS]
        attach = int(segment[ReferenceField.ATTACH])
        detach = int(segment[ReferenceField.DETACH])
        if not 0 <= attach <= detach < len(frames):
            raise ValueError("The PR2 recording has invalid transfer frame bounds")
        gripper = frames[attach][cls.GRIPPER_JOINT]
        start = attach
        while (
            start > 0
            and abs(frames[start - 1][cls.GRIPPER_JOINT] - gripper)
            <= cls.PLATEAU_TOLERANCE
        ):
            start -= 1
        destination = tuple(
            float(value)
            for value in trajectory[ReferenceField.OBJECTS][-1][object_key][:3]
        )
        if len(destination) != 3 or not all(
            math.isfinite(value) for value in destination
        ):
            raise ValueError("The PR2 recording has an invalid transfer destination")
        return cls(
            frames,
            frame_rate,
            object_key,
            destination,
            start / frame_rate,
            detach / frame_rate,
            gripper,
        )

    @property
    def duration(self) -> float:
        """Time of the final recorded joint sample."""
        return (len(self.frames) - 1) / self.frame_rate

    def sample(self, time: float) -> dict[str, float]:
        """Interpolate robot joint positions without consuming recorded object poses."""
        index = max(0.0, min(len(self.frames) - 1, time * self.frame_rate))
        first = int(index)
        second = min(first + 1, len(self.frames) - 1)
        fraction = index - first
        return {
            name: value + fraction * (self.frames[second][name] - value)
            for name, value in self.frames[first].items()
        }


# %% measured contact execution
@dataclass(frozen=True)
class ProgramParameters:
    """Contact confirmation and physical completion tolerances."""

    closure_offset: float = 0.04
    """Additional finger demand in radians, limited by the model's actuator effort."""
    closure_blend: float = 0.08
    """Angular interval over which additional closure is introduced smoothly."""
    contact_threshold: float = 0.005
    """Minimum normal force on each opposing fingertip in Newtons."""
    grasp_confirmation_time: float = 0.15
    """Continuous bilateral contact required before lifting, in seconds."""
    grasp_timeout: float = 3.0
    """Maximum wait for bilateral fingertip contacts, in seconds."""
    settling_time: float = 0.8
    """Physics time allowed after the last joint target before evaluation."""
    minimum_lift: float = 0.1
    """Required measured object height increase in meters."""
    destination_tolerance: float = 0.008
    """Maximum final position error in meters."""
    alignment_lead_time: float = 9.0
    """Time before release at which physical placement alignment begins."""
    alignment_gain: float = 3.0
    """Rate at which lateral position and tube tilt errors are corrected."""
    maximum_correction_speed: float = 0.15
    """Maximum feedback joint correction rate in radians per second."""
    maximum_correction: float = 0.2
    """Maximum accumulated feedback offset of one joint in radians."""
    jacobian_regularization: float = 0.0001
    """Damping of the Cartesian feedback inverse near singular configurations."""
    upright_tolerance: float = 0.2
    """Maximum tube-axis angle from vertical in radians."""


@dataclass
class LaboratoryPhysicsProgram:
    """Execute joint references while accepting success only from physical evidence."""

    physics: LaboratoryRobotPhysics
    """The authoritative articulated contact simulation."""
    reference: JointReference
    """Native CRAM joint intentions and the intended transfer destination."""
    parameters: ProgramParameters = field(default_factory=ProgramParameters)
    """Measured grasp and placement acceptance limits."""
    state: ProgramState = field(init=False, default=ProgramState.IDLE)
    """Execution lifecycle separate from physics stepping."""
    phase: ProgramPhase = field(init=False, default=ProgramPhase.APPROACH)
    """Current measured manipulation stage."""
    reference_time: float = field(init=False, default=0.0)
    """Current position within the joint reference, in seconds."""
    grasp_wait: float = field(init=False, default=0.0)
    """Physics time spent confirming the grasp."""
    contact_duration: float = field(init=False, default=0.0)
    """Continuous duration of bilateral fingertip contact."""
    settling_duration: float = field(init=False, default=0.0)
    """Physics time after the final reference sample."""
    grasp_verified: bool = field(init=False, default=False)
    """Whether opposing physical finger contacts were confirmed before lifting."""
    holding: bool = field(init=False, default=False)
    """Whether both fingertips currently exert a normal force on the tube."""
    gripper_force: float = field(init=False, default=0.0)
    """Sum of current fingertip normal forces on the tube in Newtons."""
    maximum_gripper_force: float = field(init=False, default=0.0)
    """Peak sum of physical fingertip normal forces in Newtons."""
    maximum_lift: float = field(init=False, default=0.0)
    """Largest observed increase of tube-root height in meters."""
    initial_height: float = field(init=False, default=0.0)
    """Tube-root height before the program starts."""
    destination_error: float = field(init=False, default=0.0)
    """Current Euclidean distance from the intended placement in meters."""
    released: bool = field(init=False, default=False)
    """Whether opening removed both fingertip contacts after a verified lift."""
    joint_corrections: dict[str, float] = field(init=False, default_factory=dict)
    """Integrated bounded arm offsets for measured placement alignment."""
    error: str | None = field(init=False, default=None)
    """The physical acceptance failure, if any."""

    FINGERTIPS: ClassVar[tuple[str, str]] = (
        "pr2/l_gripper_l_finger_tip_link",
        "pr2/l_gripper_r_finger_tip_link",
    )
    """The opposing collision bodies that must contact the tube."""

    TOOL_FRAME: ClassVar[str] = "pr2/l_gripper_tool_frame"
    """Rigid gripper frame whose Jacobian maps placement correction to arm joints."""

    def __post_init__(self) -> None:
        """Initialize evidence from the current physical scene."""
        self.reset()

    def reset(self) -> None:
        """Clear execution evidence and restore the first joint target."""
        self.state = ProgramState.IDLE
        self.phase = ProgramPhase.APPROACH
        self.reference_time = self.grasp_wait = self.contact_duration = (
            self.settling_duration
        ) = 0.0
        self.grasp_verified = self.holding = self.released = False
        self.gripper_force = self.maximum_gripper_force = self.maximum_lift = 0.0
        self.error = None
        self.joint_corrections.clear()
        self.initial_height = float(self.physics.data.xpos[self._object_id(), 2])
        self.destination_error = float(
            np.linalg.norm(
                self.physics.data.xpos[self._object_id()] - self.reference.destination
            )
        )
        self._set_targets(self.reference.sample(0.0))

    def _set_targets(self, targets: dict[str, float]) -> None:
        """Send only independently actuated joint intentions to the physical robot."""
        self.physics.set_joint_targets(
            {
                name: float(
                    np.clip(
                        value,
                        self.physics.robot_joints[name].lower,
                        self.physics.robot_joints[name].upper,
                    )
                )
                for name, value in targets.items()
                if name in self.physics.joint_targets
            }
        )

    def start(self) -> None:
        """Start an idle program or resume its paused joint reference."""
        if self.state not in (ProgramState.IDLE, ProgramState.PAUSED):
            return
        self.physics.release()
        self.state = ProgramState.RUNNING

    def pause(self) -> None:
        """Hold the current joint targets while contact physics continues."""
        if self.state == ProgramState.RUNNING:
            self.state = ProgramState.PAUSED

    def _object_id(self) -> int:
        """Resolve the physical free object's body index."""
        return self.physics.objects[self.reference.object_key].body_id

    def advance(self) -> None:
        """Advance intentions, waiting for bilateral contact before lifting."""
        if self.state != ProgramState.RUNNING:
            return
        timestep = self.physics.timestep
        if not self.grasp_verified and self.reference_time >= self.reference.grasp_time:
            self.phase = ProgramPhase.GRASP
            self.grasp_wait += timestep
            self.contact_duration = (
                self.contact_duration + timestep if self.holding else 0.0
            )
            if self.contact_duration >= self.parameters.grasp_confirmation_time:
                self.grasp_verified = True
            elif self.grasp_wait >= self.parameters.grasp_timeout:
                self._fail("No stable contact on both PR2 fingertips before lifting")
                return
        else:
            self.reference_time = min(
                self.reference.duration, self.reference_time + timestep
            )
            if not self.grasp_verified:
                self.reference_time = min(
                    self.reference_time, self.reference.grasp_time
                )
        if self.grasp_verified:
            self.phase = (
                ProgramPhase.TRANSPORT
                if self.reference_time < self.reference.release_time
                else ProgramPhase.RELEASE
            )
        if self.reference_time >= self.reference.duration:
            self.phase = ProgramPhase.SETTLE
            self.settling_duration += timestep
        targets = self.reference.sample(self.reference_time)
        self._align_placement()
        for name, correction in self.joint_corrections.items():
            targets[name] += correction
        finger = targets[JointReference.GRIPPER_JOINT]
        if self.reference_time > self.reference.grasp_time / 2:
            blend = np.clip(
                (
                    self.reference.gripper_position
                    + self.parameters.closure_blend
                    - finger
                )
                / self.parameters.closure_blend,
                0.0,
                1.0,
            )
            targets[JointReference.GRIPPER_JOINT] = max(
                0.0, finger - self.parameters.closure_offset * float(blend)
            )
        self._set_targets(targets)

    def _align_placement(self) -> None:
        """Correct measured tube position and tilt using bounded arm joint velocities."""
        if (
            not self.holding
            or self.reference_time
            < self.reference.release_time - self.parameters.alignment_lead_time
            or self.reference_time >= self.reference.release_time
        ):
            return
        names = [
            name
            for name in self.physics.joint_targets
            if name.startswith("pr2/l_") and "gripper" not in name
        ]
        indices = [
            int(self.physics.model.jnt_dofadr[self.physics.model.joint(name).id])
            for name in names
        ]
        position = self.physics.data.xpos[self._object_id()]
        rotation = self.physics.data.xmat[self._object_id()].reshape(3, 3)
        translation = np.asarray(self.reference.destination) - position
        translation[2] = 0.0
        angular = np.cross(rotation[:, 2], np.array([0.0, 0.0, 1.0]))
        velocity = self.parameters.alignment_gain * np.concatenate(
            (translation, angular)
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
            (position_jacobian[:, indices], rotation_jacobian[:, indices])
        )
        joint_velocity = jacobian.T @ np.linalg.solve(
            jacobian @ jacobian.T + self.parameters.jacobian_regularization * np.eye(6),
            velocity,
        )
        joint_velocity = np.clip(
            joint_velocity,
            -self.parameters.maximum_correction_speed,
            self.parameters.maximum_correction_speed,
        )
        for name, correction in zip(names, joint_velocity):
            self.joint_corrections[name] = float(
                np.clip(
                    self.joint_corrections.get(name, 0.0)
                    + correction * self.physics.timestep,
                    -self.parameters.maximum_correction,
                    self.parameters.maximum_correction,
                )
            )

    def observe(self) -> None:
        """Measure actual contacts, lift and release without changing object state."""
        object_id = self._object_id()
        position = self.physics.data.xpos[object_id]
        self.destination_error = float(
            np.linalg.norm(position - self.reference.destination)
        )
        if self.state == ProgramState.IDLE:
            return
        self.maximum_lift = max(
            self.maximum_lift, float(position[2]) - self.initial_height
        )
        forces = {name: 0.0 for name in self.FINGERTIPS}
        for index in range(self.physics.data.ncon):
            contact = self.physics.data.contact[index]
            first = int(self.physics.model.geom_bodyid[contact.geom1])
            second = int(self.physics.model.geom_bodyid[contact.geom2])
            if object_id not in (first, second) or contact.efc_address < 0:
                continue
            other = self.physics.model.body(
                second if first == object_id else first
            ).name
            if other not in forces:
                continue
            force = np.zeros(6)
            mujoco.mj_contactForce(self.physics.model, self.physics.data, index, force)
            forces[other] += max(0.0, float(force[0]))
        self.holding = all(
            force > self.parameters.contact_threshold for force in forces.values()
        )
        self.gripper_force = sum(forces.values())
        self.maximum_gripper_force = max(self.maximum_gripper_force, self.gripper_force)
        self.released = (
            self.grasp_verified
            and self.reference_time >= self.reference.release_time
            and self.gripper_force < self.parameters.contact_threshold
        )
        if (
            self.state == ProgramState.RUNNING
            and self.settling_duration >= self.parameters.settling_time
        ):
            self._finish()

    def _fail(self, reason: str) -> None:
        """Stop advancing the reference after a measured failure."""
        self.state = ProgramState.FAILED
        self.error = reason

    def _finish(self) -> None:
        """Require measured lift, release, placement and upright orientation."""
        if not self.grasp_verified or self.maximum_lift < self.parameters.minimum_lift:
            self._fail("The physical tube did not complete the required lift")
            return
        if not self.released:
            self._fail("The physical tube is still in contact with the gripper")
            return
        if self.destination_error > self.parameters.destination_tolerance:
            self._fail("The physical tube did not reach the intended rack position")
            return
        vertical = float(self.physics.data.xmat[self._object_id()].reshape(3, 3)[2, 2])
        if vertical < math.cos(self.parameters.upright_tolerance):
            self._fail("The physical tube did not settle upright")
            return
        self.state = ProgramState.SUCCEEDED
        self.phase = ProgramPhase.COMPLETE

    def snapshot(self) -> dict[str, Any]:
        """Report measured robot execution independently of recording completion."""
        return {
            ProgramField.STATE: self.state,
            ProgramField.PHASE: self.phase,
            ProgramField.PROGRESS: (
                self.reference_time / self.reference.duration
                if self.reference.duration
                else 1.0
            ),
            ProgramField.SOURCE: ProgramField.SOURCE_RECORDING,
            ProgramField.GRASP_VERIFIED: self.grasp_verified,
            ProgramField.HOLDING: self.holding,
            ProgramField.MAXIMUM_LIFT: self.maximum_lift,
            ProgramField.MAXIMUM_GRIPPER_FORCE: self.maximum_gripper_force,
            ProgramField.GRIPPER_FORCE: self.gripper_force,
            ProgramField.DESTINATION_ERROR: self.destination_error,
            ProgramField.RELEASED: self.released,
            ProgramField.ERROR: self.error,
            ProgramField.DURATION: self.reference.duration,
            ProgramField.TIME: self.reference_time,
        }


# %% serialized session adapter
@dataclass
class LaboratoryProgramPhysics:
    """Combine articulated dynamics and a resumable contact-verified program."""

    physics: LaboratoryRobotPhysics
    """The articulated MuJoCo contact simulation."""
    recording_directory: Path
    """The native CRAMERA recording supplying joint intentions."""
    program: LaboratoryPhysicsProgram | LaboratoryMixingProgram = field(init=False)
    """The controller and physical acceptance evidence."""

    def __post_init__(self) -> None:
        """Load one program for the articulated physical world."""
        self.program = LaboratoryPhysicsProgram(
            self.physics, JointReference.load(self.recording_directory)
        )

    @property
    def timestep(self) -> float:
        """Integration interval of the physical world."""
        return self.physics.timestep

    def step(self, count: int = 1) -> None:
        """Interleave joint intentions with authoritative contact integration."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("Physics step count must be a positive integer")
        for _ in range(count):
            self.program.advance()
            self.physics.step()
            self.program.observe()

    def reset(self) -> None:
        """Restore physical state and discard all previous acceptance evidence."""
        self.physics.reset()
        self.program.reset()

    def run_program(self) -> None:
        """Resume or restore initial body poses while retaining current tube contents."""
        if not isinstance(self.program, LaboratoryPhysicsProgram):
            self._require_switchable()
            self.program = LaboratoryPhysicsProgram(
                self.physics, JointReference.load(self.recording_directory)
            )
        if self.program.state in (
            ProgramState.IDLE,
            ProgramState.SUCCEEDED,
            ProgramState.FAILED,
        ):
            contents = self.physics.liquid.snapshot()[LiquidField.TUBES]
            self.reset()
            for key, tube in contents.items():
                self.physics.fill_liquid(key, tube[LiquidField.VOLUME])
                self.physics.liquid.tubes[key].color[:] = tube[LiquidField.COLOR]
        self.program.start()

    def _require_switchable(self) -> None:
        """Require the active robot program to yield before replacing its controller."""
        if self.program.state == ProgramState.RUNNING:
            raise InvalidPhysicsTarget("Pause the active PR2 program before switching")

    def run_mixing(self) -> None:
        """Start the fixed mixing recipe or resume its current physical state."""
        if not isinstance(self.program, LaboratoryPhysicsProgram):
            if self.program.state == ProgramState.PAUSED:
                self.program.start()
                return
            if self.program.state == ProgramState.RUNNING:
                return
        self._require_switchable()
        self.physics.reset()
        self.program = importlib.import_module(
            "cramera.laboratory_mixing_program"
        ).LaboratoryMixingProgram(self.physics)
        self.program.start()

    def pause_program(self) -> None:
        """Pause the reference while physical gravity and contacts remain active."""
        self.program.pause()

    def set_target(
        self, key: str, position: list[float], orientation: list[float] | None = None
    ) -> None:
        """Apply manual forces only while the robot program is not advancing."""
        if self.program.state == ProgramState.RUNNING:
            raise InvalidPhysicsTarget(
                "Pause the PR2 program before moving objects manually"
            )
        self.physics.set_target(key, position, orientation=orientation)

    def fill_liquid(self, key: str, volume_ml: float) -> None:
        """Allow fill changes only while the reference robot program is paused or idle."""
        if self.program.state == ProgramState.RUNNING:
            raise InvalidPhysicsTarget("Pause the PR2 program before filling tubes")
        self.physics.fill_liquid(key, volume_ml)

    def tare_scale(self) -> None:
        """Zero the physical scale while the robot controller is inactive."""
        if self.program.state == ProgramState.RUNNING:
            raise InvalidPhysicsTarget("Pause the PR2 program before zeroing the scale")
        self.physics.tare_scale()

    def release(self) -> None:
        """Stop advancing robot intentions and remove any manual force target."""
        self.program.pause()
        self.physics.release()

    def snapshot(self) -> dict[str, Any]:
        """Combine physical object and joint poses with measured program feedback."""
        snapshot = self.physics.snapshot()
        snapshot[ProgramField.ROBOT] = self.program.snapshot()
        return snapshot
