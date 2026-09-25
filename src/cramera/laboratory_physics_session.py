"""Own the local contact laboratory, its scene bundle, and fixed-step worker."""

from __future__ import annotations

import copy
import importlib
import json
import math
import shutil
import threading
import time
from _thread import LockType
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing_extensions import Any, ClassVar, Protocol

from cramera import paths


# %% contact session contract
class PhysicsRoute(StrEnum):
    """Local HTTP routes for the contact laboratory."""

    START = "/api/laboratory/physics/start"
    """Create the isolated scene and begin simulation."""
    STATE = "/api/laboratory/physics/state"
    """Read object positions and current contacts."""
    TARGET = "/api/laboratory/physics/target"
    """Move the force target for one authored object."""
    LIQUID = "/api/laboratory/physics/liquid"
    """Set the liquid quantity in an authored tube."""
    SCALE_TARE = "/api/laboratory/physics/scale/tare"
    """Use the current scale load as the zero reference."""
    RELEASE = "/api/laboratory/physics/release"
    """Remove the active manipulation force."""
    RESET = "/api/laboratory/physics/reset"
    """Restore the initial object poses."""
    STOP = "/api/laboratory/physics/stop"
    """Stop the owned simulation worker."""


class PhysicsState(StrEnum):
    """Observable lifecycle states of a local contact simulation."""

    IDLE = "idle"
    """No simulation worker is active."""
    RUNNING = "running"
    """The simulation advances in real time."""
    FAILED = "failed"
    """The engine could not start or continue."""


class PhysicsField(StrEnum):
    """Fields shared by scene metadata, HTTP requests, and physics snapshots."""

    OK = "ok"
    """Whether the session is usable."""
    STATE = "state"
    """The worker lifecycle state."""
    ERROR = "error"
    """A startup or simulation failure explanation."""
    VIEWER_URL = "viewerUrl"
    """The isolated contact laboratory viewer."""
    KEY = "key"
    """An authored object identifier."""
    POSITION = "position"
    """A desired position in world coordinates."""
    ORIENTATION = "orientation"
    """A desired XYZW rotation quaternion."""
    VOLUME_ML = "volumeMl"
    """A requested liquid quantity in millilitres."""
    NAME = "name"
    """The scene bundle name."""
    TASK = "task"
    """The short activity description shown in the viewer."""
    LABEL = "label"
    """The display name of an object."""
    OBJECTS = "objects"
    """The authored object catalog or simulated pose mapping."""
    LABORATORY = "laboratory"
    """Manual manipulation metadata incompatible with force-based movement."""
    CAMERAS = "cameras"
    """Authored overview and inspection camera presets."""
    PHYSICS = "physics"
    """The contact laboratory controls and bounds."""
    SCALE = "scale"
    """The laboratory scale placement or measured load."""
    BOUNDS = "bounds"
    """The permitted target range in world coordinates."""
    MINIMUM = "min"
    """The minimum allowed target coordinates."""
    MAXIMUM = "max"
    """The maximum allowed target coordinates."""
    VALIDATION = "validation"
    """The scene's simulation capability declaration."""
    MODE = "mode"
    """The manipulation mode used by the scene."""
    FRAMES = "frames"
    """The initial fixed joint positions."""
    FRAMES_PER_SECOND = "framesPerSecond"
    """The static bundle's initial frame rate."""
    TRAJECTORY = "trajectory"
    """The relative initial pose file."""


class PhysicsFile(StrEnum):
    """Files in the independent contact laboratory bundle."""

    SCENE = "scene.json"
    """The authored scene description."""
    TRAJECTORY = "trajectory.json"
    """The static initial object and joint poses."""


class InvalidPhysicsRequest(ValueError):
    """A request names an invalid object, target, or argument."""


class PhysicsSessionInactive(RuntimeError):
    """A manipulation was requested without a running simulation."""


