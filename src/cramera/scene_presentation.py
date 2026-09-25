"""Authored appearance settings carried from source scenes into native worlds."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum

from typing_extensions import Any

# %% scene presentation fields


class PresentationField(StrEnum):
    """Scene-file fields that determine authored rendering and material handling."""

    RENDERING = "rendering"
    """Renderer settings from the source scene."""

    CAMERA = "camera"
    """Initial camera position and target."""

    MODELS = "models"
    """Articulated model declarations."""

    ROBOT = "robot"
    """Whether a model contains a robot."""

    OBJECTS = "objects"
    """Independently animated object declarations."""

    KEY = "key"
    """Exact object identifier shared with native object catalogs."""

    PRESERVE_MATERIALS = "preserveMaterials"
    """Opt-in to retaining the model or object's authored materials."""


# %% authored scene presentation


@dataclass(frozen=True)
class ScenePresentation:
    """Appearance settings applied without importing source manipulation controls."""

    rendering: dict[str, Any] | None = None
    """Renderer configuration copied from the authored scene when present."""

    camera: dict[str, Any] | None = None
    """Initial camera configuration copied from the authored scene when present."""

    preserve_environment_materials: bool = False
    """Whether the source environment opts in to retaining authored materials."""

    preserved_object_keys: frozenset[str] = field(default_factory=frozenset)
    """Exact object keys whose source declarations retain authored materials."""

    @classmethod
    def from_json(cls, source: dict[str, Any]) -> ScenePresentation:
        """Read presentation fields from an authored scene file."""
        rendering = source.get(PresentationField.RENDERING)
        camera = source.get(PresentationField.CAMERA)
        return cls(
            rendering=deepcopy(rendering) if isinstance(rendering, dict) else None,
            camera=deepcopy(camera) if isinstance(camera, dict) else None,
            preserve_environment_materials=any(
                not model.get(PresentationField.ROBOT, False)
                and model.get(PresentationField.PRESERVE_MATERIALS) is True
                for model in source.get(PresentationField.MODELS, [])
            ),
            preserved_object_keys=frozenset(
                entry[PresentationField.KEY]
                for entry in source.get(PresentationField.OBJECTS, [])
                if entry.get(PresentationField.PRESERVE_MATERIALS) is True
                and isinstance(entry.get(PresentationField.KEY), str)
            ),
        )

    def apply_to_scene(self, scene: dict[str, Any]) -> None:
        """Apply copied appearance settings to a generated scene in place."""
        if self.rendering is not None:
            scene[PresentationField.RENDERING] = deepcopy(self.rendering)
        if self.camera is not None:
            scene[PresentationField.CAMERA] = deepcopy(self.camera)
        if self.preserve_environment_materials:
            for model in scene.get(PresentationField.MODELS, []):
                if not model.get(PresentationField.ROBOT, False):
                    model[PresentationField.PRESERVE_MATERIALS] = True
        for entry in scene.get(PresentationField.OBJECTS, []):
            self.apply_to_object(entry)

    def apply_to_object(self, payload: dict[str, Any]) -> None:
        """Retain authored materials for an explicitly opted-in object in place."""
        if payload.get(PresentationField.KEY) in self.preserved_object_keys:
            payload[PresentationField.PRESERVE_MATERIALS] = True

    def signature(self) -> str:
        """Return a stable digest of settings affecting generated scene appearance."""
        payload = {
            PresentationField.MODELS: [
                {
                    PresentationField.PRESERVE_MATERIALS: (
                        self.preserve_environment_materials
                    )
                }
            ],
            PresentationField.OBJECTS: [
                {PresentationField.KEY: key}
                for key in sorted(self.preserved_object_keys)
            ],
        }
        self.apply_to_scene(payload)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
