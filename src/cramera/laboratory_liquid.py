"""Conservative reduced free surfaces and ballistic pouring in laboratory tubes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil, pi, sqrt
from numbers import Real

import numpy as np
from scipy.spatial.transform import Rotation


# %% wire vocabulary
class LiquidField(StrEnum):
    """Scene and snapshot fields shared with the liquid renderer."""

    MODEL = "model"
    TUBES = "tubes"
    OBJECTS = "objects"
    KEY = "key"
    SPAWN = "spawn"
    LIQUID = "liquid"
    VOLUME = "volumeMl"
    CAPACITY = "capacityMl"
    COLOR = "color"
    NORMAL = "normal"
    OFFSET = "offset"
    RADIUS = "radius"
    BOTTOM = "bottom"
    ROUNDING_RADIUS = "roundingRadius"
    RIM = "rim"
    DROPLETS = "droplets"
    PUDDLES = "puddles"
    POSITION = "position"
    TOTAL = "totalMl"
    SPILLED = "spilledMl"
    IN_FLIGHT = "inFlightMl"
    INITIAL = "initialMl"
    SOURCE = "source"
    TIME = "time"
    EMITTED_AT = "emittedAt"
    VELOCITY = "velocity"


class LiquidModel(StrEnum):
    """Numerical model identity exposed to clients."""

    REDUCED_FREE_SURFACE = "reduced_free_surface"


class TubeKey(StrEnum):
    """Authored containers with open cylindrical interiors."""

    CLEAR = "tube_clear"
    AMBER = "tube_amber"
    TEAL = "tube_teal"


@dataclass
class InvalidLiquidInput(ValueError):
    """A liquid command violates the finite geometry or volume bounds."""

    reason: str
    """Description of the invalid input."""

    def __str__(self) -> str:
        """Expose the validation reason to an API client."""
        return self.reason


# %% vessel geometry
@dataclass
class TubeInterior:
    """Rounded closed bottom and open circular rim, expressed in tube coordinates."""

    radius: float = 0.0077
    """Inner cylindrical radius in meters."""
    bottom: float = 0.0013
    """Lowest inner point along the local positive tube axis."""
    rim: float = 0.149
    """Height of the open rim along the local tube axis."""
    integration_order: int = 64
    """Gauss integration nodes per axial section."""
    milliliters_per_cubic_meter: float = 1_000_000.0
    """Volume conversion for external milliliter values."""
    heights: np.ndarray = field(init=False, repr=False)
    """Cross-section heights used to integrate a tilted liquid surface."""
    radii: np.ndarray = field(init=False, repr=False)
    """Interior radius at each integration height."""
    weights: np.ndarray = field(init=False, repr=False)
    """Cross-section volume integration weights in milliliters."""

    def __post_init__(self) -> None:
        """Prepare bounded quadrature for the rounded base and straight wall."""
        nodes, weights = np.polynomial.legendre.leggauss(self.integration_order)
        bottom_heights = self.bottom + (nodes + 1) * self.radius / 2
        cylinder_heights = self.center + (nodes + 1) * (self.rim - self.center) / 2
        self.heights = np.concatenate((bottom_heights, cylinder_heights))
        self.radii = np.concatenate(
            (
                np.sqrt(self.radius**2 - (bottom_heights - self.center) ** 2),
                np.full_like(nodes, self.radius),
            )
        )
        lengths = np.concatenate(
            (weights * self.radius / 2, weights * (self.rim - self.center) / 2)
        )
        self.weights = lengths * pi * self.radii**2 * self.milliliters_per_cubic_meter

    @property
    def center(self) -> float:
        """Return the sphere center of the rounded bottom."""
        return self.bottom + self.radius

    @property
    def capacity_ml(self) -> float:
        """Return the complete inner volume up to the open rim."""
        return (
            pi * self.radius**2 * (self.rim - self.center) + 2 * pi * self.radius**3 / 3
        ) * self.milliliters_per_cubic_meter

    def upright_volume(self, height: float) -> float:
        """Return the volume below a horizontal local height."""
        cap_height = min(max(height - self.bottom, 0.0), self.radius)
        cap = pi * cap_height**2 * (self.radius - cap_height / 3)
        cylinder = pi * self.radius**2 * max(0.0, min(height, self.rim) - self.center)
        return (cap + cylinder) * self.milliliters_per_cubic_meter

    def volume_below(self, normal: np.ndarray, offset: float) -> float:
        """Integrate the interior satisfying ``normal dot point <= offset``."""
        horizontal = float(np.linalg.norm(normal[:2]))
        if horizontal < 1e-10:
            if normal[2] >= 0:
                return self.upright_volume(offset)
            return self.capacity_ml - self.upright_volume(-offset)
        cut = np.clip(
            (offset - normal[2] * self.heights) / (horizontal * self.radii), -1.0, 1.0
        )
        fractions = (
            np.arcsin(cut) + cut * np.sqrt(np.maximum(0.0, 1 - cut**2)) + pi / 2
        ) / pi
        return float(self.weights @ fractions)

    def surface_offset(self, normal: np.ndarray, volume_ml: float) -> float:
        """Locate the volume-conserving free-surface plane."""
        horizontal = float(np.linalg.norm(normal[:2]))
        lower = (
            min(normal[2] * self.bottom, normal[2] * self.rim)
            - horizontal * self.radius
        )
        upper = (
            max(normal[2] * self.bottom, normal[2] * self.rim)
            + horizontal * self.radius
        )
        if volume_ml <= 0:
            return float(lower)
        if volume_ml >= self.capacity_ml:
            return float(upper)
        for _ in range(28):
            middle = (lower + upper) / 2
            if self.volume_below(normal, middle) < volume_ml:
                lower = middle
            else:
                upper = middle
        return float((lower + upper) / 2)

    def lowest_lip(self, normal: np.ndarray) -> np.ndarray:
        """Return the first open rim point reached by a rising free surface."""
        horizontal = float(np.linalg.norm(normal[:2]))
        radial = (
            -self.radius * normal[:2] / horizontal if horizontal > 1e-8 else np.zeros(2)
        )
        return np.array([*radial, self.rim])


@dataclass(frozen=True)
class TubePose:
    """Validated rigid placement of a tube in the world."""

    world_T_tube: np.ndarray
    """Homogeneous transform mapping tube points into world coordinates."""

    @classmethod
    def from_values(cls, values: list[float]) -> TubePose:
        """Validate an XYZ position and an XYZW quaternion."""
        if (
            not isinstance(values, (list, tuple, np.ndarray))
            or len(values) != 7
            or any(
                isinstance(value, (bool, np.bool_))
                or not isinstance(value, Real)
                or not np.isfinite(value)
                for value in values
            )
        ):
            raise InvalidLiquidInput("A tube pose must contain seven finite numbers.")
        quaternion = np.asarray(values[3:], dtype=float)
        if np.linalg.norm(quaternion) < 1e-10:
            raise InvalidLiquidInput("A tube quaternion must have nonzero length.")
        transform = np.eye(4)
        transform[:3, :3] = Rotation.from_quat(quaternion).as_matrix()
        transform[:3, 3] = values[:3]
        return cls(transform)

    def world_point(self, local: np.ndarray) -> np.ndarray:
        """Transform a local point into world coordinates."""
        return self.world_T_tube[:3, :3] @ local + self.world_T_tube[:3, 3]

    def local_point(self, world: np.ndarray) -> np.ndarray:
        """Transform a world point into tube coordinates."""
        return self.world_T_tube[:3, :3].T @ (world - self.world_T_tube[:3, 3])


# %% liquid state
@dataclass(frozen=True)
class LiquidParameters:
    """Numerical and reduced-flow settings with SI units."""

    gravity: float = 9.81
    """Downward acceleration in meters per second squared."""
    maximum_step: float = 0.02
    """Maximum integration substep in seconds."""
    maximum_duration: float = 1.0
    """Largest accepted public integration duration in seconds."""
    slosh_frequency: float = 14.0
    """Angular frequency of the damped free-surface slope oscillator."""
    slosh_damping: float = 5.0
    """Decay coefficient of free-surface slope velocity."""
    acceleration_limit: float = 20.0
    """Maximum excitation acceleration on each world axis."""
    velocity_limit: float = 2.0
    """Maximum inherited packet velocity in meters per second."""
    slope_limit: float = 1.5
    """Maximum free-surface slope relative to vertical."""
    discharge: float = 0.32
    """Reduced effective opening coefficient for overflow."""
    worktop_height: float = 0.9
    """Horizontal worktop contact plane in world coordinates."""
    worktop_half_width: float = 0.9
    """World X half extent of the worktop."""
    worktop_half_depth: float = 0.375
    """World Y half extent of the worktop."""
    puddle_merge_distance: float = 0.03
    """Maximum planar distance for merging landed packets."""
    puddle_offset: float = 0.0005
    """Visible puddle offset above its support plane."""


@dataclass
class TubeLiquid:
    """Contained volume and inertial free-surface state for one tube."""

    key: str
    """Authored body identifier."""
    volume_ml: float
    """Current contained volume in milliliters."""
    color: np.ndarray
    """Linear RGB tint mixed by volume on receipt."""
    pose: TubePose
    """Current authoritative rigid-body pose."""
    normal: np.ndarray
    """Upward free-surface normal in local tube coordinates."""
    offset: float
    """Free-surface plane offset in meters."""
    slope: np.ndarray = field(default_factory=lambda: np.zeros(2))
    """Free-surface slope along the two world horizontal axes."""
    slope_velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    """Time derivative of the inertial slope."""
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
    """Measured world velocity of the tube center in meters per second."""

    def accept(self, volume_ml: float, color: np.ndarray, capacity_ml: float) -> float:
        """Mix as much incoming liquid as the vessel can contain."""
        accepted = min(volume_ml, max(0.0, capacity_ml - self.volume_ml))
        total = self.volume_ml + accepted
        if accepted > 0:
            self.color = (self.color * self.volume_ml + color * accepted) / total
            self.volume_ml = total
        return accepted


@dataclass
class LiquidDroplet:
    """A ballistic packet whose volume remains explicitly accounted for."""

    source: str
    """Tube that emitted the packet."""
    position: np.ndarray
    """World position in meters."""
    velocity: np.ndarray
    """World velocity in meters per second."""
    volume_ml: float
    """Conserved packet volume in milliliters."""
    color: np.ndarray
    """Linear RGB tint of the emitted liquid."""
    emitted_at: float = 0.0
    """Simulation time at emission, in seconds."""


@dataclass
class LiquidPuddle:
    """Landed liquid merged locally on a horizontal support plane."""

    position: np.ndarray
    """World center of the visible puddle."""
    volume_ml: float
    """Accumulated landed volume in milliliters."""
    color: np.ndarray
    """Volume-weighted linear RGB tint."""

    def accept(self, droplet: LiquidDroplet) -> None:
        """Merge a landed packet while conserving its volume and tint."""
        total = self.volume_ml + droplet.volume_ml
        self.color = (
            self.color * self.volume_ml + droplet.color * droplet.volume_ml
        ) / total
        self.position[:2] = (
            self.position[:2] * self.volume_ml
            + droplet.position[:2] * droplet.volume_ml
        ) / total
        self.volume_ml = total


# %% simulator
@dataclass
class LaboratoryLiquid:
    """Advance volume-conserving free surfaces, overflow and ballistic transfer."""

    scene: dict
    """Authored scene containing the independently posed tubes."""
    interior: TubeInterior = field(default_factory=TubeInterior)
    """Shared rounded tube interior geometry."""
    parameters: LiquidParameters = field(default_factory=LiquidParameters)
    """Bounded integration and reduced discharge parameters."""
    tubes: dict[str, TubeLiquid] = field(init=False, default_factory=dict)
    """Current liquid state for every supported scene tube."""
    droplets: list[LiquidDroplet] = field(init=False, default_factory=list)
    """Packets currently outside any tube or support surface."""
    puddles: list[LiquidPuddle] = field(init=False, default_factory=list)
    """Liquid resting on the worktop or floor."""
    initial_ml: float = field(init=False, default=0.0)
    """Authored total adjusted by explicit filling and emptying commands."""
    authored_volumes: dict[str, float] = field(init=False, default_factory=dict)
    """Initial volume restored on every reset."""
    authored_colors: dict[str, np.ndarray] = field(init=False, default_factory=dict)
    """Initial tint restored on every reset."""
    time: float = field(init=False, default=0.0)
    """Elapsed liquid integration time since reset, in seconds."""

    def __post_init__(self) -> None:
        """Load authored fills and seed the simulator from the scene poses."""
        levels = {
            TubeKey.CLEAR: self.interior.bottom,
            TubeKey.AMBER: 0.074,
            TubeKey.TEAL: 0.095,
        }
        colors = {
            TubeKey.CLEAR: [0.22, 0.65, 0.86],
            TubeKey.AMBER: [0.82, 0.32, 0.055],
            TubeKey.TEAL: [0.12, 0.56, 0.52],
        }
        poses = {}
        for item in self.scene.get(LiquidField.OBJECTS, []):
            key = item.get(LiquidField.KEY)
            if key not in levels:
                continue
            metadata = item.get(LiquidField.LIQUID, {})
            volume = metadata.get(
                LiquidField.VOLUME, self.interior.upright_volume(levels[key])
            )
            self._validate_volume(volume)
            color = np.asarray(
                metadata.get(LiquidField.COLOR, colors[key]), dtype=float
            )
            if (
                color.shape != (3,)
                or not np.isfinite(color).all()
                or (color < 0).any()
                or (color > 1).any()
            ):
                raise InvalidLiquidInput(
                    "Liquid color requires three finite components between zero and one."
                )
            self.authored_volumes[key] = float(volume)
            self.authored_colors[key] = color.copy()
            poses[key] = item[LiquidField.SPAWN]
        self.reset(poses)

    def _validate_volume(self, volume_ml: float) -> None:
        """Reject a nonfinite amount or an amount outside the inner capacity."""
        if (
            isinstance(volume_ml, (bool, np.bool_))
            or not isinstance(volume_ml, Real)
            or not np.isfinite(volume_ml)
            or not 0 <= volume_ml <= self.interior.capacity_ml
        ):
            raise InvalidLiquidInput(
                "Liquid volume must be a finite number between zero and the tube capacity."
            )

    def _validate_poses(self, poses: dict[str, list[float]]) -> dict[str, TubePose]:
        """Validate every supported tube before changing any simulator state."""
        if not isinstance(poses, dict) or any(
            key not in poses for key in self.authored_volumes
        ):
            raise InvalidLiquidInput(
                "Every liquid tube requires an authoritative pose."
            )
        return {key: TubePose.from_values(poses[key]) for key in self.authored_volumes}

    def reset(self, poses: dict[str, list[float]]) -> None:
        """Restore authored fills and clear all flight, spill and inertial history."""
        validated = self._validate_poses(poses)
        self.tubes.clear()
        for key, pose in validated.items():
            normal = pose.world_T_tube[:3, :3].T @ np.array([0.0, 0.0, 1.0])
            volume = self.authored_volumes[key]
            self.tubes[key] = TubeLiquid(
                key,
                volume,
                self.authored_colors[key].copy(),
                pose,
                normal,
                self.interior.surface_offset(normal, volume),
            )
        self.droplets.clear()
        self.puddles.clear()
        self.time = 0.0
        self.initial_ml = sum(self.authored_volumes.values())

    def fill(
        self, key: str, volume_ml: float, color: list[float] | None = None
    ) -> None:
        """Replace a tube's contained volume and account for the explicit source change."""
        if key not in self.tubes:
            raise InvalidLiquidInput("The selected object is not a liquid tube.")
        self._validate_volume(volume_ml)
        if color is not None and (
            not isinstance(color, (list, tuple, np.ndarray))
            or len(color) != 3
            or any(
                isinstance(value, (bool, np.bool_))
                or not isinstance(value, Real)
                or not np.isfinite(value)
                or not 0 <= value <= 1
                for value in color
            )
        ):
            raise InvalidLiquidInput(
                "Liquid color requires three finite numbers between zero and one."
            )
        tube = self.tubes[key]
        self.initial_ml += float(volume_ml) - tube.volume_ml
        tube.volume_ml = float(volume_ml)
        if color is not None:
            tube.color = np.asarray(color, dtype=float).copy()
        tube.offset = self.interior.surface_offset(tube.normal, tube.volume_ml)

    def step(self, duration: float, poses: dict[str, list[float]]) -> None:
        """Advance liquid using the current authoritative rigid-body poses."""
        if (
            isinstance(duration, (bool, np.bool_))
            or not isinstance(duration, Real)
            or not np.isfinite(duration)
            or not 0 <= duration <= self.parameters.maximum_duration
        ):
            raise InvalidLiquidInput(
                "Liquid duration must be finite and between zero and one second."
            )
        validated = self._validate_poses(poses)
        if duration == 0:
            return
        previous = {key: tube.pose for key, tube in self.tubes.items()}
        steps = max(1, ceil(duration / self.parameters.maximum_step))
        timestep = duration / steps
        for index in range(steps):
            fraction = (index + 1) / steps
            interpolated = {}
            for key, pose in validated.items():
                transform = pose.world_T_tube.copy()
                transform[:3, 3] = (
                    previous[key].world_T_tube[:3, 3] * (1 - fraction)
                    + pose.world_T_tube[:3, 3] * fraction
                )
                interpolated[key] = TubePose(transform)
            self._advance(timestep, interpolated)

    def _advance(self, duration: float, poses: dict[str, TubePose]) -> None:
        """Integrate inertial surfaces, discharge and flight for one bounded step."""
        previous = {key: tube.pose for key, tube in self.tubes.items()}
        for key, tube in self.tubes.items():
            self._move_surface(tube, poses[key], duration)
            self._overflow(tube, duration)
        flying = []
        for droplet in self.droplets:
            start = droplet.position.copy()
            droplet.position += droplet.velocity * duration
            droplet.position[2] -= self.parameters.gravity * duration**2 / 2
            droplet.velocity[2] -= self.parameters.gravity * duration
            self._receive(droplet, start, previous)
            if droplet.volume_ml <= 0:
                continue
            if self._land(droplet, start):
                continue
            flying.append(droplet)
        self.droplets = flying
        for tube in self.tubes.values():
            tube.offset = self.interior.surface_offset(tube.normal, tube.volume_ml)
        self.time += duration

    def _move_surface(self, tube: TubeLiquid, pose: TubePose, duration: float) -> None:
        """Excite a damped world-space free surface from measured translation."""
        parameters = self.parameters
        velocity = np.clip(
            (pose.world_T_tube[:3, 3] - tube.pose.world_T_tube[:3, 3]) / duration,
            -parameters.velocity_limit,
            parameters.velocity_limit,
        )
        acceleration = np.clip(
            (velocity - tube.velocity) / duration,
            -parameters.acceleration_limit,
            parameters.acceleration_limit,
        )
        target = np.clip(
            acceleration[:2]
            / max(parameters.gravity + acceleration[2], parameters.gravity / 2),
            -parameters.slope_limit,
            parameters.slope_limit,
        )
        tube.slope_velocity += (
            parameters.slosh_frequency**2 * (target - tube.slope)
            - 2 * parameters.slosh_damping * tube.slope_velocity
        ) * duration
        tube.slope = np.clip(
            tube.slope + tube.slope_velocity * duration,
            -parameters.slope_limit,
            parameters.slope_limit,
        )
        world_normal = np.array([*tube.slope, 1.0])
        world_normal /= np.linalg.norm(world_normal)
        tube.pose = pose
        tube.velocity = velocity
        tube.normal = pose.world_T_tube[:3, :3].T @ world_normal
        tube.offset = self.interior.surface_offset(tube.normal, tube.volume_ml)

    def _overflow(self, tube: TubeLiquid, duration: float) -> None:
        """Discharge excess volume through the lowest available point on the rim."""
        if tube.volume_ml <= 0:
            return
        lip = self.interior.lowest_lip(tube.normal)
        lip_offset = float(tube.normal @ lip)
        retained = self.interior.volume_below(tube.normal, lip_offset)
        excess = max(0.0, tube.volume_ml - retained)
        head = max(0.0, tube.offset - lip_offset)
        speed = sqrt(2 * self.parameters.gravity * head)
        flow = (
            self.parameters.discharge
            * pi
            * self.interior.radius**2
            * speed
            * self.interior.milliliters_per_cubic_meter
        )
        emitted = min(excess, flow * duration)
        if emitted <= 0:
            return
        direction = lip.copy()
        direction[2] = 0.0
        direction /= max(float(np.linalg.norm(direction)), self.interior.radius)
        velocity = (
            tube.velocity + tube.pose.world_T_tube[:3, :3] @ direction * speed / 2
        )
        tube.volume_ml -= emitted
        self.droplets.append(
            LiquidDroplet(
                tube.key,
                tube.pose.world_point(lip),
                velocity.copy(),
                emitted,
                tube.color.copy(),
                self.time,
            )
        )

    def _receive(
        self, droplet: LiquidDroplet, start: np.ndarray, previous: dict[str, TubePose]
    ) -> None:
        """Capture packets crossing inward through another tube's circular opening."""
        for key, tube in self.tubes.items():
            if key == droplet.source or droplet.volume_ml <= 0:
                continue
            before = previous[key].local_point(start)
            after = tube.pose.local_point(droplet.position)
            if before[2] <= self.interior.rim or after[2] > self.interior.rim:
                continue
            fraction = (before[2] - self.interior.rim) / (before[2] - after[2])
            crossing = before + (after - before) * fraction
            if np.linalg.norm(crossing[:2]) > self.interior.radius:
                continue
            droplet.volume_ml -= tube.accept(
                droplet.volume_ml, droplet.color, self.interior.capacity_ml
            )

    def _land(self, droplet: LiquidDroplet, start: np.ndarray) -> bool:
        """Deposit packets on the bounded worktop or the floor beneath it."""
        parameters = self.parameters
        for height in (parameters.worktop_height, 0.0):
            if start[2] < height or droplet.position[2] > height:
                continue
            fraction = (start[2] - height) / max(start[2] - droplet.position[2], 1e-12)
            crossing = start + (droplet.position - start) * fraction
            if height > 0 and (
                abs(crossing[0]) > parameters.worktop_half_width
                or abs(crossing[1]) > parameters.worktop_half_depth
            ):
                continue
            droplet.position = crossing
            droplet.position[2] = height + parameters.puddle_offset
            for puddle in self.puddles:
                if (
                    np.linalg.norm(puddle.position - droplet.position)
                    <= parameters.puddle_merge_distance
                ):
                    puddle.accept(droplet)
                    return True
            self.puddles.append(
                LiquidPuddle(
                    droplet.position.copy(), droplet.volume_ml, droplet.color.copy()
                )
            )
            return True
        return False

    def snapshot(self) -> dict:
        """Return renderer geometry and a complete milliliter conservation ledger."""
        tubes = {
            key: {
                LiquidField.VOLUME: tube.volume_ml,
                LiquidField.CAPACITY: self.interior.capacity_ml,
                LiquidField.COLOR: tube.color.tolist(),
                LiquidField.NORMAL: tube.normal.tolist(),
                LiquidField.OFFSET: tube.offset,
                LiquidField.RADIUS: self.interior.radius,
                LiquidField.BOTTOM: self.interior.bottom,
                LiquidField.ROUNDING_RADIUS: self.interior.radius,
                LiquidField.RIM: self.interior.rim,
            }
            for key, tube in self.tubes.items()
        }
        droplets = [
            {
                LiquidField.SOURCE: droplet.source,
                LiquidField.EMITTED_AT: droplet.emitted_at,
                LiquidField.VELOCITY: droplet.velocity.tolist(),
                LiquidField.POSITION: droplet.position.tolist(),
                LiquidField.RADIUS: (
                    3
                    * droplet.volume_ml
                    / self.interior.milliliters_per_cubic_meter
                    / (4 * pi)
                )
                ** (1 / 3),
                LiquidField.COLOR: droplet.color.tolist(),
            }
            for droplet in self.droplets
        ]
        puddles = [
            {
                LiquidField.POSITION: puddle.position.tolist(),
                LiquidField.VOLUME: puddle.volume_ml,
                LiquidField.COLOR: puddle.color.tolist(),
            }
            for puddle in self.puddles
        ]
        contained = sum(tube.volume_ml for tube in self.tubes.values())
        flying = sum(droplet.volume_ml for droplet in self.droplets)
        spilled = sum(puddle.volume_ml for puddle in self.puddles)
        return {
            LiquidField.MODEL: LiquidModel.REDUCED_FREE_SURFACE,
            LiquidField.TIME: self.time,
            LiquidField.TUBES: tubes,
            LiquidField.DROPLETS: droplets,
            LiquidField.PUDDLES: puddles,
            LiquidField.TOTAL: contained + flying + spilled,
            LiquidField.SPILLED: spilled,
            LiquidField.IN_FLIGHT: flying,
            LiquidField.INITIAL: self.initial_ml,
        }
