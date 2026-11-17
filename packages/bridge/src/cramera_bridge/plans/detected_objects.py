"""
The objects later steps name by the detection step that finds them: each is the object
of the twin the detection's query finds when the plan is built, which the detection then
perceives again when it runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import Dict, List, Optional, Set

from coraplex.perception import PerceptionQuery
from cramera_bridge.exceptions import AmbiguousDetectedObject, DetectedObjectNotInTwin
from cramera_bridge.performing import TaskContext
from cramera_bridge.plans.actions import DiscoveredActions
from cramera_bridge.plans.object_types import ObjectType
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.site import RobotSite
from cramera_bridge.plans.steps import StepArguments
from cramera_bridge.plans.world import OBJECT_TYPE_ANNOTATIONS
from cramera_protocol.detection import DetectedObjectReference, DetectionRegion
from cramera_protocol.plan_builder import ActionFieldKind, PlanStep
from semantic_digital_twin.world_description.color_names import ColorName
from semantic_digital_twin.world_description.world_entity import Body

# %% detected objects


@dataclass(frozen=True)
class DetectedObjects:
    """
    Resolves the references to the objects detection steps find.
    """

    context: TaskContext
    """
    The world and the robot that performs the steps.
    """

    bodies: Dict[str, Body]
    """
    The plan's objects by their identity.
    """

    actions: DiscoveredActions
    """
    The workspace's actions, which steps name.
    """

    site: RobotSite = field(default_factory=RobotSite)
    """
    Where the plan stands the robot, and the robot profile's zones.
    """

    def resolve(self, steps: List[PlanStep]) -> Dict[str, Body]:
        """
        :param steps: The steps of the robot that performs them.
        :return: The object each reference a step makes finds, by the reference's text.
        :raises DetectedObjectNotInTwin: When a referenced detection finds no object.
        :raises AmbiguousDetectedObject: When it finds several.
        """
        referenced = self.referenced_steps(steps)
        return {
            DetectedObjectReference(step.identifier).text: self.found_object(step)
            for step in steps
            if step.identifier in referenced
        }

    @staticmethod
    def referenced_steps(steps: List[PlanStep]) -> Set[str]:
        """
        :param steps: Steps of one robot.
        :return: The identities of the steps whose detected object a step names.
        """
        references = (
            DetectedObjectReference.from_value(value)
            for step in steps
            for value in step.values.values()
            if isinstance(value, str)
        )
        return {reference.step for reference in references if reference is not None}

    def found_object(self, step: PlanStep) -> Body:
        """
        :param step: A detection step.
        :return: The one object of the twin its query finds, wherever the camera looks.
        :raises DetectedObjectNotInTwin: When it finds none.
        :raises AmbiguousDetectedObject: When it finds several.
        """
        found = self.query(step).candidates()
        if not found:
            raise DetectedObjectNotInTwin(step.identifier)
        if len(found) > 1:
            raise AmbiguousDetectedObject(step.identifier, len(found))
        return found[0]

    def query(self, step: PlanStep) -> PerceptionQuery:
        """
        :param step: A step looking for an object.
        :return: What it looks for: the kind and color its values name, any free object
            of any color by default, in its region, the whole table by default.
        """
        arguments = StepArguments(
            context=self.context,
            bodies=self.bodies,
            action=self.actions.named(step.action),
            step=step,
            site=self.site,
        )
        region = RegionBoxes(self.context.robot, site=self.site).box(DetectionRegion())
        object_type = OBJECT_TYPE_ANNOTATIONS[ObjectType.OBJECT]
        color: Optional[ColorName] = None
        for offered in arguments.action.schema.fields:
            if offered.name not in step.values:
                continue
            value = step.values[offered.name]
            field_type = arguments.action.field_types[offered.name]
            if offered.kind is ActionFieldKind.REGION:
                region = arguments.region(offered, value)
            elif field_type is ObjectType:
                object_type = arguments.member(offered, value)
            elif field_type is ColorName:
                color = arguments.member(offered, value)
        return PerceptionQuery(
            object_type,
            region,
            self.context.robot,
            self.context.world,
            color=color,
        )
