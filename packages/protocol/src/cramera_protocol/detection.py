"""
Detecting objects while a plan is composed and when it runs: the region a detection
looks in, how a later step names the object a detection found, and scanning the scene
for the objects on the table, which fills a plan's object list.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import List, Optional

from cramera_protocol.document import JsonDocument, document_field
from cramera_protocol.geometry import Pose
from cramera_protocol.plan_builder import DemoDocument, PlanDocument, PlannedObject

# %% regions


class RegionKind(StrEnum):
    """
    How a detection names the region it looks in.
    """

    TABLE = "table"
    """
    The whole top of the table the robot works at.
    """

    ZONE = "zone"
    """
    A named zone of the robot profile.
    """

    MARKER = "marker"
    """
    The square around a marker dragged across the scene.
    """


class DetectionRegionKey(StrEnum):
    """
    The keys of a detection's region.
    """

    KIND = "kind"
    """
    How the region is named.
    """

    ZONE = "zone"
    """
    The named zone.
    """

    POSE = "pose"
    """
    The marker's pose.
    """


@dataclass(kw_only=True)
class DetectionRegion(JsonDocument):
    """
    Where a detection looks for objects; the value of a field of kind
    :attr:`~cramera_protocol.plan_builder.ActionFieldKind.REGION`.
    """

    kind: RegionKind = document_field(DetectionRegionKey.KIND, default=RegionKind.TABLE)
    """
    How the region is named.
    """

    zone: Optional[str] = document_field(DetectionRegionKey.ZONE, default=None)
    """
    The identifier of the robot profile's zone a region of kind :attr:`RegionKind.ZONE`
    is.
    """

    pose: Optional[Pose] = document_field(DetectionRegionKey.POSE, default=None)
    """
    The pose of the marker a region of kind :attr:`RegionKind.MARKER` lies around, in
    world coordinates, resting on the surface below it.
    """


# %% references to detected objects


class DetectedObjectText(StrEnum):
    """
    How an object field names the object an earlier detection step found.
    """

    PREFIX = "detected:"
    """
    Comes before the detection step's identity; no object identity contains it.
    """


@dataclass(frozen=True)
class DetectedObjectReference:
    """
    The object a detection step of the same robot found, named by a later step in place
    of one of the plan's objects.
    """

    step: str
    """
    The identity of the detection step.
    """

    @property
    def text(self) -> str:
        """
        The reference as an object field's value.
        """
        return f"{DetectedObjectText.PREFIX}{self.step}"

    @classmethod
    def from_value(cls, value: str) -> Optional[DetectedObjectReference]:
        """
        :param value: An object field's value.
        :return: The reference it is, or None for an object's identity.
        """
        if not value.startswith(DetectedObjectText.PREFIX):
            return None
        return cls(step=value.removeprefix(DetectedObjectText.PREFIX))


# %% scanning the scene


class DetectionGrounding(StrEnum):
    """
    Where the objects a detection reports while a plan is composed come from.
    """

    PERCEIVED = "perceived"
    """
    Seen by the robot's perception now, as its camera sees the region.
    """

    ASSUMED = "assumed"
    """
    Taken from the twin, as the camera does not see the region now; the detection is
    perceived again when the plan runs.
    """


class ScanRequestKey(StrEnum):
    """
    The keys of a request to scan the scene.
    """

    PLAN = "plan"
    """
    The plan whose objects the scan updates.
    """

    REGION = "region"
    """
    Where the scan looks.
    """


@dataclass(kw_only=True)
class ScanRequest(JsonDocument):
    """
    Asks the plan builder to look for the objects in a region of the scene.
    """

    plan: PlanDocument = document_field(ScanRequestKey.PLAN)
    """
    The plan whose robot looks and whose objects are the twin's.
    """

    region: DetectionRegion = document_field(
        ScanRequestKey.REGION, default_factory=DetectionRegion
    )
    """
    Where the scan looks.
    """


class SceneScanOrderKey(StrEnum):
    """
    The keys of what the bridge scans.
    """

    DEMO = "demo"
    """
    The plan and its environment.
    """

    REGION = "region"
    """
    Where the scan looks.
    """

    SIMULATED_OBJECTS = "simulatedObjects"
    """
    The objects of the simulated scene the camera is rendered from.
    """


@dataclass(kw_only=True)
class SceneScanOrder(JsonDocument):
    """
    What the bridge scans: a plan's world, a region in it, and the scene the robot's
    camera sees.
    """

    demo: DemoDocument = document_field(SceneScanOrderKey.DEMO)
    """
    The plan, whose robot looks and whose objects are the twin's, and its environment.
    """

    region: DetectionRegion = document_field(
        SceneScanOrderKey.REGION, default_factory=DetectionRegion
    )
    """
    Where the scan looks.
    """

    simulated_objects: Optional[List[PlannedObject]] = document_field(
        SceneScanOrderKey.SIMULATED_OBJECTS, default=None
    )
    """
    The objects standing in a simulated scene that the robot's camera is rendered from,
    or None to ask the robot's own perception.
    """


class SceneScanKey(StrEnum):
    """
    The keys of a scan's result.
    """

    OBJECTS = "objects"
    """
    The plan's objects after the scan.
    """

    GROUNDING = "grounding"
    """
    Where the objects found come from.
    """

    UNRECOGNIZED = "unrecognized"
    """
    How many objects were found that no object model matches.
    """


@dataclass(kw_only=True)
class SceneScan(JsonDocument):
    """
    The plan's objects after a scan: those found where they were found, each new one a
    copy of the object model matching it, and the others as they were.
    """

    objects: List[PlannedObject] = document_field(SceneScanKey.OBJECTS)
    """
    The plan's objects.
    """

    grounding: DetectionGrounding = document_field(SceneScanKey.GROUNDING)
    """
    Whether the objects found were perceived or assumed from the twin.
    """

    unrecognized: int = document_field(SceneScanKey.UNRECOGNIZED, default=0)
    """
    How many objects were found that no object model matches, which were left out.
    """
