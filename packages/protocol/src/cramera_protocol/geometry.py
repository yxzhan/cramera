"""
Positions, orientations and areas as the protocol writes them: plain number arrays and
objects, in metres, z up.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import Any, List, Self

from cramera_protocol.document import JsonDocument, JsonValue, document_field
from cramera_protocol.exceptions import InvalidDocumentValue

# %% fixed-length number arrays


@dataclass(frozen=True)
class NumberArray(JsonValue):
    """
    A JSON array of a fixed number of numbers.
    """

    @classmethod
    @abstractmethod
    def length(cls) -> int:
        """
        :return: How many numbers the array holds.
        """

    @classmethod
    def from_json(cls, data: Any) -> Self:
        if (
            not isinstance(data, list)
            or len(data) != cls.length()
            or not all(
                isinstance(number, (int, float)) and not isinstance(number, bool)
                for number in data
            )
        ):
            raise InvalidDocumentValue(
                cls.__name__, "", data, f"{cls.length()} numbers"
            )
        return cls(*(float(number) for number in data))

    def to_json(self) -> List[float]:
        return list(vars(self).values())


@dataclass(frozen=True)
class Vector3(NumberArray):
    """
    A point or direction in metres.
    """

    x: float
    """
    The x coordinate.
    """

    y: float
    """
    The y coordinate.
    """

    z: float
    """
    The z coordinate.
    """

    @classmethod
    def length(cls) -> int:
        return 3


@dataclass(frozen=True)
class Pose(NumberArray):
    """
    A position and a unit quaternion, written as ``[x, y, z, qx, qy, qz, qw]``.
    """

    x: float
    """
    The x coordinate of the position.
    """

    y: float
    """
    The y coordinate of the position.
    """

    z: float
    """
    The z coordinate of the position.
    """

    qx: float
    """
    The x component of the orientation quaternion.
    """

    qy: float
    """
    The y component of the orientation quaternion.
    """

    qz: float
    """
    The z component of the orientation quaternion.
    """

    qw: float
    """
    The scalar component of the orientation quaternion.
    """

    @classmethod
    def length(cls) -> int:
        return 7


# %% boxes


@dataclass(frozen=True)
class Bounds(JsonValue):
    """
    An axis-aligned box in metres, written as ``[[min x, min y, min z], [max x, max y,
    max z]]``.
    """

    minimum: Vector3
    """
    The corner with the smallest coordinates.
    """

    maximum: Vector3
    """
    The corner with the largest coordinates.
    """

    @classmethod
    def from_json(cls, data: Any) -> Self:
        if not isinstance(data, list) or len(data) != 2:
            raise InvalidDocumentValue(cls.__name__, "", data, "two corners")
        return cls(Vector3.from_json(data[0]), Vector3.from_json(data[1]))

    def to_json(self) -> List[List[float]]:
        return [self.minimum.to_json(), self.maximum.to_json()]


# %% areas


class RectangleKey(StrEnum):
    """
    The keys of an axis-aligned rectangle on the floor plane.
    """

    MIN_X = "minX"
    """
    The smallest x coordinate.
    """

    MAX_X = "maxX"
    """
    The largest x coordinate.
    """

    MIN_Y = "minY"
    """
    The smallest y coordinate.
    """

    MAX_Y = "maxY"
    """
    The largest y coordinate.
    """


@dataclass(kw_only=True)
class FloorRectangle(JsonDocument):
    """
    An axis-aligned rectangle in the horizontal plane, in metres.
    """

    min_x: float = document_field(RectangleKey.MIN_X)
    """
    The smallest x coordinate.
    """

    max_x: float = document_field(RectangleKey.MAX_X)
    """
    The largest x coordinate.
    """

    min_y: float = document_field(RectangleKey.MIN_Y)
    """
    The smallest y coordinate.
    """

    max_y: float = document_field(RectangleKey.MAX_Y)
    """
    The largest y coordinate.
    """


class BoxKey(StrEnum):
    """
    The keys of an axis-aligned box.
    """

    MIN_X = "minX"
    """
    The smallest x coordinate.
    """

    MAX_X = "maxX"
    """
    The largest x coordinate.
    """

    MIN_Y = "minY"
    """
    The smallest y coordinate.
    """

    MAX_Y = "maxY"
    """
    The largest y coordinate.
    """

    MIN_Z = "minZ"
    """
    The smallest z coordinate.
    """

    MAX_Z = "maxZ"
    """
    The largest z coordinate.
    """


@dataclass(kw_only=True)
class AxisAlignedBox(JsonDocument):
    """
    A box whose sides are parallel to the axes of the frame it is written in, in metres.
    """

    min_x: float = document_field(BoxKey.MIN_X)
    """
    The smallest x coordinate.
    """

    max_x: float = document_field(BoxKey.MAX_X)
    """
    The largest x coordinate.
    """

    min_y: float = document_field(BoxKey.MIN_Y)
    """
    The smallest y coordinate.
    """

    max_y: float = document_field(BoxKey.MAX_Y)
    """
    The largest y coordinate.
    """

    min_z: float = document_field(BoxKey.MIN_Z)
    """
    The smallest z coordinate.
    """

    max_z: float = document_field(BoxKey.MAX_Z)
    """
    The largest z coordinate.
    """
