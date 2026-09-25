"""Own a physical PR2 and its native motion reference in one contact laboratory."""

from __future__ import annotations

import copy
import importlib
import json
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import Any, ClassVar, Protocol

from cramera import paths
from cramera.laboratory_physics_session import (
    ContactSimulation,
    InvalidPhysicsRequest,
    LaboratoryPhysicsSession,
    PhysicsField,
    PhysicsFile,
    PhysicsState,
)


# %% robot API contract
class RobotPhysicsRoute(StrEnum):
    """Isolated endpoints for the PR2 contact world."""

    START = "/api/laboratory/physics/robot/start"
    """Start the robot contact simulation."""
    STATE = "/api/laboratory/physics/robot/state"
    """Read measured joints, objects and execution state."""
    TARGET = "/api/laboratory/physics/robot/target"
    """Manipulate a loose object while the robot program is inactive."""
    LIQUID = "/api/laboratory/physics/robot/liquid"
    """Set a tube's liquid quantity while the robot program is inactive."""
    SCALE_TARE = "/api/laboratory/physics/robot/scale/tare"
    """Zero the laboratory scale while the robot program is paused or idle."""
    RELEASE = "/api/laboratory/physics/robot/release"
    """Remove the manual manipulation force and pause the robot reference."""
    RESET = "/api/laboratory/physics/robot/reset"
    """Restore the robot, objects and program."""
    STOP = "/api/laboratory/physics/robot/stop"
    """Terminate the robot contact worker."""
    RUN = "/api/laboratory/physics/robot/run"
    """Execute the saved native joint targets through physical actuators."""
    MIX = "/api/laboratory/physics/robot/mix"
    """Pour two source liquids, swirl the receiver and place it in A2."""
    PAUSE = "/api/laboratory/physics/robot/pause"
    """Pause advancement of the robot program."""


class RobotField(StrEnum):
    """Robot-specific scene and snapshot fields."""

    ROBOT = "robot"
    """Robot description or measured program status."""
    ROBOTS = "robots"
    """Optional robot catalog in the recorded scene."""
    MODELS = "models"
    """Models rendered in the laboratory."""
    URDF = "urdf"
    """Robot description relative to the generated bundle."""
    SOURCE = "source"
    """Origin of the motor setpoints."""


class ProgramSimulation(ContactSimulation, Protocol):
    """A contact engine that also advances a bounded robot program."""

    def run_program(self) -> None:
        """Start or resume the reference motor targets."""

    def run_mixing(self) -> None:
        """Start or resume the fixed two-liquid mixing recipe."""

    def pause_program(self) -> None:
        """Hold the current robot program stage."""


def create_robot_physics(
    bundle_directory: Path, recording_directory: Path
) -> ProgramSimulation:
    """Load the robot engine and controller only when their scene is requested."""
    model = importlib.import_module(
        "cramera.laboratory_physics_robot"
    ).LaboratoryRobotPhysics(
        bundle_directory=bundle_directory, robot_directory=recording_directory
    )
    return importlib.import_module(
        "cramera.laboratory_physics_program"
    ).LaboratoryProgramPhysics(physics=model, recording_directory=recording_directory)


