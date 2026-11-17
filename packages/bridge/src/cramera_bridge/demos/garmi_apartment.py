"""
GARMI carries a bowl off the kitchen counter and a spoon out of a drawer to the table of
the apartment, as CRAM's GARMI demonstration (``coraplex/demos/coraplex_garmi_demo``)
does, recorded for the viewer.

The world is the apartment's MuJoCo scene with its furniture annotated by the world
reasoner, the bowl and the spoon from coraplex's object meshes, and CRAM's GARMI. The
demo finds GARMI, the bowl and the spoon with entity queries; each transport leaves
where GARMI stands and how it grasps open, and the plan grounds them by a query when it
reaches them. With ``--step-queries`` each of these queries is a query step.

Needs ROS 2 with the ``iai_garmi_apartment`` and ``garmi_description`` packages built,
since the scene and the robot are read from their share directories.

``python -m cramera_bridge.demos.garmi_apartment [--step-queries] [--port N] [--name
NAME] [--publish DIR]``
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from enum import Enum, IntEnum, StrEnum

import numpy as np
from typing_extensions import Dict, Optional, Sequence

from coraplex.plans.plan_transformation import PlanRewriting
from coraplex.robot_plans.actions.composite.transporting import TransportAction
from coraplex.robot_plans.actions.core.robot_body import ParkArmsAction
from coraplex.robot_plans.plan_transformations import OpenDrawerBeforeMoveAndPickUp
from cramera_bridge.exceptions import TaskFailed
from cramera_bridge.performing import TaskContext
from cramera_bridge.plans.workspace import OBJECT_DIRECTORY
from cramera_bridge.robot_task import RobotTaskDemo, TaskCommand
from cramera_bridge.scenes.environment import SceneEnvironment
from cramera_protocol.bundle.acceptance import AcceptanceMetrics
from cramera_protocol.bundle.scene import Camera
from cramera_protocol.geometry import Vector3
from cramph.composites import Sequence
from cramph.node import StatechartNode
from krrood.entity_query_language.factories import entity, the, variable
from semantic_digital_twin.reasoning.world_reasoner import WorldReasoner
from semantic_digital_twin.robots.garmi import Garmi
from semantic_digital_twin.semantic_annotations.mixins import HasGraspCandidates
from semantic_digital_twin.semantic_annotations.semantic_annotations import Bowl, Spoon
from semantic_digital_twin.specifications.connections import (
    Connection6DoFSpecification,
)
from semantic_digital_twin.specifications.kinematic_structure_entities import (
    BodySpecification,
)
from semantic_digital_twin.specifications.robots import RobotSpecification
from semantic_digital_twin.specifications.worlds import WorldSpecification
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from krrood_spatial_math.spatial_types import Point3, Pose
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import FixedConnection

APARTMENT_PACKAGE = "iai_garmi_apartment"
"""
The ROS package holding the apartment's MuJoCo scene.
"""

NAME = "apartment_garmi_bowl_and_spoon"
"""
The name the recording is saved under unless ``--name`` gives another.
"""

TASK = "GARMI: carries a bowl and a spoon to the table"
"""
What the task does, as the scene picker labels it.
"""

ENVIRONMENT_NAME = "Apartment"
"""
The environment's display name, as the recorded GARMI scenes name it.
"""

CAMERA = Camera(position=Vector3(-1.6, 3.4, 3.2), target=Vector3(0.6, 6.4, 0.8))
"""
The recording's first view: the kitchen counter, the drawer and the table from the
living room.
"""

PLACE_TOLERANCE = 0.05
"""
How far, in metres, an object may end from where it was carried to.
"""

# %% what the scene is built from


class SceneFile(StrEnum):
    """
    The files the scene is built from.
    """

    APARTMENT = os.path.join("mjcf", "scene-bodies.xml")
    """
    The apartment's MuJoCo scene, in the ``iai_garmi_apartment`` package.
    """

    BOWL = "bowl.stl"
    """
    The bowl's mesh, among coraplex's object meshes.
    """

    SPOON = "spoon.stl"
    """
    The spoon's mesh, among coraplex's object meshes.
    """

    @property
    def path(self) -> str:
        """
        :return: Where the file is read from.
        """
        if self is SceneFile.APARTMENT:
            # ROS is only needed for this demo, so it is imported here.
            from ament_index_python.packages import get_package_share_directory

            return os.path.join(
                get_package_share_directory(APARTMENT_PACKAGE), self.value
            )
        return str(OBJECT_DIRECTORY / self.value)


class SceneBody(StrEnum):
    """
    The bodies of the scene the task acts on.
    """

    BOWL = "bowl"
    """
    The bowl carried off the counter.
    """

    SPOON = "spoon"
    """
    The spoon carried out of the drawer.
    """

    SPOON_DRAWER = "drawer_1"
    """
    The drawer the spoon starts in.
    """


# %% where things stand


@dataclass(frozen=True)
class ScenePlacement:
    """
    A position and a heading about the vertical.
    """

    x: float
    """
    In metres.
    """

    y: float
    """
    In metres.
    """

    z: float
    """
    In metres.
    """

    yaw: float = 0.0
    """
    The heading about the vertical, in radians.
    """

    @property
    def transform(self) -> HomogeneousTransformationMatrix:
        """
        :return: The placement as a transform.
        """
        return HomogeneousTransformationMatrix.from_xyz_rpy(
            self.x, self.y, self.z, yaw=self.yaw
        )

    @property
    def position(self) -> Point3:
        """
        :return: Where the placement is, without its heading.
        """
        return Point3.from_iterable([self.x, self.y, self.z])


class ScenePose(Enum):
    """
    Where GARMI and the objects start, and where the objects are carried to: those of
    CRAM's demonstration.
    """

    GARMI_START = ScenePlacement(0.0, 6.0, 0.0, yaw=math.pi / 2)
    """
    Where GARMI starts, in its ``odom`` frame, facing the kitchen.
    """

    BOWL_START = ScenePlacement(0.0, 7.2, 1.0)
    """
    Where the bowl starts, on the kitchen counter.
    """

    BOWL_TARGET = ScenePlacement(1.6, 5.2, 0.8)
    """
    Where the bowl is carried to, on the table.
    """

    SPOON_START = ScenePlacement(-0.09, 0.0, -0.069)
    """
    Where the spoon starts, relative to its drawer: on the drawer's bottom plate, clear
    of its walls.
    """

    SPOON_TARGET = ScenePlacement(1.6, 5.4, 0.8)
    """
    Where the spoon is carried to, on the table.
    """


class SamplingSeed(IntEnum):
    """
    The seeds the plan's locations draw from.
    """

    REPEATABLE = 0
    """
    Fixes the poses the plan's locations draw, so each run stands where the one before
    it stood: the locations draw from their costmaps rather than ranking them.
    """


# %% environment


@dataclass
class GarmiApartmentEnvironment(SceneEnvironment):
    """
    The apartment's MuJoCo scene, its furniture annotated, with the bowl on the counter
    and the spoon in a drawer.
    """

    def apartment(self) -> World:
        """
        Annotate the furniture before the bowl and the spoon are added, so the world
        reasoner describes the apartment rather than the objects the plan knows.

        :return: A new world holding the apartment and the objects, and no robot.
        """
        world = WorldSpecification.from_mjcf(
            SceneFile.APARTMENT.path, use_visual_as_collision_backup=True
        ).create_world()
        inferred = WorldReasoner(world).infer_semantic_annotations()
        with world.modify_world():
            world.add_semantic_annotations(inferred)
        # Both objects are carried, and a pose can only be written to a connection
        # with the degrees of freedom to take it, so they hang off 6DoF connections.
        Bowl.get_annotation_specification(
            SceneBody.BOWL,
            BodySpecification.mesh(
                SceneBody.BOWL,
                SceneFile.BOWL.path,
                parent_T_self=ScenePose.BOWL_START.value.transform,
            ),
            parent_connection_specification=Connection6DoFSpecification(),
        ).spawn(world)
        # The spoon hangs off its drawer, so it moves with the drawer when the plan
        # opens it.
        Spoon.get_annotation_specification(
            SceneBody.SPOON,
            BodySpecification.mesh(
                SceneBody.SPOON,
                SceneFile.SPOON.path,
                parent_T_self=ScenePose.SPOON_START.value.transform,
            ),
            parent_connection_specification=Connection6DoFSpecification(),
        ).spawn(world, parent=world.get_body_by_name(SceneBody.SPOON_DRAWER))
        return world

    def create_world(self, robots: Sequence[RobotSpecification] = ()) -> World:
        """
        :param robots: The robots to place in the world, after the apartment.
        :return: A new world holding the apartment, the objects and the robots.
        """
        world = self.apartment()
        for robot in robots:
            robot.spawn(world)
        return world

    def populate(self, world: World) -> None:
        apartment = self.apartment()
        with world.modify_world():
            connection = FixedConnection.create_with_dofs(
                world=world,
                parent=world.root,
                child=apartment.root,
                parent_T_connection_expression=HomogeneousTransformationMatrix(),
            )
            world.merge_world(apartment, root_connection=connection)


# %% the demo


@dataclass
class GarmiApartmentDemo(RobotTaskDemo):
    """
    GARMI carries the bowl off the kitchen counter and the spoon out of a drawer, and
    places both on the table, with its right arm.
    """

    targets: Dict[str, ScenePlacement] = field(
        default_factory=lambda: {
            SceneBody.BOWL: ScenePose.BOWL_TARGET.value,
            SceneBody.SPOON: ScenePose.SPOON_TARGET.value,
        }
    )
    """
    Where each object is carried to, by its body's name.
    """

    def the_robot(self, world: World) -> Garmi:
        """
        :param world: The world built for the task.
        :return: GARMI, found by an entity query.
        """
        robot = variable(Garmi, world.semantic_annotations)
        return the(entity(robot)).first()

    def the_bowl(self, world: World) -> Bowl:
        """
        :param world: The world built for the task.
        :return: The bowl, found by an entity query.
        """
        bowl = variable(Bowl, world.semantic_annotations)
        return the(entity(bowl)).first()

    def the_spoon(self, world: World) -> Spoon:
        """
        :param world: The world built for the task.
        :return: The spoon, found by an entity query.
        """
        spoon = variable(Spoon, world.semantic_annotations)
        return the(entity(spoon)).first()

    def build_context(self, world: World) -> TaskContext:
        """
        :param world: The world built for the task.
        :return: The context of CRAM's demonstration: locations drawn repeatably, and a
            drawer opened before anything is taken out of it.
        """
        return TaskContext(
            world=world,
            robot=self.the_robot(world),
            sampling_seed=SamplingSeed.REPEATABLE,
            extensions=[
                PlanRewriting(transformations=[OpenDrawerBeforeMoveAndPickUp()])
            ],
        )

    def transport(
        self, graspable: HasGraspCandidates, context: TaskContext
    ) -> TransportAction:
        """
        :param graspable: The object to carry.
        :param context: The world and GARMI.
        :return: Carrying it to its target with the right arm, where GARMI stands and
            how it grasps left open.
        """
        target = self.targets[graspable.root.name.name]
        return TransportAction.from_graspable_by_closest_grasps(
            graspable,
            Pose(position=target.position, reference_frame=context.world.root),
            context.robot.get_right_arm_if_specified(),
            context.statechart_context(),
        )

    def build_plan(self, context: TaskContext) -> StatechartNode:
        """
        :param context: The world and GARMI.
        :return: Carrying the bowl and then the spoon to the table.
        """
        world = context.world
        return Sequence(
            nodes=[
                ParkArmsAction(arms=context.robot.all_arms),
                self.transport(self.the_bowl(world), context),
                self.transport(self.the_spoon(world), context),
            ]
        )

    def measure(self, context: TaskContext) -> AcceptanceMetrics:
        """
        :param context: The world and GARMI after the run.
        :return: How far each object ended from its target, in metres.
        :raises TaskFailed: When an object is still held or ended too far from its
            target.
        """
        robot_bodies = set(context.robot.bodies)
        measurements = {}
        for name, target in self.targets.items():
            body = context.world.get_body_by_name(name)
            if body.parent_connection.parent in robot_bodies:
                raise TaskFailed(f"GARMI still holds the {name}")
            position = body.global_pose.to_np()[:3, 3]
            error = float(
                np.linalg.norm(position - np.array([target.x, target.y, target.z]))
            )
            if error > PLACE_TOLERANCE:
                raise TaskFailed(f"the {name} ended {error:.3f} m from its target")
            measurements[f"{name}PlaceError"] = error
        return AcceptanceMetrics(measurements=measurements)


def demo() -> GarmiApartmentDemo:
    """
    :return: The task in the apartment, GARMI at its start.
    """
    return GarmiApartmentDemo(
        environment=GarmiApartmentEnvironment(),
        robot=RobotSpecification(
            semantic_annotation_type=Garmi,
            odometry_T_robot_start=ScenePose.GARMI_START.value.transform,
        ),
        name=NAME,
        task=TASK,
        environment_name=ENVIRONMENT_NAME,
        camera=CAMERA,
        collision_avoidance=True,
    )


def main(arguments: Optional[Sequence[str]] = None) -> None:
    """
    :param arguments: The command's arguments, or this process's by default.
    """
    saved = TaskCommand(demo()).main(
        sys.argv[1:] if arguments is None else list(arguments)
    )
    print(f"saved {saved.scene}")


if __name__ == "__main__":
    main()
