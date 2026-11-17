"""
The robots of a world: their identities, their semantic parts and how a scene bundle and
the live publisher describe them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from typing_extensions import TYPE_CHECKING, Dict, List, Optional

from cramera_bridge.exceptions import UnknownArm, UnknownRobot
from cramera_bridge.geometry import NumericPose, POSE_PRECISION
from cramera_protocol.bundle.scene import PartRole, PartSide, RobotPart
from cramera_protocol.live_messages import RobotInstance
from semantic_digital_twin.robots.robot_parts import AbstractRobot, Arm
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF

if TYPE_CHECKING:
    from semantic_digital_twin.world import World
    from semantic_digital_twin.world_description.world_entity import (
        KinematicStructureEntity,
        SemanticAnnotation,
    )

# %% parts


@dataclass(frozen=True)
class RobotParts:
    """
    The arms and end effectors of one robot, read from its semantic annotations.
    """

    world: World
    """
    The world holding the robot.
    """

    robot: AbstractRobot
    """
    The robot's annotation.
    """

    def arms(self) -> List[Arm]:
        """
        :return: The arms the robot's annotation names as its parts.
        """
        return self.robot.all_arms

    def side_of(self, arm: Arm) -> Optional[PartSide]:
        """
        :param arm: One of the robot's arms.
        :return: The side the robot names the arm on, or None for a robot that names no
            left and right arm.
        """
        if arm is self.robot.get_left_arm_if_specified():
            return PartSide.LEFT
        if arm is self.robot.get_right_arm_if_specified():
            return PartSide.RIGHT
        return None

    def arm_named(self, name: str) -> Arm:
        """
        :param name: The name of an arm annotation.
        :return: The robot's arm of that name.
        :raises UnknownArm: When the robot has no arm of that name.
        """
        for arm in self.arms():
            if str(arm.name) == name:
                return arm
        raise UnknownArm(name)

    def annotations(self) -> List[SemanticAnnotation]:
        """
        :return: Every arm, then its end effector.
        """
        return [part for arm in self.arms() for part in (arm, arm.end_effector)]

    def descriptions(self) -> List[RobotPart]:
        """
        :return: Every arm, then its end effector, with the links of each; an arm's
            links leave out those of its end effector.
        """
        parts: List[RobotPart] = []
        for arm in self.arms():
            end_effector_links = self.link_names(arm.end_effector)
            side = self.side_of(arm)
            parts.append(
                RobotPart(
                    name=str(arm.name),
                    role=PartRole.ARM,
                    side=side,
                    links=sorted(set(self.link_names(arm)) - set(end_effector_links)),
                )
            )
            parts.append(
                RobotPart(
                    name=str(arm.end_effector.name),
                    role=PartRole.END_EFFECTOR,
                    side=side,
                    links=sorted(set(end_effector_links)),
                    attached_to=str(arm.name),
                )
            )
        return parts

    @staticmethod
    def link_names(part: SemanticAnnotation) -> List[str]:
        """
        :param part: A robot part annotation.
        :return: The full names of the part's bodies.
        """
        return [str(body.name) for body in part.bodies]


# %% robots of a world


@dataclass(frozen=True)
class RobotRoster:
    """
    The robots of one world, each identified by the identity of its annotation.
    """

    world: World
    """
    The world whose robots are read.
    """

    def robots(self) -> List[AbstractRobot]:
        """
        :return: The robot annotations, in the order the world holds them.
        """
        return list(self.world.get_semantic_annotations_by_type(AbstractRobot))

    @staticmethod
    def identifier(robot: AbstractRobot) -> str:
        """
        :param robot: A robot annotation.
        :return: Its identity, which no other robot of any world shares.
        """
        return str(robot.id)

    @staticmethod
    def label(robot: AbstractRobot) -> str:
        """
        :param robot: A robot annotation.
        :return: Its display name.
        """
        return str(robot.name)

    def named(self, identifier: str) -> AbstractRobot:
        """
        :param identifier: The identity of a robot.
        :return: The robot with that identity.
        :raises UnknownRobot: When no robot of the world carries it.
        """
        for robot in self.robots():
            if self.identifier(robot) == identifier:
                return robot
        raise UnknownRobot(identifier)

    def model_name(self, robot: AbstractRobot) -> str:
        """
        A name for the robot's model file: its class name, made distinct by its
        identity when several robots share the world.

        :param robot: A robot of the world.
        :return: A name of letters, digits and ``_``.
        """
        name = self.class_model_name(type(robot))
        if len(self.robots()) == 1:
            return name
        return f"{name}_{robot.id.hex}"

    @staticmethod
    def class_model_name(robot_class: type) -> str:
        """
        :param robot_class: A robot class.
        :return: The model name of the only robot of a world being of that class.
        """
        return re.sub(r"[^A-Za-z0-9_]", "_", robot_class.__name__.lower())

    def instance(self, robot: AbstractRobot) -> RobotInstance:
        """
        :param robot: A robot of the world.
        :return: The robot as the live publisher lists it.
        """
        return RobotInstance(
            identifier=self.identifier(robot),
            label=self.label(robot),
            model=type(robot).__name__,
            pose=NumericPose.of_entity(
                self.world, self.description_root(robot)
            ).published(),
            joint_positions=self.joint_positions(robot),
        )

    def description_root(self, robot: AbstractRobot) -> KinematicStructureEntity:
        """
        :param robot: A robot of the world.
        :return: The frame the robot's description starts at, where a viewer draws the
            description: the robot's root when its drive attaches it to its odometry,
            and otherwise the ancestor of the robot's root directly below the world's
            root, as Tracy's table hangs 0.88 m above it.
        """
        entity = robot.root
        if robot.drive is not None:
            return entity
        parent = entity.parent_kinematic_structure_entity
        while parent is not None and parent is not self.world.root:
            entity, parent = parent, parent.parent_kinematic_structure_entity
        return entity

    def root_poses(self) -> Dict[str, NumericPose]:
        """
        :return: The root pose of every robot by its identity.
        """
        return {
            self.identifier(robot): NumericPose.of_entity(self.world, robot.root)
            for robot in self.robots()
        }

    @staticmethod
    def joint_positions(robot: AbstractRobot) -> Dict[str, float]:
        """
        :param robot: A robot annotation.
        :return: The value of each of its movable joints by its prefixed name.
        """
        connections = [body.parent_connection for body in robot.bodies]
        return {
            str(connection.name): round(float(connection.position), POSE_PRECISION)
            for connection in connections
            if isinstance(connection, ActiveConnection1DOF)
        }
