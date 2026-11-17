"""
Scanning the scene while a plan is composed: the plan's robot looks for the free objects
in a region, where its camera sees the region now through its perception, else in the
twin; what it finds moves the plan's objects, and an object the plan does not hold yet
becomes a copy of the object model matching its kind and color.

A simulated robot perceives a simulated scene: RoboKudo renders its camera from a world
of its own holding the scene's objects, so the plan's twin can start empty.

Run as ``python -m cramera_bridge.plans.scanning <order.json> <scan.json>``, it reads a
:class:`~cramera_protocol.detection.SceneScanOrder` and writes the
:class:`~cramera_protocol.detection.SceneScan`, once its standard input closes, so each scan has a process of its own.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from typing_extensions import Any, Dict, List, Optional

from coraplex.perception import (
    DetectedScene,
    Detection,
    PerceptionQuery,
    ValidationPerception,
)
from cramera_bridge.execution import PhysicalExecution
from cramera_bridge.geometry import NumericPose
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.object_types import ObjectType
from cramera_bridge.plans.order_command import OrderCommand
from cramera_bridge.plans.objects import (
    DescribedModel,
    ObjectLibraries,
    ObjectLibrary,
)
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.robot_perception import RobotPerception
from cramera_bridge.plans.world import GRASPABLE_TYPES, OBJECT_TYPE_ANNOTATIONS
from cramera_protocol.detection import DetectionGrounding, SceneScan, SceneScanOrder
from cramera_protocol.plan_builder import PlannedObject
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

# %% scanning


@dataclass
class SceneScanner:
    """
    Scans the scene a plan's robot sees for the plan's free objects.
    """

    order: SceneScanOrder
    """
    The plan, the region and the scene the camera sees.
    """

    libraries: ObjectLibraries = field(default_factory=ObjectLibraries.of_environment)
    """
    The object libraries the models of new objects come from.
    """

    perception: RobotPerception = field(init=False)
    """
    The perception of the plan's robot.
    """

    models: List[DescribedModel] = field(init=False)
    """
    The models of :attr:`libraries` with their colors and grasps, read once for the
    scan, since every new detection is matched against all of them.
    """

    def __post_init__(self) -> None:
        self.perception = RobotPerception(
            demo=self.order.demo, simulated_objects=self.order.simulated_objects
        )
        self.models = self.libraries.described_models()

    def scan(self) -> SceneScan:
        """
        :return: The plan's objects after the scan; a simulated robot rests with its
            arms parked, as a simulated run starts.
        :raises PerceptionEngineNotNamed: When a simulated scene is perceived and no
            analysis engine is named.
        """
        believed = PlannedTaskDemo.of_document(self.order.demo, port=0)
        world = believed.build_world()
        if self.order.simulated_objects is not None:
            PhysicalExecution().prepare(world)
        context = believed.build_context(world)
        query = PerceptionQuery(
            OBJECT_TYPE_ANNOTATIONS[ObjectType.OBJECT],
            RegionBoxes(context.robot, site=believed.site).box(self.order.region),
            context.robot,
            world,
        )
        with self.perception.perceiving() as live:
            grounded = ValidationPerception(live).look_for(query)
        bodies = DetectedScene(grounded.detections, query.region).apply_to(world)
        known = set(believed.bodies.values())
        models = [
            None if body in known else self.matching_model(detection)
            for detection, body in zip(grounded.detections, bodies)
        ]
        return SceneScan(
            objects=self.plan_objects(world, believed.bodies, models, bodies),
            grounding=DetectionGrounding(grounded.grounding.value),
            unrecognized=sum(
                1
                for model, body in zip(models, bodies)
                if body not in known and model is None
            ),
        )

    def plan_objects(
        self,
        world: World,
        planned: Dict[str, Body],
        models: List[Optional[str]],
        bodies: List[Body],
    ) -> List[PlannedObject]:
        """
        :param world: The twin after the scan.
        :param planned: The bodies of the plan's objects, by their identity.
        :param models: The model matching each detection whose body is new, see
            :meth:`matching_model`, None for the others.
        :param bodies: The body each detection moved or added.
        :return: The plan's objects where the twin holds them now, then a copy of the
            matching object model for each new body; a new body no model matches is left
            out.
        """
        objects = [
            replace(
                planned_object,
                pose=NumericPose.of_entity(
                    world, planned[planned_object.identifier]
                ).published(),
            )
            for planned_object in self.order.demo.plan.objects
        ]
        for model, body in zip(models, bodies):
            if model is None:
                continue
            objects.append(
                PlannedObject(
                    identifier=self.free_identifier(
                        ObjectLibrary.slug(Path(model)), objects
                    ),
                    model=model,
                    pose=NumericPose.of_entity(world, body).published(),
                )
            )
        return objects

    def matching_model(self, detection: Detection) -> Optional[str]:
        """
        :param detection: An object the scan found.
        :return: The identifier of the first object model of the detected color whose
            objects carry the detected annotation, None when there is none.
        """
        if detection.color is None:
            return None
        for described in self.models:
            if described.color == detection.color.value and issubclass(
                GRASPABLE_TYPES[described.grasp], detection.semantic_annotation
            ):
                return described.model.identifier
        return None

    @staticmethod
    def free_identifier(base: str, objects: List[PlannedObject]) -> str:
        """
        :param base: The identity an object would like.
        :param objects: The objects holding identities already.
        :return: ``base``, or ``base`` followed by ``_2``, ``_3`` and so on when taken.
        """
        taken = {planned.identifier for planned in objects}
        identifier = base
        number = 2
        while identifier in taken:
            identifier = f"{base}_{number}"
            number += 1
        return identifier


# %% command


@dataclass(frozen=True)
class ScanCommand(OrderCommand):
    """
    Scans the scene an order file names and writes the scan into a file.
    """

    def answer(self, order: Any) -> SceneScan:
        return SceneScanner(SceneScanOrder.from_json(order)).scan()


if __name__ == "__main__":
    ScanCommand().main(sys.argv[1:])
