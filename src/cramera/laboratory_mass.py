"""
Couple contained liquid into the inertia used by the contact solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

import mujoco
import numpy as np

from cramera.laboratory_liquid import LaboratoryLiquid


# %% dry reference properties
@dataclass(frozen=True)
class DryBodyInertia:
    """
    Authored inertial properties before any contained liquid is added.
    """

    body_id: int
    """
    MuJoCo body carrying the glass and its liquid.
    """

    mass: float
    """
    Dry mass in kilograms.
    """

    inertia: np.ndarray
    """
    Dry principal moments about the unchanged inertial frame.
    """


@dataclass
class LaboratoryLiquidMass:
    """
    Add a lumped liquid load at each glass's authored inertial frame.

    Liquid uses a fixed density and the glass's normalized inertia distribution. Static
    weight therefore follows contained volume; fluid redistribution and sloshing torques
    are not represented by this inertial approximation.
    """

    model: mujoco.MjModel
    """
    Collision model whose tube masses change with contained volume.
    """

    tube_body_ids: dict[str, int]
    """
    Authored tube identifiers mapped to their dynamic MuJoCo bodies.
    """

    density_grams_per_milliliter: float = 1.0
    """
    Assumed density of the water-like liquids in grams per milliliter.
    """

    grams_per_kilogram: float = field(init=False, default=1000.0)
    """
    Unit conversion from liquid mass to MuJoCo's kilogram convention.
    """

    dry_bodies: dict[str, DryBodyInertia] = field(init=False)
    """
    Immutable baseline values that prevent accumulation across updates.
    """

    constant_data: mujoco.MjData = field(init=False, repr=False)
    """
    Scratch state for rebuilding model constants without resetting live motion.
    """

    def __post_init__(self) -> None:
        """
        Capture dry properties and allocate separate constant-calculation state.
        """
        if (
            not isfinite(self.density_grams_per_milliliter)
            or self.density_grams_per_milliliter <= 0
        ):
            raise ValueError("Liquid density must be positive and finite")
        self.dry_bodies = {
            key: DryBodyInertia(
                body_id,
                float(self.model.body_mass[body_id]),
                self.model.body_inertia[body_id].copy(),
            )
            for key, body_id in self.tube_body_ids.items()
        }
        self.constant_data = mujoco.MjData(self.model)

    def synchronize(self, liquid: LaboratoryLiquid) -> bool:
        """
        Refresh changed loads and return whether model constants were rebuilt.
        """
        changed = False
        for key, dry in self.dry_bodies.items():
            mass = dry.mass + (
                liquid.tubes[key].volume_ml
                * self.density_grams_per_milliliter
                / self.grams_per_kilogram
            )
            if self.model.body_mass[dry.body_id] == mass:
                continue
            self.model.body_mass[dry.body_id] = mass
            self.model.body_inertia[dry.body_id] = dry.inertia * (mass / dry.mass)
            changed = True
        if changed:
            mujoco.mj_setConst(self.model, self.constant_data)
        return changed
