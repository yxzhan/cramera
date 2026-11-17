"""
The regions of a scene a step looks for objects in, as the native boxes CRAM's
perception queries take.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cramera_bridge.exceptions import RobotHasNoTable
from cramera_bridge.plans.site import RobotSite
from cramera_protocol.detection import DetectionRegion, RegionKind
from cramera_protocol.geometry import FloorRectangle
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.robots.robot_part_mixins import HasMountingTable
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from semantic_digital_twin.world_description.geometry import VolumetricBoundingBox
from semantic_digital_twin.world_description.shape_collection import ShapeCollection

# %% regions


@dataclass(frozen=True)
class RegionBoxes:
    """
    Turns the regions a plan names into boxes in the world of the robot that looks.
    """

    robot: AbstractRobot
    """
    The robot looking, whose table the regions lie on.
    """

    site: RobotSite = field(default_factory=RobotSite)
    """
    Where the plan stands the robot, and the robot profile's zones.
    """

    height_above_table: float = 0.3
    """
    How far above the table top, in metres, a region on the table reaches.
    """

    marker_half_width: float = 0.1
    """
    Half the side, in metres, of the square around a marker a region of kind
    :attr:`RegionKind.MARKER` covers.
    """

    def box(self, region: DetectionRegion) -> VolumetricBoundingBox:
        """
        :param region: A region of the plan.
        :return: The box it spans in the world's root frame.
        :raises UnknownZone: For a zone the robot profile does not name.
        """
        match region.kind:
            case RegionKind.TABLE:
                return self.table()
            case RegionKind.ZONE:
                return self.zone(region.zone)
            case RegionKind.MARKER:
                return self.around_marker(region)

    def zone(self, identifier: str) -> VolumetricBoundingBox:
        """
        :param identifier: A zone of the robot profile.
        :return: The box above the zone, from the table top up to
            :attr:`height_above_table`.
        :raises UnknownZone: When the profile names no such zone.
        """
        area = self.site.world_rectangle(self.site.zone(identifier).area)
        return self.above(area, float(self.table().min_z))

    def around_marker(self, region: DetectionRegion) -> VolumetricBoundingBox:
        """
        :param region: A region around a marker.
        :return: The box above the square around the marker, from the surface it rests
            on up to :attr:`height_above_table`.
        """
        marker = region.pose
        area = FloorRectangle(
            min_x=marker.x - self.marker_half_width,
            max_x=marker.x + self.marker_half_width,
            min_y=marker.y - self.marker_half_width,
            max_y=marker.y + self.marker_half_width,
        )
        return self.above(area, marker.z)

    def above(self, area: FloorRectangle, bottom: float) -> VolumetricBoundingBox:
        """
        :param area: A rectangle in world coordinates.
        :param bottom: The height the box starts at.
        :return: The box above the rectangle, :attr:`height_above_table` high.
        """
        return VolumetricBoundingBox(
            min_x=area.min_x,
            min_y=area.min_y,
            min_z=bottom,
            max_x=area.max_x,
            max_y=area.max_y,
            max_z=bottom + self.height_above_table,
            origin=HomogeneousTransformationMatrix(
                reference_frame=self.robot._world.root
            ),
        )

    def table(self) -> VolumetricBoundingBox:
        """
        :return: The box above the whole top of the robot's table, up to
            :attr:`height_above_table`.
        :raises RobotHasNoTable: When the robot is mounted on no table.
        """
        if not isinstance(self.robot, HasMountingTable):
            raise RobotHasNoTable(type(self.robot).__name__)
        table = self.robot.table.root
        world = table._world
        tabletop = max(table.collision, key=lambda shape: shape.scale.x * shape.scale.y)
        top = (
            ShapeCollection([tabletop], reference_frame=table)
            .as_bounding_box_collection_in_frame(world.root)
            .bounding_box()
        )
        return VolumetricBoundingBox(
            min_x=top.min_x,
            min_y=top.min_y,
            min_z=top.max_z,
            max_x=top.max_x,
            max_y=top.max_y,
            max_z=top.max_z + self.height_above_table,
            origin=HomogeneousTransformationMatrix(reference_frame=world.root),
        )
