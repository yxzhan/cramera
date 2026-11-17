"""
Where a plan stands the robot that performs it, and the robot profile's named zones on
the robot's table, which are written in the frame of that pose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from typing_extensions import List, Optional, Tuple

from cramera_bridge.exceptions import UnknownZone
from cramera_protocol.geometry import FloorRectangle
from cramera_protocol.geometry import Pose as PlannedPose
from cramera_protocol.plan_builder import DemoDocument
from cramera_protocol.robot_profile import Zone

# %% the robot's site


@dataclass(frozen=True)
class RobotSite:
    """
    Where a plan stands the robot that performs it, and the robot profile's zones on its
    table, which are written in the frame of that pose.
    """

    pose: PlannedPose = PlannedPose(0, 0, 0, 0, 0, 0, 1)
    """
    The pose the plan stands the performing robot at, in world coordinates.
    """

    zones: List[Zone] = field(default_factory=list)
    """
    The robot profile's named zones.
    """

    @classmethod
    def of_demo(cls, document: DemoDocument) -> RobotSite:
        """
        :param document: A plan and its environment.
        :return: The site of the plan's performing robot, with the document's zones.
        """
        robot = document.plan.performing_robot()
        if robot is None:
            return cls(zones=list(document.zones))
        return cls(pose=robot.pose, zones=list(document.zones))

    def zone(self, identifier: str) -> Zone:
        """
        :param identifier: A zone's identifier.
        :return: The zone.
        :raises UnknownZone: When the profile names no such zone.
        """
        zone = self.find_zone(identifier)
        if zone is None:
            raise UnknownZone(identifier)
        return zone

    def find_zone(self, identifier: str) -> Optional[Zone]:
        """
        :param identifier: A zone's identifier.
        :return: The zone, or None when the profile names no such zone.
        """
        return next(
            (zone for zone in self.zones if zone.identifier == identifier), None
        )

    def to_world(self, x: float, y: float) -> Tuple[float, float]:
        """
        :param x: An x coordinate in the robot's frame.
        :param y: A y coordinate in the robot's frame.
        :return: The point in world coordinates, the robot standing upright.
        """
        yaw = self.yaw()
        return (
            self.pose.x + math.cos(yaw) * x - math.sin(yaw) * y,
            self.pose.y + math.sin(yaw) * x + math.cos(yaw) * y,
        )

    def yaw(self) -> float:
        """
        :return: The robot's heading about the vertical, in radians.
        """
        pose = self.pose
        return math.atan2(
            2 * (pose.qw * pose.qz + pose.qx * pose.qy),
            1 - 2 * (pose.qy * pose.qy + pose.qz * pose.qz),
        )

    def world_rectangle(self, area: FloorRectangle) -> FloorRectangle:
        """
        :param area: A rectangle in the robot's frame.
        :return: The axis-aligned rectangle around it in world coordinates.
        """
        corners = [
            self.to_world(x, y)
            for x in (area.min_x, area.max_x)
            for y in (area.min_y, area.max_y)
        ]
        xs = [corner[0] for corner in corners]
        ys = [corner[1] for corner in corners]
        return FloorRectangle(
            min_x=min(xs), max_x=max(xs), min_y=min(ys), max_y=max(ys)
        )
