"""
Where a step puts an object down when that place is only found once the plan is
grounded: into a named zone of the robot profile, on top of another object, or at a
marker dragged across the scene. Each is resolved against the world as the steps before
left it, so a stack follows the objects placed before it.

Also what the robot's arms reach at most, the quick check of a marker and of a zone's
spots; the plan's validation stays the full check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property

import numpy as np
from typing_extensions import Dict, Iterator, List, Optional, Tuple

from cramera_bridge.exceptions import (
    NoFreeSpotInZone,
    PlacementNotFound,
    UnknownSupportingObject,
    UnknownZone,
)
from cramera_bridge.names import ENUM_NAMES
from cramera_bridge.plans.arms import Arms, RobotArms
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.site import RobotSite
from cramera_protocol.geometry import FloorRectangle
from cramera_protocol.geometry import Pose as PlannedPose
from cramera_protocol.geometry import Vector3
from cramera_protocol.plan_builder import Destination, PlacementMode
from cramera_protocol.reach import ArmReach
from krrood_spatial_math.spatial_types import Pose
from semantic_digital_twin.collision_checking.collision_rules import (
    AvoidCollisionRule,
)
from semantic_digital_twin.datastructures.definitions import GripperState
from semantic_digital_twin.robots.robot_parts import AbstractRobot, Arm, EndEffector
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import ActiveConnection
from semantic_digital_twin.world_description.geometry import AxisAlignedBox
from semantic_digital_twin.world_description.world_entity import Body

# %% reach


@dataclass(frozen=True)
class ArmReaches:
    """
    The sphere around each arm's shoulder that its tool stays within: the arm's links
    from its first moving joint to its tool, stretched out.

    Revolute joints turn about the origin of the link they move, so no pose of the arm
    puts the tool farther.
    """

    robot: AbstractRobot
    """
    The robot.
    """

    def arms(self) -> List[ArmReach]:
        """
        :return: The sphere of each arm, in the world's root frame.
        """
        return [self.arm_reach(arm) for arm in self.robot.all_arms]

    def arm_reach(self, arm: Arm) -> ArmReach:
        """
        :param arm: An arm of the robot.
        :return: The sphere its tool stays within.
        """
        world = self.robot._world
        connections = world.compute_chain_of_connections(
            arm.root, arm.end_effector.tool_frame
        )
        first_moving = next(
            index
            for index, connection in enumerate(connections)
            if isinstance(connection, ActiveConnection)
        )
        points = [connections[first_moving].child.global_pose.to_np()[:3, 3]] + [
            connection.child.global_pose.to_np()[:3, 3]
            for connection in connections[first_moving + 1 :]
        ]
        radius = sum(
            float(np.linalg.norm(after - before))
            for before, after in zip(points, points[1:])
        )
        shoulder = points[0]
        return ArmReach(
            arm=ENUM_NAMES.member_identifier(
                Arms, RobotArms(self.robot).find_side(arm)
            ),
            shoulder=Vector3(*(float(coordinate) for coordinate in shoulder)),
            radius=radius,
        )

    def reaches(self, x: float, y: float, z: float) -> bool:
        """
        :return: Whether any arm's tool may get to the point in world coordinates.
        """
        point = Vector3(x, y, z)
        return any(arm.reaches(point) for arm in self.arms())


# %% objects


@dataclass(frozen=True)
class ObjectExtent:
    """
    How far an object reaches: its collision shapes' box in its own frame and in the
    world.
    """

    body: Body
    """
    The object.
    """

    def own_box(self) -> AxisAlignedBox:
        """
        :return: The box around the object's collision shapes in its own frame.
        """
        return self.body.collision.as_bounding_box_collection_in_frame(
            self.body
        ).bounding_box()

    def world_box(self) -> AxisAlignedBox:
        """
        :return: The box around the object's collision shapes in the world's root
            frame.
        """
        return self.body.collision.as_bounding_box_collection_in_frame(
            self.body._world.root
        ).bounding_box()

    def bottom_offset(self) -> float:
        """
        :return: How far the object's origin lies above its lowest point, so it rests on
            a surface rather than sinking into it.
        """
        return -float(self.own_box().min_z)

    def half_width(self) -> float:
        """
        :return: How far the object reaches from its origin along its own horizontal
            axes, at most, so it fits a square of twice that side kept in its
            orientation.
        """
        box = self.own_box()
        return float(
            max(abs(box.min_x), abs(box.max_x), abs(box.min_y), abs(box.max_y))
        )


@dataclass(frozen=True)
class GripperPart:
    """
    A body of a gripper and the box around its collision shapes, in the world's root
    frame.
    """

    body: Body
    """
    The body.
    """

    low: np.ndarray
    """
    The box's lowest corner.
    """

    high: np.ndarray
    """
    The box's highest corner.
    """

    @classmethod
    def of_body(cls, body: Body) -> GripperPart:
        """
        :param body: A body of a gripper with collision shapes.
        :return: The part as the body stands now.
        """
        low, high = cls.corners(ObjectExtent(body).world_box())
        return cls(body=body, low=low, high=high)

    @staticmethod
    def corners(box: AxisAlignedBox) -> Tuple[np.ndarray, np.ndarray]:
        """
        :param box: A box.
        :return: Its lowest and its highest corner.
        """
        return (
            np.array([box.min_x, box.min_y, box.min_z], dtype=float),
            np.array([box.max_x, box.max_y, box.max_z], dtype=float),
        )

    def shifted(self, shift: np.ndarray) -> GripperPart:
        """
        :param shift: How far the gripper moves.
        :return: The part with the gripper moved that far.
        """
        return GripperPart(body=self.body, low=self.low + shift, high=self.high + shift)

    def reaches_into(self, box: AxisAlignedBox, distance: float) -> bool:
        """
        :param box: The box around another object, in the world's root frame.
        :param distance: How far the part has to keep from the object.
        :return: Whether the part comes closer to the box than that distance.
        """
        other_low, other_high = self.corners(box)
        return bool(
            np.all(self.low - distance < other_high)
            and np.all(self.high + distance > other_low)
        )


@dataclass(frozen=True)
class OpenedGripper:
    """
    The room a gripper takes once it opens where it stands, as it does to let go of
    what it holds.
    """

    world: World
    """
    The world the gripper stands in.
    """

    end_effector: EndEffector
    """
    The gripper.
    """

    def parts(self) -> List[GripperPart]:
        """
        :return: Each body of the gripper with collision shapes, the gripper opened,
            read inside a reset state context that stands the world back up afterwards.
        """
        with self.world.reset_state_context():
            if self.end_effector.has_joint_state_of_type(GripperState.OPEN):
                self.end_effector.get_joint_state_by_type(GripperState.OPEN).apply_to(
                    self.world
                )
            return [
                GripperPart.of_body(body)
                for body in self.end_effector.bodies_with_collision
            ]


# %% targets


@dataclass(frozen=True)
class PlacingTargets:
    """
    Resolves where a step puts its object down, in the world as it is now.
    """

    world: World
    """
    The world as the steps before left it.
    """

    robot: AbstractRobot
    """
    The robot that puts the object down.
    """

    site: RobotSite
    """
    Where the plan stands the robot, and the profile's zones.
    """

    bodies: Dict[str, Body]
    """
    The plan's objects by their identity, and the objects detections find by their
    references.
    """

    object_identifier: str
    """
    The identity of the object put down.
    """

    clearance: float = 0.01
    """
    How far, in metres, an object put into a zone keeps at least from the objects
    already there; the spots of a zone's grid lie twice as far apart.
    """

    release_height: float = 0.005
    """
    How far, in metres, above the surface below an object is let go, so a held object
    pressed against the surface or the object below cannot keep the gripper from its
    goal; it drops that far.
    """

    def pose(self, destination: Destination) -> Pose:
        """
        :param destination: A destination resolved once the plan is grounded.
        :return: The pose the object is put down at.
        :raises PlacementNotFound: When the destination resolves to no pose, see
            :meth:`not_found`.
        """
        pose = self.find_pose(destination)
        if pose is None:
            raise self.not_found(destination)
        return pose

    def find_pose(self, destination: Destination) -> Optional[Pose]:
        """
        :param destination: A destination resolved once the plan is grounded.
        :return: The pose the object is put down at, or None when the destination
            resolves to none.
        """
        if destination.placement is PlacementMode.ZONE:
            return self.in_zone(destination.zone)
        if destination.placement is PlacementMode.ON_TOP_OF:
            return self.on_top_of(destination.object)
        return self.at_marker(destination.pose)

    def not_found(self, destination: Destination) -> PlacementNotFound:
        """
        :param destination: A destination that resolves to no pose, see
            :meth:`find_pose`.
        :return: Why it resolves to none.
        """
        if destination.placement is not PlacementMode.ZONE:
            return UnknownSupportingObject(destination.object)
        if self.site.find_zone(destination.zone) is None:
            return UnknownZone(destination.zone)
        return NoFreeSpotInZone(destination.zone, self.object_identifier)

    @property
    def body(self) -> Body:
        """
        The object put down.
        """
        return self.bodies[self.object_identifier]

    def on_top_of(self, identifier: str) -> Optional[Pose]:
        """
        :param identifier: The identity of the object below, or the reference to the
            object a detection finds.
        :return: The object put down resting centred on the top of the object below,
            keeping its current orientation, or None when the plan holds no such
            object.
        """
        if identifier not in self.bodies:
            return None
        below = self.bodies[identifier]
        top = float(ObjectExtent(below).world_box().max_z)
        position = below.global_pose.to_np()[:3, 3]
        return self.resting(float(position[0]), float(position[1]), top)

    def at_marker(self, marker: PlannedPose) -> Pose:
        """
        :param marker: The marker's pose, on the surface it rests on.
        :return: The object put down resting on that surface at the marker, turned to
            its heading.
        """
        return Pose.from_xyz_quaternion(
            marker.x,
            marker.y,
            marker.z + ObjectExtent(self.body).bottom_offset() + self.release_height,
            marker.qx,
            marker.qy,
            marker.qz,
            marker.qw,
            reference_frame=self.world.root,
        )

    def in_zone(self, identifier: str) -> Optional[Pose]:
        """
        :param identifier: A zone of the robot profile.
        :return: The object put down resting on the table at the zone's free spot
            within reach that lies nearest its centre, or None when the profile names
            no such zone or every spot is taken or out of reach.
        """
        zone = self.site.find_zone(identifier)
        if zone is None:
            return None
        height = float(RegionBoxes(self.robot).table().min_z)
        lift = ObjectExtent(self.body).bottom_offset()
        reach = ArmReaches(self.robot)
        return next(
            (
                spot
                for spot in (
                    self.resting(x, y, height) for x, y in self.spots(zone.area)
                )
                if self.is_free(spot)
                and reach.reaches(*spot.to_np()[:2, 3], height + lift)
            ),
            None,
        )

    def spots(self, area: FloorRectangle) -> Iterator[Tuple[float, float]]:
        """
        :param area: A zone's rectangle in the robot's frame.
        :return: The world coordinates of the centres of a grid of spots the object
            fits in the zone, one object and twice its clearance apart, nearest the
            zone's centre first.
        """
        margin = ObjectExtent(self.body).half_width()
        spacing = 2 * (margin + self.clearance)
        centre_x = (area.min_x + area.max_x) / 2
        centre_y = (area.min_y + area.max_y) / 2
        steps_x = max(0, math.floor(((area.max_x - area.min_x) / 2 - margin) / spacing))
        steps_y = max(0, math.floor(((area.max_y - area.min_y) / 2 - margin) / spacing))
        offsets = sorted(
            (
                (i * spacing, j * spacing)
                for i in range(-steps_x, steps_x + 1)
                for j in range(-steps_y, steps_y + 1)
            ),
            key=lambda offset: (math.hypot(*offset), offset),
        )
        for offset_x, offset_y in offsets:
            yield self.site.to_world(centre_x + offset_x, centre_y + offset_y)

    def is_free(self, spot: Pose) -> bool:
        """
        :param spot: Where the object is put down, as :meth:`resting` gives it.
        :return: Whether the object, put down there, keeps its clearance from every
            other object of the plan, and the gripper holding it, opened there to let
            go, keeps from each the distance the robot's collision avoidance keeps it
            at.
        """
        return self.find_obstacle(spot) is None

    def find_taker(self, spot: Pose) -> Optional[str]:
        """
        :param spot: Where the object is put down.
        :return: The identity of the first other object of the plan that keeps the
            object from being put down there, see :meth:`is_free`; None when none does.
        """
        obstacle = self.find_obstacle(spot)
        if obstacle is None:
            return None
        return next(
            identifier for identifier, body in self.bodies.items() if body is obstacle
        )

    def find_obstacle(self, spot: Pose) -> Optional[Body]:
        """
        :param spot: Where the object is put down.
        :return: The first other object of the plan that keeps the object from being put
            down there, see :meth:`is_free`; None when none does.
        """
        position = spot.to_np()[:3, 3]
        reach = ObjectExtent(self.body).half_width() + self.clearance
        shift = position - self.body.global_pose.to_np()[:3, 3]
        for other in self.others():
            box = ObjectExtent(other).world_box()
            if (
                position[0] + reach > box.min_x
                and position[0] - reach < box.max_x
                and position[1] + reach > box.min_y
                and position[1] - reach < box.max_y
            ):
                return other
            if any(
                part.shifted(shift).reaches_into(
                    box, self.gripper_clearance(part.body, other)
                )
                for part in self.opened_gripper
            ):
                return other
        return None

    @cached_property
    def opened_gripper(self) -> List[GripperPart]:
        """
        The gripper holding the object, opened to let go of it where it is now; none
        when no gripper holds it.
        """
        for arm in self.robot.all_arms:
            if self.body in arm.end_effector.held_bodies:
                return OpenedGripper(self.world, arm.end_effector).parts()
        return []

    def gripper_clearance(self, part: Body, other: Body) -> float:
        """
        :param part: A body of the gripper.
        :param other: Another object of the plan.
        :return: How far the robot's collision avoidance keeps the part from the
            object, else :attr:`clearance`.
        """
        rules = reversed(self.world.collision_manager.rules)
        distances = (
            rule.buffer_zone_distance_for(part, other)
            for rule in rules
            if isinstance(rule, AvoidCollisionRule)
        )
        return next(
            (distance for distance in distances if distance is not None),
            self.clearance,
        )

    def others(self) -> List[Body]:
        """
        :return: The plan's objects other than the one put down, each once.
        """
        others: List[Body] = []
        for body in self.bodies.values():
            if body is not self.body and all(body is not seen for seen in others):
                others.append(body)
        return others

    def resting(self, x: float, y: float, surface: float) -> Pose:
        """
        :param x: The x coordinate in world coordinates.
        :param y: The y coordinate in world coordinates.
        :param surface: The height of the surface the object rests on.
        :return: The object's pose resting on the surface at the point, in its current
            orientation, :attr:`release_height` above it.
        """
        orientation = self.body.global_pose.orientation.to_np()
        return Pose.from_xyz_quaternion(
            x,
            y,
            surface + ObjectExtent(self.body).bottom_offset() + self.release_height,
            *orientation[:4],
            reference_frame=self.world.root,
        )
