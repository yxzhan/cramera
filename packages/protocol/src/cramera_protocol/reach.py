"""
What a robot reaches, for the quick check a marker dragged across the scene gets while
it moves: a sphere around each arm's shoulder, which no point outside of the arm's tool
reaches, and the top of the table the robot works at, which a marker snaps to.

Everything is written in the frame of the pose a plan stands the robot at. The full
check of whether a step can be carried out stays the plan's validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import List, Optional

from cramera_protocol.document import JsonDocument, document_field
from cramera_protocol.geometry import FloorRectangle, Vector3

# %% requests


class ReachRequestKey(StrEnum):
    """
    The keys of a request for what a robot reaches.
    """

    ROBOT = "robot"
    """
    The robot model.
    """


@dataclass(kw_only=True)
class ReachRequest(JsonDocument):
    """
    Asks what a robot model reaches.
    """

    robot: str = document_field(ReachRequestKey.ROBOT)
    """
    The identifier of the robot model, as the catalog lists it.
    """


# %% reach


class ArmReachKey(StrEnum):
    """
    The keys of what one arm reaches.
    """

    ARM = "arm"
    """
    The arm.
    """

    SHOULDER = "shoulder"
    """
    Where the arm's first joint turns.
    """

    RADIUS = "radius"
    """
    How far the arm's tool gets from its shoulder.
    """


@dataclass(kw_only=True)
class ArmReach(JsonDocument):
    """
    The sphere one arm's tool stays within, whatever its joints do.
    """

    arm: str = document_field(ArmReachKey.ARM)
    """
    The arm's identifier, as a step names it, such as ``left``.
    """

    shoulder: Vector3 = document_field(ArmReachKey.SHOULDER)
    """
    The sphere's centre, where the arm's first joint turns.
    """

    radius: float = document_field(ArmReachKey.RADIUS)
    """
    The sphere's radius in metres: the length of the arm's links from the shoulder to
    its tool, stretched out.
    """

    def reaches(self, point: Vector3) -> bool:
        """
        :param point: A point in the robot's frame.
        :return: Whether it lies within the sphere.
        """
        return (
            math.dist(
                (point.x, point.y, point.z),
                (self.shoulder.x, self.shoulder.y, self.shoulder.z),
            )
            <= self.radius
        )


class TableTopKey(StrEnum):
    """
    The keys of the top of a robot's table.
    """

    AREA = "area"
    """
    The rectangle the top covers.
    """

    HEIGHT = "height"
    """
    How high the top is.
    """


@dataclass(kw_only=True)
class TableTop(JsonDocument):
    """
    The top of the table a robot is mounted on.
    """

    area: FloorRectangle = document_field(TableTopKey.AREA)
    """
    The rectangle the top covers.
    """

    height: float = document_field(TableTopKey.HEIGHT)
    """
    The top's height in metres.
    """

    def covers(self, x: float, y: float) -> bool:
        """
        :return: Whether the point lies above the top.
        """
        return (
            self.area.min_x <= x <= self.area.max_x
            and self.area.min_y <= y <= self.area.max_y
        )


class RobotReachKey(StrEnum):
    """
    The keys of what a robot reaches.
    """

    ROBOT = "robot"
    """
    The robot model.
    """

    ARMS = "arms"
    """
    What each arm reaches.
    """

    TABLE = "table"
    """
    The top of the robot's table.
    """

    FLOOR_HEIGHT = "floorHeight"
    """
    The height of the floor the robot stands on.
    """


@dataclass(kw_only=True)
class RobotReach(JsonDocument):
    """
    What a robot model reaches and the table it works at, in the frame of the pose a
    plan stands it at.
    """

    robot: str = document_field(RobotReachKey.ROBOT)
    """
    The identifier of the robot model.
    """

    arms: List[ArmReach] = document_field(RobotReachKey.ARMS, default_factory=list)
    """
    What each arm reaches, in the robot's order of arms.
    """

    table: Optional[TableTop] = document_field(RobotReachKey.TABLE, default=None)
    """
    The top of the table the robot is mounted on, or None for a robot on the floor.
    """

    floor_height: float = document_field(RobotReachKey.FLOOR_HEIGHT, default=0.0)
    """
    The height of the floor, which a marker beside the table rests on.
    """

    def reaches(self, point: Vector3) -> bool:
        """
        A point outside every arm's sphere is out of reach for certain; one inside may
        still be, which the plan's validation finds out.

        :param point: A point in the robot's frame.
        :return: Whether any arm's tool may get there.
        """
        return any(arm.reaches(point) for arm in self.arms)

    def surface_height(self, x: float, y: float) -> float:
        """
        :return: The height of the surface below the point: the table's top above it,
            else the floor.
        """
        if self.table is not None and self.table.covers(x, y):
            return self.table.height
        return self.floor_height
