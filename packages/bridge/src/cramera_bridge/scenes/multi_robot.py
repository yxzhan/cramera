"""
Several robots in one world, each placed under a namespace of its own and identified in
the built world by the identity of its annotation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from typing_extensions import Dict, Optional, Sequence, Type

from cramera_bridge.exceptions import InvalidRobotPlacement, RobotPlacementUnavailable
from cramera_bridge.robots import RobotRoster
from cramera_bridge.scenes.environment import SceneEnvironment
from semantic_digital_twin.specifications.robots import RobotSpecification
from semantic_digital_twin.specifications.worlds import WorldSpecification
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF

NAMESPACE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
"""
What a robot namespace looks like: it becomes the prefix of every name of the robot.
"""

NAME_SEPARATOR = "/"
"""
What separates a robot's namespace from the names of its joints.
"""

# %% one robot


@dataclass(frozen=True)
class RobotPlacement:
    """
    One robot of a world: its model, namespace, name and start pose and posture.
    """

    namespace: str
    """
    The prefix of every body, joint and localization frame name of the robot.
    """

    label: str
    """
    The name of the robot's annotation.
    """

    robot_type: Type[AbstractRobot]
    """
    The annotation class that loads and annotates the robot's model.
    """

    pose: HomogeneousTransformationMatrix
    """
    Where the robot's localization frame starts, relative to the world's root.
    """

    joint_positions: Dict[str, float] = field(default_factory=dict)
    """
    Start values of the robot's joints by their full names.
    """

    def __post_init__(self) -> None:
        if NAMESPACE_PATTERN.fullmatch(self.namespace) is None:
            raise InvalidRobotPlacement(
                f"The robot namespace {self.namespace!r} must start with a letter and "
                f"hold only letters, digits and underscores."
            )
        if not self.label.strip():
            raise InvalidRobotPlacement("Every robot needs a name.")
        for name, position in self.joint_positions.items():
            if not name.startswith(f"{self.namespace}{NAME_SEPARATOR}"):
                raise InvalidRobotPlacement(
                    f"The joint {name!r} is not a joint of {self.namespace!r}."
                )
            if not math.isfinite(position):
                raise InvalidRobotPlacement(f"The joint {name!r} needs a finite value.")

    def specification(self) -> RobotSpecification:
        """
        :return: The native description placing the robot at its start pose.
        """
        return RobotSpecification(
            semantic_annotation_type=self.robot_type, localization_pose=self.pose
        )

    def place(self, world: World) -> AbstractRobot:
        """
        Merge the robot into a world and move every name of its bodies, joints and
        degrees of freedom under its namespace, in place of the prefix its description
        gives them, so that robots of one model stay apart.

        :param world: The world receiving the robot.
        :return: The placed robot.
        """
        robot = self.specification().spawn(world)
        described = robot.root.name.prefix
        connections = [body.parent_connection for body in robot.bodies]
        dofs = [dof for connection in connections for dof in connection.dofs]
        with world.modify_world():
            for entity in [*robot.bodies, *dofs]:
                entity.update_name(self.namespaced(entity.name, described))
        # a connection serializes without an identity, which the world's record of a
        # renamed entity needs, so its name is set without being recorded
        for connection in connections:
            connection.name = self.namespaced(connection.name, described)
        return robot

    def namespaced(self, name: PrefixedName, described: str) -> PrefixedName:
        """
        :param name: A name of the robot.
        :param described: The prefix the robot's description gives its names.
        :return: The name under the robot's namespace in place of that prefix; a name
            with another prefix as it is.
        """
        prefix = name.prefix
        if prefix != described and not prefix.startswith(
            f"{described}{NAME_SEPARATOR}"
        ):
            return name
        return PrefixedName(name.name, prefix=self.namespace + prefix[len(described) :])

    def resolve(self, world: World) -> AbstractRobot:
        """
        :param world: A world built with this placement.
        :return: The robot placed under this namespace.
        :raises RobotPlacementUnavailable: When not exactly one robot carries it.
        """
        robots = [
            robot
            for robot in world.get_semantic_annotations_by_type(self.robot_type)
            if robot.root.name.prefix == self.namespace
        ]
        if len(robots) != 1:
            raise RobotPlacementUnavailable(self.namespace, len(robots))
        return robots[0]

    def resolve_by_model(self, world: World) -> AbstractRobot:
        """
        :param world: A world that holds the robot under the names of its own
            description, as the real robot reports it, rather than under this namespace.
        :return: The one robot of this placement's model.
        :raises RobotPlacementUnavailable: When not exactly one robot is of that model.
        """
        robots = world.get_semantic_annotations_by_type(self.robot_type)
        if len(robots) != 1:
            raise RobotPlacementUnavailable(self.namespace, len(robots))
        return robots[0]

    def name_reported_robot(self, world: World) -> AbstractRobot:
        """
        Name the one robot of this placement's model in a world the real robot reports
        as a world built with this placement names it, so that the robot published is
        found under this placement's namespace.

        :param world: A world the real robot reports.
        :return: The robot.
        :raises RobotPlacementUnavailable: When not exactly one robot is of that model.
        """
        robot = self.resolve_by_model(world)
        with world.modify_world():
            robot.update_name(PrefixedName(self.label, prefix=self.namespace))
        return robot

    def restore_joint_positions(self, world: World) -> None:
        """
        :param world: A world built with this placement.
        :raises InvalidRobotPlacement: When a joint value names no movable joint of the
            robot.
        """
        robot = self.resolve(world)
        connections = {
            str(body.parent_connection.name): body.parent_connection
            for body in robot.bodies
            if isinstance(body.parent_connection, ActiveConnection1DOF)
        }
        unknown = sorted(self.joint_positions.keys() - connections.keys())
        if unknown:
            raise InvalidRobotPlacement(
                f"The robot {self.namespace!r} has no movable joints {unknown}."
            )
        for name, position in self.joint_positions.items():
            connections[name].position = position


# %% several robots


@dataclass
class RobotScene:
    """
    Robots sharing one world, and the robot selected for the next plan.
    """

    placements: Sequence[RobotPlacement]
    """
    The robots; their namespaces differ.
    """

    selected_namespace: str
    """
    The namespace of the robot selected for the next plan.
    """

    def __post_init__(self) -> None:
        self.placements = tuple(self.placements)
        namespaces = [placement.namespace for placement in self.placements]
        if not namespaces:
            raise InvalidRobotPlacement("A robot scene needs at least one robot.")
        if len(namespaces) != len(set(namespaces)):
            raise InvalidRobotPlacement("The robots' namespaces must differ.")
        if self.selected_namespace not in namespaces:
            raise InvalidRobotPlacement(
                f"The selected robot {self.selected_namespace!r} is not in the scene."
            )

    def build_world(self, environment: Optional[SceneEnvironment] = None) -> World:
        """
        :param environment: The scene the robots stand in, or None for an empty world.
        :return: A new world holding every robot at its start pose and posture.
        """
        world = WorldSpecification().create_world()
        for placement in self.placements:
            placement.place(world)
        with world.modify_world():
            for placement in self.placements:
                placement.resolve(world).update_name(
                    PrefixedName(placement.label, prefix=placement.namespace)
                )
        for placement in self.placements:
            placement.restore_joint_positions(world)
        if environment is not None:
            environment.populate(world)
        return world

    def identifiers(self, world: World) -> Dict[str, str]:
        """
        :param world: A world built from this scene.
        :return: The identity of every placed robot by its namespace.
        """
        return {
            placement.namespace: RobotRoster.identifier(placement.resolve(world))
            for placement in self.placements
        }

    def selected_placement(self) -> RobotPlacement:
        """
        :return: The placement of the robot selected for the next plan.
        """
        return next(
            placement
            for placement in self.placements
            if placement.namespace == self.selected_namespace
        )

    def selected_robot(self, world: World) -> AbstractRobot:
        """
        :param world: A world built from this scene.
        :return: The robot selected for the next plan.
        """
        return self.selected_placement().resolve(world)
