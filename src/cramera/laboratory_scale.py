"""Measure the load transmitted through the laboratory balance pan."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

import mujoco
import numpy as np
from typing_extensions import Any


# %% measurement contract
class ScaleField(StrEnum):
    """Published balance readings in grams and Newtons."""

    GRAMS = "grams"
    GROSS_GRAMS = "grossGrams"
    TARE_GRAMS = "tareGrams"
    STABLE = "stable"
    FORCE_NEWTONS = "forceNewtons"
    OBJECTS = "objects"
    AVAILABLE = "available"


class ScaleGeometry(StrEnum):
    """Authored collision surface that transmits the measured load."""

    PAN = "laboratory_balance_weighing_pan"
    """Primary load-bearing surface of the instrument."""
    HOLDER_PREFIX = "laboratory_balance_weighing_holder_"
    """Prefix preceding each numbered pan-mounted support segment."""


@dataclass(frozen=True)
class ScaleParameters:
    """Sampling and settling criteria of the simulated balance."""

    sample_period: float = 0.02
    """Interval between force samples in simulation seconds."""
    filter_time_constant: float = 0.12
    """Exponential display smoothing time in seconds."""
    settling_period: float = 0.5
    """Required continuous observation window before permitting tare."""
    settling_tolerance: float = 0.01
    """Maximum force-equivalent fluctuation across the settling window in grams."""
    decimal_places: int = 3
    """Display precision; settling also requires less than half a digit of filter error."""


@dataclass
class InvalidScaleOperation(ValueError):
    """A balance operation requires an available, settled measurement."""

    reason: str
    """Description of the missing condition."""

    def __str__(self) -> str:
        """Present the validation failure to the caller."""
        return self.reason


# %% contact load measurement
@dataclass
class LaboratoryScale:
    """Report vertical contact load on the pan, with smoothing and deliberate tare."""

    model: mujoco.MjModel
    """Collision model containing the optional weighing pan."""
    data: mujoco.MjData
    """Live solver state used to read contact reactions."""
    parameters: ScaleParameters = field(default_factory=ScaleParameters)
    """Display filtering and stability settings."""
    pan_id: int = field(init=False)
    """Compiled pan index, or minus one for older scenes without a balance."""
    load_geometry_ids: set[int] = field(init=False)
    """Pan and attached collar surfaces contributing to the measured load."""
    gross_grams: float = field(init=False, default=0.0)
    """Filtered total load before subtracting tare."""
    tare_grams: float = field(init=False, default=0.0)
    """Deliberately recorded zero offset."""
    force_newtons: float = field(init=False, default=0.0)
    """Most recently measured downward force on the pan."""
    objects: list[str] = field(init=False, default_factory=list)
    """Names of bodies currently transmitting contact load to the pan."""
    readings: deque[tuple[float, float]] = field(init=False, default_factory=deque)
    """Recent timestamped force-equivalent masses for settling detection."""
    last_sample: float | None = field(init=False, default=None)
    """Simulation timestamp of the latest observation."""

    def __post_init__(self) -> None:
        """Resolve the authored pan while supporting older scene bundles."""
        self.pan_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, ScaleGeometry.PAN
        )
        self.load_geometry_ids = {
            index
            for index in range(self.model.ngeom)
            if self.model.geom(index).name.startswith(ScaleGeometry.HOLDER_PREFIX)
            and self.model.geom(index)
            .name.removeprefix(ScaleGeometry.HOLDER_PREFIX)
            .isdigit()
        }
        if self.pan_id >= 0:
            self.load_geometry_ids.add(self.pan_id)

    @property
    def available(self) -> bool:
        """Whether this model contains a pan and nonzero gravity."""
        return self.pan_id >= 0 and float(np.linalg.norm(self.model.opt.gravity)) > 0

    @property
    def stable(self) -> bool:
        """Whether the load has settled sufficiently for an intentional tare."""
        if not self.available or not self.readings:
            return False
        elapsed = self.readings[-1][0] - self.readings[0][0]
        values = [value for _, value in self.readings]
        return (
            elapsed + 1e-9 >= self.parameters.settling_period
            and max(values) - min(values) <= self.parameters.settling_tolerance
            and abs(self.gross_grams - values[-1])
            <= 0.5 * 10 ** (-self.parameters.decimal_places)
        )

    def reset(self) -> None:
        """Clear tare and prior readings after restoring the physical scene."""
        self.gross_grams = self.tare_grams = self.force_newtons = 0.0
        self.objects.clear()
        self.readings.clear()
        self.last_sample = None
        self.observe(force=True)

    def observe(self, force: bool = False) -> None:
        """Sample resolved pan reactions and advance the filtered load display."""
        if not self.available:
            return
        now = float(self.data.time)
        elapsed = 0.0 if self.last_sample is None else now - self.last_sample
        if (
            not force
            and self.last_sample is not None
            and elapsed + 1e-12 < self.parameters.sample_period
        ):
            return
        gravity = float(np.linalg.norm(self.model.opt.gravity))
        upward = -self.model.opt.gravity / gravity
        load = 0.0
        objects = set()
        effort = np.zeros(6)
        for index in range(self.data.ncon):
            contact = self.data.contact[index]
            pan_first = contact.geom1 in self.load_geometry_ids
            pan_second = contact.geom2 in self.load_geometry_ids
            if contact.efc_address < 0 or pan_first == pan_second:
                continue
            mujoco.mj_contactForce(self.model, self.data, index, effort)
            world_force = contact.frame.reshape(3, 3).T @ effort[:3]
            downward = float(world_force @ upward) * (1 if pan_first else -1)
            load += downward
            if downward > 0:
                other = contact.geom2 if pan_first else contact.geom1
                objects.add(self.model.body(self.model.geom_bodyid[other]).name)
        self.force_newtons = max(0.0, load)
        self.objects = sorted(objects)
        grams = self.force_newtons / gravity * 1000.0
        if self.last_sample is None:
            self.gross_grams = grams
        else:
            fraction = -math.expm1(-elapsed / self.parameters.filter_time_constant)
            self.gross_grams += fraction * (grams - self.gross_grams)
        self.last_sample = now
        self.readings.append((now, grams))
        while (
            len(self.readings) > 1
            and self.readings[1][0] <= now - self.parameters.settling_period
        ):
            self.readings.popleft()

    def tare(self) -> None:
        """Use a settled total load as the display's new zero reference."""
        if not self.available:
            raise InvalidScaleOperation("This laboratory has no weighing pan")
        if not self.stable:
            raise InvalidScaleOperation("Wait for the balance reading to settle")
        self.tare_grams = self.gross_grams

    def snapshot(self) -> dict[str, Any]:
        """Return the latest authoritative measurement and stability status."""
        return {
            ScaleField.GRAMS: round(
                self.gross_grams - self.tare_grams, self.parameters.decimal_places
            )
            + 0.0,
            ScaleField.GROSS_GRAMS: round(
                self.gross_grams, self.parameters.decimal_places
            ),
            ScaleField.TARE_GRAMS: round(
                self.tare_grams, self.parameters.decimal_places
            ),
            ScaleField.FORCE_NEWTONS: self.force_newtons,
            ScaleField.STABLE: self.stable,
            ScaleField.OBJECTS: self.objects.copy(),
            ScaleField.AVAILABLE: self.available,
        }