# %% isolated world and presentation
@dataclass
class LaboratoryRobotSession(LaboratoryPhysicsSession):
    """Serialize physical robot execution and independent manual object manipulation."""

    physics: ProgramSimulation | None = field(default=None, init=False)
    """The contact engine and its robot program."""
    recording_directory: Path | None = field(default=None, init=False)
    """Resolved native PR2 reference bundle."""

    RECORDING_SCENE: ClassVar[str] = "precision_lab_pr2"
    """Source of robot visuals and the native joint reference."""
    SCENE_NAME: ClassVar[str] = "precision_lab_pr2_physics"
    """Generated scene containing both robot and laboratory contacts."""
    VIEWER_URL: ClassVar[str] = f"/index.html?scene={SCENE_NAME}&layout=scene&offline=1"
    """Viewer isolated from the native robot live bridge."""
    ROBOT_ASSETS: ClassVar[str] = "robot"
    """Subdirectory preserving robot mesh-relative paths."""
    REFERENCE_SOURCE: ClassVar[str] = "recorded_cram_joint_targets"
    """Explicit provenance of the motor commands."""

    def _create_physics(self, directory: Path) -> ProgramSimulation:
        """Construct the robot contact world from the existing native reference."""
        self.recording_directory = paths.resolve_scene_directory(self.RECORDING_SCENE)
        if self.recording_directory is None:
            raise FileNotFoundError(
                "Zuerst den nativen PR2-Laborauftrag ausführen, um die Bewegungsreferenz zu erzeugen."
            )
        return create_robot_physics(
            bundle_directory=directory, recording_directory=self.recording_directory
        )

    def _write_scene(self, source_directory: Path) -> None:
        """Combine authored laboratory visuals with the physical robot's measured pose."""
        super()._write_scene(source_directory)
        robot_directory = self.output_directory / self.ROBOT_ASSETS
        shutil.copytree(
            self.recording_directory,
            robot_directory,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                *self.EXCLUDED_ASSETS, "trajectory.json", "acceptance_metrics.json"
            ),
        )
        source = json.loads((self.recording_directory / PhysicsFile.SCENE).read_text())
        scene_path = self.output_directory / PhysicsFile.SCENE
        scene = json.loads(scene_path.read_text())
        for item in source[RobotField.MODELS]:
            if not item.get(RobotField.ROBOT):
                continue
            robot = copy.deepcopy(item)
            robot[RobotField.URDF] = (
                Path(self.ROBOT_ASSETS) / robot[RobotField.URDF]
            ).as_posix()
            scene[RobotField.MODELS].append(robot)
        for key in (RobotField.ROBOT, RobotField.ROBOTS):
            if key in source:
                scene[key] = source[key]
        scene[PhysicsField.TASK] = "PR2 · Kontaktphysik"
        scene[PhysicsField.PHYSICS][RobotField.ROBOT] = True
        scene[PhysicsField.VALIDATION][RobotField.SOURCE] = self.REFERENCE_SOURCE
        scene_path.write_text(json.dumps(scene, indent=2))
        trajectory_path = self.output_directory / PhysicsFile.TRAJECTORY
        trajectory = json.loads(trajectory_path.read_text())
        trajectory[PhysicsField.FRAMES] = [self.physics.snapshot()[PhysicsField.FRAMES]]
        trajectory_path.write_text(json.dumps(trajectory, indent=2))

    def _validate_target(self, key: str, position: list[float]) -> None:
        """Allow manual targets only when the robot program is not advancing."""
        super()._validate_target(key, position)
        if (
            self.physics.snapshot()[RobotField.ROBOT][PhysicsField.STATE]
            == PhysicsState.RUNNING
        ):
            raise InvalidPhysicsRequest(
                "Den PR2-Ablauf vor dem manuellen Bewegen pausieren."
            )

    def run_program(self) -> dict[str, Any]:
        """Transfer manipulation control from the cursor to the robot program."""
        with self.lock:
            self._require_running()
            self.physics.run_program()
            return self._status()

    def run_mixing(self) -> dict[str, Any]:
        """Transfer manipulation control to the fixed mixing recipe."""
        with self.lock:
            self._require_running()
            self.physics.run_mixing()
            return self._status()

    def _validate_liquid(self, key: str, volume_ml: float) -> None:
        """Require the robot program to pause before changing liquid quantities."""
        super()._validate_liquid(key, volume_ml)
        if (
            self.physics.snapshot()[RobotField.ROBOT][PhysicsField.STATE]
            == PhysicsState.RUNNING
        ):
            raise InvalidPhysicsRequest("Den PR2-Ablauf vor dem Befüllen pausieren.")

    def _validate_scale(self) -> None:
        """Require the robot program to pause before zeroing the scale."""
        if (
            self.physics.snapshot()[RobotField.ROBOT][PhysicsField.STATE]
            == PhysicsState.RUNNING
        ):
            raise InvalidPhysicsRequest("Den PR2-Ablauf vor dem Tarieren pausieren.")

    def pause_program(self) -> dict[str, Any]:
        """Pause the reference while physics continues to calculate contacts."""
        with self.lock:
            self._require_running()
            self.physics.pause_program()
            return self._status()

    def _stop_physics(self) -> None:
        """Pause motor commands and remove manual forces after worker termination."""
        self.physics.pause_program()
        super()._stop_physics()