class ContactSimulation(Protocol):
    """The engine operations serialized by a contact session."""

    timestep: float
    """The fixed integration interval in seconds."""

    def step(self, count: int = 1) -> None:
        """Advance a fixed number of integration steps."""

    def reset(self) -> None:
        """Restore the initial simulation state."""

    def set_target(
        self, key: str, position: list[float], orientation: list[float] | None = None
    ) -> None:
        """Set a force-driven pose target for one authored object."""

    def fill_liquid(self, key: str, volume_ml: float) -> None:
        """Set a bounded liquid quantity for an authored tube."""

    def tare_scale(self) -> None:
        """Use the current stable scale load as the zero reference."""

    def release(self) -> None:
        """Remove the active force-driven target."""

    def snapshot(self) -> dict[str, Any]:
        """Read object poses, target, and contact forces."""


def create_physics(bundle_directory: Path) -> ContactSimulation:
    """Load the optional physics engine only when a contact session starts."""
    return importlib.import_module("cramera.laboratory_physics").LaboratoryPhysics(
        bundle_directory=bundle_directory
    )


# %% session ownership
@dataclass
class LaboratoryPhysicsSession:
    """Serialize manipulation and simulation for an independently copied laboratory."""

    data_directory: Path
    """The local CRAMERA artifact directory."""
    physics: ContactSimulation | None = field(default=None, init=False)
    """The lazily initialized contact engine."""
    worker: threading.Thread | None = field(default=None, init=False)
    """The sole fixed-step simulation worker."""
    lock: LockType = field(default_factory=threading.Lock, init=False, repr=False)
    """Serialize all engine operations and scene initialization."""
    lifecycle_lock: LockType = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    """Prevent a new worker from starting while its predecessor is being joined."""
    stopped: threading.Event = field(default_factory=threading.Event, init=False)
    """Signal the simulation worker to terminate."""
    error: str | None = field(default=None, init=False)
    """The latest startup or worker failure."""
    object_keys: set[str] = field(default_factory=set, init=False)
    """The manipulable authored object identifiers."""

    SOURCE_SCENE: ClassVar[str] = "precision_lab"
    """The immutable authored laboratory scene."""
    SCENE_NAME: ClassVar[str] = "precision_lab_physics"
    """The independent contact laboratory bundle."""
    VIEWER_URL: ClassVar[str] = f"/index.html?scene={SCENE_NAME}&layout=scene&offline=1"
    """The viewer URL isolated from the robot live bridge."""
    TARGET_MINIMUM: ClassVar[tuple[float, float, float]] = (-0.8, -0.65, 0.8)
    """The minimum permitted manipulation target coordinates."""
    TARGET_MAXIMUM: ClassVar[tuple[float, float, float]] = (0.8, 0.45, 1.6)
    """The maximum permitted manipulation target coordinates."""
    WORKER_INTERVAL: ClassVar[float] = 0.005
    """The maximum wait between batches of physics integration."""
    MAXIMUM_CATCHUP: ClassVar[float] = 0.1
    """The largest wall-time interval integrated after a stalled worker."""
    EXCLUDED_ASSETS: ClassVar[tuple[str, ...]] = (
        "*.blend",
        "*.blend1",
        "*.blend2",
        "render_*.png",
    )
    """Authoring and reference renders unnecessary for the browser scene."""
    MODE: ClassVar[str] = "contact_physics"
    """The generated scene's force-driven manipulation mode."""

    @property
    def output_directory(self) -> Path:
        """The separate generated scene directory."""
        return self.data_directory / "scenes" / self.SCENE_NAME

    def start(self) -> dict[str, Any]:
        """Start one simulation, preserving an already running contact session."""
        with self.lifecycle_lock, self.lock:
            if self.worker is not None and self.worker.is_alive():
                return self._status()
            self.error = None
            self.physics = None
            try:
                directory = paths.resolve_scene_directory(self.SOURCE_SCENE)
                if directory is None:
                    raise FileNotFoundError(
                        "The authored precision laboratory is missing"
                    )
                self.physics = self._create_physics(directory)
                self._write_scene(directory)
                self.stopped.clear()
                self.worker = threading.Thread(
                    target=self._run, name=self.SCENE_NAME, daemon=True
                )
                self.worker.start()
            except Exception as error:
                self.error = str(error)
                self.physics = None
                self.stopped.set()
            return self._status()

    def _create_physics(self, directory: Path) -> ContactSimulation:
        """Construct the engine for this session's authored laboratory."""
        return create_physics(directory)

    def status(self) -> dict[str, Any]:
        """Read a consistent snapshot while the worker may be stepping."""
        with self.lock:
            return self._status()

    def set_target(
        self, key: str, position: list[float], orientation: list[float] | None = None
    ) -> dict[str, Any]:
        """Apply a bounded pose target to a known object in the running simulation."""
        with self.lock:
            self._require_running()
            self._validate_target(key, position)
            if orientation is None:
                self.physics.set_target(key, position.copy())
            else:
                self._validate_orientation(orientation)
                self.physics.set_target(
                    key, position.copy(), orientation=orientation.copy()
                )
            return self._status()

    @staticmethod
    def _validate_orientation(orientation: list[float]) -> None:
        """Reject malformed or zero-length quaternion targets before engine mutation."""
        if (
            not isinstance(orientation, list)
            or len(orientation) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in orientation
            )
        ):
            raise InvalidPhysicsRequest("Expected four finite quaternion coordinates")
        length = math.hypot(*orientation)
        if not math.isfinite(length) or length < 1e-8:
            raise InvalidPhysicsRequest("Expected a nonzero orientation quaternion")

    def fill_liquid(self, key: str, volume_ml: float) -> dict[str, Any]:
        """Change the contained volume while serializing against physical stepping."""
        with self.lock:
            self._require_running()
            self._validate_liquid(key, volume_ml)
            self.physics.fill_liquid(key, volume_ml)
            return self._status()

    def _validate_liquid(self, key: str, volume_ml: float) -> None:
        """Reject unknown objects and malformed quantities before engine validation."""
        if not isinstance(key, str) or key not in self.object_keys:
            raise InvalidPhysicsRequest("Unknown laboratory object")
        if (
            isinstance(volume_ml, bool)
            or not isinstance(volume_ml, (int, float))
            or not math.isfinite(volume_ml)
            or volume_ml < 0
        ):
            raise InvalidPhysicsRequest("Expected a nonnegative finite liquid volume")

    def tare_scale(self) -> dict[str, Any]:
        """Zero the scale while serializing against physical stepping."""
        with self.lock:
            self._require_running()
            self._validate_scale()
            self.physics.tare_scale()
            return self._status()

    def _validate_scale(self) -> None:
        """Allow zeroing while manual manipulation owns the simulation."""

    def _validate_target(self, key: str, position: list[float]) -> None:
        """Reject unknown objects and targets outside the manipulation bounds."""
        if not isinstance(key, str) or key not in self.object_keys:
            raise InvalidPhysicsRequest("Unknown laboratory object")
        if not isinstance(position, list) or len(position) != 3:
            raise InvalidPhysicsRequest("Expected three target coordinates")
        for value, minimum, maximum in zip(
            position, self.TARGET_MINIMUM, self.TARGET_MAXIMUM
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise InvalidPhysicsRequest("Target is outside the laboratory bounds")

    def release(self) -> dict[str, Any]:
        """Remove the active manipulation force from a running simulation."""
        with self.lock:
            self._require_running()
            self.physics.release()
            return self._status()

    def reset(self) -> dict[str, Any]:
        """Restore the authored initial object states without replacing the worker."""
        with self.lock:
            self._require_running()
            self.physics.reset()
            return self._status()

    def stop(self) -> dict[str, Any]:
        """Terminate and join the simulation worker before returning an idle state."""
        with self.lifecycle_lock:
            with self.lock:
                self.stopped.set()
                worker = self.worker
            if worker is not None and worker is not threading.current_thread():
                worker.join()
            with self.lock:
                self.worker = None
                if self.physics is not None:
                    self._stop_physics()
                return self._status()

    def _stop_physics(self) -> None:
        """Remove active control after the stepping worker has stopped."""
        self.physics.release()

    def _require_running(self) -> None:
        """Reject manipulation until a working simulation owns the scene."""
        if (
            self.physics is None
            or self.worker is None
            or not self.worker.is_alive()
            or self.stopped.is_set()
        ):
            raise PhysicsSessionInactive("Start the laboratory physics session first")

    def _status(self) -> dict[str, Any]:
        """Return detached engine data while the caller holds the session lock."""
        state = PhysicsState.IDLE
        if self.error:
            state = PhysicsState.FAILED
        elif (
            self.worker is not None
            and self.worker.is_alive()
            and not self.stopped.is_set()
        ):
            state = PhysicsState.RUNNING
        answer = (
            copy.deepcopy(self.physics.snapshot()) if self.physics is not None else {}
        )
        answer.update(
            {
                PhysicsField.OK: self.error is None,
                PhysicsField.STATE: state,
                PhysicsField.VIEWER_URL: self.VIEWER_URL,
            }
        )
        if self.error:
            answer[PhysicsField.ERROR] = self.error
        return answer

    def _write_scene(self, source_directory: Path) -> None:
        """Copy browser assets and replace manual controls with contact metadata."""
        scene = json.loads((source_directory / PhysicsFile.SCENE).read_text())
        self.object_keys = {
            item[PhysicsField.KEY] for item in scene[PhysicsField.OBJECTS]
        }
        scene[PhysicsField.NAME] = self.SCENE_NAME
        scene[PhysicsField.TASK] = "Laborbank · Kontaktphysik"
        laboratory = scene.pop(PhysicsField.LABORATORY, {})
        scene[PhysicsField.PHYSICS] = {
            PhysicsField.OBJECTS: [
                {
                    PhysicsField.KEY: item[PhysicsField.KEY],
                    PhysicsField.LABEL: item.get(
                        PhysicsField.LABEL, item[PhysicsField.KEY]
                    ),
                }
                for item in scene[PhysicsField.OBJECTS]
            ],
            PhysicsField.BOUNDS: {
                PhysicsField.MINIMUM: list(self.TARGET_MINIMUM),
                PhysicsField.MAXIMUM: list(self.TARGET_MAXIMUM),
            },
            PhysicsField.CAMERAS: laboratory.get(PhysicsField.CAMERAS, {}),
        }
        if PhysicsField.SCALE in laboratory:
            scene[PhysicsField.PHYSICS][PhysicsField.SCALE] = laboratory[
                PhysicsField.SCALE
            ]
        scene[PhysicsField.VALIDATION] = {PhysicsField.MODE: self.MODE}
        scene[PhysicsField.FRAMES_PER_SECOND] = 1
        scene[PhysicsField.TRAJECTORY] = PhysicsFile.TRAJECTORY
        shutil.copytree(
            source_directory,
            self.output_directory,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*self.EXCLUDED_ASSETS),
        )
        original_trajectory = json.loads(
            (source_directory / PhysicsFile.TRAJECTORY).read_text()
        )
        trajectory = {
            PhysicsField.FRAMES_PER_SECOND: 1,
            PhysicsField.FRAMES: original_trajectory.get(PhysicsField.FRAMES, [{}])[:1],
            PhysicsField.OBJECTS: [self.physics.snapshot()[PhysicsField.OBJECTS]],
        }
        (self.output_directory / PhysicsFile.TRAJECTORY).write_text(
            json.dumps(trajectory, indent=2)
        )
        (self.output_directory / PhysicsFile.SCENE).write_text(
            json.dumps(scene, indent=2)
        )

    def _run(self) -> None:
        """Advance fixed steps according to elapsed wall time until stopped."""
        previous = time.monotonic()
        remainder = 0.0
        try:
            while not self.stopped.wait(self.WORKER_INTERVAL):
                now = time.monotonic()
                remainder += min(now - previous, self.MAXIMUM_CATCHUP)
                previous = now
                with self.lock:
                    count = int(remainder / self.physics.timestep)
                    if count:
                        self.physics.step(count)
                        remainder -= count * self.physics.timestep
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.stopped.set()
