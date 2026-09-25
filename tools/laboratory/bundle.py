"""Build the CRAMERA description and collision bodies of the laboratory bench."""

from __future__ import annotations

import argparse
import json
import math
import struct
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing_extensions import Any

from geometry import LatheMesh, TubeDimensions

# %% artifact names and dimensions


class Artifact(StrEnum):
    """Files comprising the editable scene description."""

    SPECIFICATION = "specification.json"
    SCENE = "scene.json"
    TRAJECTORY = "trajectory.json"
    ENVIRONMENT = "environment.urdf"
    SEMANTICS = "semantics.json"
    GLASS_BOTTOM_COLLISION = "assets/glass_bottom_collision.stl"


class Field(StrEnum):
    """Fields shared by the scene template and the generated initial state."""

    OBJECTS = "objects"
    KEY = "key"
    SPAWN = "spawn"
    MESH = "mesh"
    URDF = "urdf"
    LABORATORY = "laboratory"
    DRAWER = "drawer"
    JOINT = "joint"
    OPEN = "open"
    SCALE = "scale"
    PAN_POSITION = "panPosition"
    PAN_RADIUS = "panRadius"
    DISPLAY_POSITION = "displayPosition"
    DISPLAY_SIZE = "displaySize"
    HOLDER = "holder"
    """Geometry of the open, pan-mounted vessel support."""
    INNER_RADIUS = "innerRadius"
    """Minimum radial clearance within the support."""
    OUTER_RADIUS = "outerRadius"
    """Radial distance to each segment's outer face."""
    HEIGHT = "height"
    """Support height above the weighing surface."""
    SEGMENTS = "segments"
    """Number of tangent box segments forming the support."""


class BalancePart(StrEnum):
    """URDF names identifying the instrument and its measured contact surface."""

    PARENT = "laboratory_root"
    """Fixed laboratory frame supporting the instrument."""
    BODY = "laboratory_balance"
    """Instrument frame at the weighing pan's centre."""
    JOINT = "laboratory_balance_joint"
    """Fixed attachment to the laboratory frame."""
    PAN = "weighing_pan"
    """Cylinder whose contact forces form the balance reading."""
    HOUSING = "housing"
    """Instrument case supporting the pan."""
    HOLDER = "weighing_holder"
    """Open collar transmitting supported vessel loads into the pan."""


@dataclass(frozen=True)
class BalanceHolderDimensions:
    """Open collar dimensions permitting upright support of rounded vessels."""

    inner_radius: float = 0.0115
    """Minimum clear radius inside the tangent support boxes."""
    outer_radius: float = 0.016
    """Radial distance to the outer face of each support box."""
    height: float = 0.04
    """Collar height above the weighing surface."""
    segments: int = 24
    """Number of overlapping tangent support boxes."""

    @property
    def radius(self) -> float:
        """Distance from the pan centre to each support box centre."""
        return (self.inner_radius + self.outer_radius) / 2

    @property
    def size(self) -> tuple[float, float, float]:
        """Radial thickness, tangential width, and height of each box."""
        return (
            self.outer_radius - self.inner_radius,
            2 * self.outer_radius * math.tan(math.pi / self.segments),
            self.height,
        )


@dataclass(frozen=True)
class BalanceDimensions:
    """Metric collision and display dimensions matching the authored instrument."""

    origin: tuple[float, float, float] = (0.65, 0.21, 0.968)
    """Pan centre in the laboratory frame."""
    pan_radius: float = 0.085
    """Radius of the circular load-bearing surface."""
    pan_thickness: float = 0.012
    """Full height of the pan collision cylinder."""
    housing_center: tuple[float, float, float] = (0.0, -0.02, -0.037)
    """Case centre relative to the pan centre."""
    housing_size: tuple[float, float, float] = (0.30, 0.22, 0.058)
    """Full case width, depth, and height."""
    display_position: tuple[float, float, float] = (0.65, 0.076, 0.94)
    """Display centre in the laboratory frame."""
    display_size: tuple[float, float] = (0.12, 0.021)
    """Width and height of the live reading overlay."""

    @property
    def pan_position(self) -> tuple[float, float, float]:
        """Centre of the pan's upper contact surface in the laboratory frame."""
        return (*self.origin[:2], self.origin[2] + self.pan_thickness / 2)


@dataclass(frozen=True)
class RackDimensions:
    """Open rack plate and foot dimensions in its local frame."""

    origin: tuple[float, float, float] = (0.22, -0.04, 0.900)
    """Lower centre of the rack in the laboratory frame."""
    width: float = 0.19
    """Full extent across the three columns."""
    depth: float = 0.11
    """Full extent across the two rows."""
    base_thickness: float = 0.006
    """Thickness of the lower plate."""
    top_bottom: float = 0.062
    """Elevation of the underside of the upper plate."""
    top_thickness: float = 0.006
    """Thickness of the upper plate."""
    hole_radius: float = 0.0115
    """Radius of the open visual holes and half-width of collision apertures."""
    columns: tuple[float, ...] = (-0.06, 0.0, 0.06)
    """Horizontal hole centres."""
    rows: tuple[float, ...] = (-0.025, 0.025)
    """Depth coordinates of the hole centres."""


@dataclass
class UrdfDescription:
    """An articulated body with separate visual and collision geometry."""

    root: ElementTree.Element
    """Robot XML element receiving its links and joints."""

    @classmethod
    def named(cls, name: str) -> UrdfDescription:
        """Start an empty model with the given name."""
        return cls(ElementTree.Element("robot", name=name))

    @staticmethod
    def coordinates(values: tuple[float, ...]) -> str:
        """Serialize metre or radian coordinates without unnecessary precision."""
        return " ".join(f"{value:.8g}" for value in values)

    def link(self, name: str, mesh: str | None = None) -> ElementTree.Element:
        """Add one body, optionally with a material-bearing visual mesh."""
        link = ElementTree.SubElement(self.root, "link", name=name)
        if mesh:
            visual = ElementTree.SubElement(link, "visual")
            geometry = ElementTree.SubElement(visual, "geometry")
            ElementTree.SubElement(geometry, "mesh", filename=mesh)
        return link

    def box(
        self,
        link: ElementTree.Element,
        name: str,
        center: tuple[float, float, float],
        size: tuple[float, float, float],
        yaw: float = 0.0,
    ) -> None:
        """Add one oriented rectangular collision primitive."""
        collision = ElementTree.SubElement(link, "collision", name=name)
        ElementTree.SubElement(
            collision,
            "origin",
            xyz=self.coordinates(center),
            rpy=self.coordinates((0.0, 0.0, yaw)),
        )
        geometry = ElementTree.SubElement(collision, "geometry")
        ElementTree.SubElement(geometry, "box", size=self.coordinates(size))

    def cylinder(
        self,
        link: ElementTree.Element,
        name: str,
        radius: float,
        length: float,
        center_height: float,
    ) -> None:
        """Add an axial collision primitive for a bottom or stopper."""
        collision = ElementTree.SubElement(link, "collision", name=name)
        ElementTree.SubElement(
            collision, "origin", xyz=self.coordinates((0.0, 0.0, center_height))
        )
        geometry = ElementTree.SubElement(collision, "geometry")
        ElementTree.SubElement(
            geometry, "cylinder", radius=str(radius), length=str(length)
        )

    def mesh(self, link: ElementTree.Element, name: str, filename: str) -> None:
        """Add a collision surface expressed in the link's local frame."""
        collision = ElementTree.SubElement(link, "collision", name=name)
        geometry = ElementTree.SubElement(collision, "geometry")
        ElementTree.SubElement(geometry, "mesh", filename=filename)

    def inertia(
        self,
        link: ElementTree.Element,
        mass: float,
        center: tuple[float, float, float],
        size: tuple[float, float, float],
    ) -> None:
        """Assign an estimated positive inertia using a bounding box approximation."""
        inertial = ElementTree.SubElement(link, "inertial")
        ElementTree.SubElement(inertial, "origin", xyz=self.coordinates(center))
        ElementTree.SubElement(inertial, "mass", value=str(mass))
        moments = tuple(
            mass * sum(size[other] ** 2 for other in range(3) if other != axis) / 12
            for axis in range(3)
        )
        ElementTree.SubElement(
            inertial,
            "inertia",
            ixx=str(moments[0]),
            iyy=str(moments[1]),
            izz=str(moments[2]),
            ixy="0",
            ixz="0",
            iyz="0",
        )

    def joint(
        self,
        name: str,
        parent: str,
        child: str,
        origin: tuple[float, float, float],
        travel: float | None = None,
    ) -> None:
        """Connect a fixed frame or a bounded drawer to its parent."""
        joint = ElementTree.SubElement(
            self.root,
            "joint",
            name=name,
            type="fixed" if travel is None else "prismatic",
        )
        ElementTree.SubElement(joint, "parent", link=parent)
        ElementTree.SubElement(joint, "child", link=child)
        ElementTree.SubElement(
            joint, "origin", xyz=self.coordinates(origin), rpy="0 0 0"
        )
        if travel is not None:
            ElementTree.SubElement(joint, "axis", xyz="0 -1 0")
            ElementTree.SubElement(
                joint,
                "limit",
                lower="0",
                upper=str(travel),
                effort="40",
                velocity="0.15",
            )
            ElementTree.SubElement(joint, "dynamics", damping="2", friction="1")

    def write(self, path: Path) -> None:
        """Write a human-readable description with relative asset references."""
        ElementTree.indent(self.root)
        ElementTree.ElementTree(self.root).write(
            path, encoding="utf-8", xml_declaration=True
        )


# %% environment collision geometry


@dataclass
class CollisionMesh:
    """A dimensioned surface exported as a binary STL collision asset."""

    surface: LatheMesh
    """Indexed, consistently wound faces of the closed material shell."""

    def write(self, path: Path) -> None:
        """Triangulate the faces and preserve their metric coordinates and normals."""
        triangles = [
            (face[0], face[index], face[index + 1])
            for face in self.surface.faces
            for index in range(1, len(face) - 1)
        ]
        data = bytearray(b"CRAMERA hollow glass collision".ljust(80, b"\0"))
        data.extend(struct.pack("<I", len(triangles)))
        for triangle in triangles:
            vertices = [self.surface.vertices[index] for index in triangle]
            first = tuple(vertices[1][axis] - vertices[0][axis] for axis in range(3))
            second = tuple(vertices[2][axis] - vertices[0][axis] for axis in range(3))
            normal = tuple(
                first[(axis + 1) % 3] * second[(axis + 2) % 3]
                - first[(axis + 2) % 3] * second[(axis + 1) % 3]
                for axis in range(3)
            )
            length = math.sqrt(sum(component * component for component in normal))
            normal = tuple(component / length for component in normal)
            coordinates = [coordinate for vertex in vertices for coordinate in vertex]
            data.extend(struct.pack("<12fH", *normal, *coordinates, 0))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@dataclass
class LaboratoryBundle:
    """Generate a manual workbench without inventing a robot execution."""

    output: Path
    """Directory holding the generated descriptions and Blender assets."""
    scene: dict[str, Any]
    """Scene template containing poses, controls and validation status."""

    def environment(self) -> None:
        """Build the bench, open rack and independently sliding drawer."""
        description = UrdfDescription.named("precision_laboratory")
        description.link("laboratory_root")
        room = description.link("laboratory_room", "assets/environment.glb")
        description.joint(
            "laboratory_room_joint",
            "laboratory_root",
            "laboratory_room",
            (0.0, 0.0, 0.0),
        )
        description.box(room, "worktop", (0.0, 0.0, 0.88), (1.8, 0.75, 0.04))
        for index, horizontal in enumerate((-0.85, 0.85)):
            for row, depth in enumerate((-0.32, 0.32)):
                description.box(
                    room,
                    f"bench_leg_{index}_{row}",
                    (horizontal, depth, 0.43),
                    (0.04, 0.04, 0.86),
                )
        drawer = self.scene[Field.LABORATORY][Field.DRAWER]
        drawer_link = description.link("laboratory_drawer", "assets/drawer.glb")
        description.joint(
            drawer[Field.JOINT],
            "laboratory_root",
            "laboratory_drawer",
            (-0.52, 0.0, 0.69),
            drawer[Field.OPEN],
        )
        description.box(
            drawer_link, "drawer_bottom", (0.0, 0.0, -0.064), (0.55, 0.62, 0.012)
        )
        for index, horizontal in enumerate((-0.269, 0.269)):
            description.box(
                drawer_link,
                f"drawer_side_{index}",
                (horizontal, 0.0, 0.0),
                (0.012, 0.62, 0.14),
            )
        for index, depth in enumerate((-0.304, 0.304)):
            description.box(
                drawer_link,
                f"drawer_end_{index}",
                (0.0, depth, 0.0),
                (0.55, 0.012, 0.14),
            )
        description.inertia(drawer_link, 3.5, (0.0, 0.0, 0.0), (0.55, 0.62, 0.14))
        dimensions = RackDimensions()
        rack = description.link("laboratory_rack")
        description.joint(
            "laboratory_rack_joint",
            "laboratory_root",
            "laboratory_rack",
            dimensions.origin,
        )
        description.box(
            rack,
            "rack_base",
            (0.0, 0.0, dimensions.base_thickness / 2),
            (dimensions.width, dimensions.depth, dimensions.base_thickness),
        )
        for index, horizontal in enumerate((-0.09, 0.09)):
            description.box(
                rack,
                f"rack_support_{index}",
                (horizontal, 0.0, 0.034),
                (0.01, dimensions.depth, 0.056),
            )
        horizontal_boundaries = [
            -dimensions.width / 2,
            *(
                value + sign * dimensions.hole_radius
                for value in dimensions.columns
                for sign in (-1, 1)
            ),
            dimensions.width / 2,
        ]
        depth_boundaries = [
            -dimensions.depth / 2,
            *(
                value + sign * dimensions.hole_radius
                for value in dimensions.rows
                for sign in (-1, 1)
            ),
            dimensions.depth / 2,
        ]
        for column, (left, right) in enumerate(
            zip(horizontal_boundaries, horizontal_boundaries[1:])
        ):
            for row, (front, back) in enumerate(
                zip(depth_boundaries, depth_boundaries[1:])
            ):
                if column % 2 and row % 2:
                    continue
                description.box(
                    rack,
                    f"slot_web_{column}_{row}",
                    (
                        (left + right) / 2,
                        (front + back) / 2,
                        dimensions.top_bottom + dimensions.top_thickness / 2,
                    ),
                    (right - left, back - front, dimensions.top_thickness),
                )
        self.balance(description)
        description.write(self.output / Artifact.ENVIRONMENT)

    def balance(self, description: UrdfDescription) -> None:
        """Add the instrument's contact bodies and matching browser placement."""
        dimensions = BalanceDimensions()
        balance = description.link(BalancePart.BODY)
        description.joint(
            BalancePart.JOINT,
            BalancePart.PARENT,
            BalancePart.BODY,
            dimensions.origin,
        )
        description.cylinder(
            balance,
            BalancePart.PAN,
            dimensions.pan_radius,
            dimensions.pan_thickness,
            0.0,
        )
        description.box(
            balance,
            BalancePart.HOUSING,
            dimensions.housing_center,
            dimensions.housing_size,
        )
        holder = BalanceHolderDimensions()
        for segment in range(holder.segments):
            angle = math.tau * segment / holder.segments
            description.box(
                balance,
                f"{BalancePart.HOLDER}_{segment}",
                (
                    holder.radius * math.cos(angle),
                    holder.radius * math.sin(angle),
                    (dimensions.pan_thickness + holder.height) / 2,
                ),
                holder.size,
                angle,
            )
        self.scene[Field.LABORATORY][Field.SCALE] = {
            Field.PAN_POSITION: list(dimensions.pan_position),
            Field.PAN_RADIUS: dimensions.pan_radius,
            Field.DISPLAY_POSITION: list(dimensions.display_position),
            Field.DISPLAY_SIZE: list(dimensions.display_size),
            Field.HOLDER: {
                Field.INNER_RADIUS: holder.inner_radius,
                Field.OUTER_RADIUS: holder.outer_radius,
                Field.HEIGHT: holder.height,
                Field.SEGMENTS: holder.segments,
            },
        }

    def tube(self, item: dict[str, Any]) -> None:
        """Provide a hollow compound collision approximation for one loose vessel."""
        dimensions = TubeDimensions()
        description = UrdfDescription.named(item[Field.KEY])
        link = description.link(item[Field.KEY], item[Field.MESH])
        wall_segments = 48
        radius = (dimensions.outer_radius + dimensions.inner_radius) / 2
        wall_height = dimensions.height - dimensions.bottom_radius
        tangent_width = 2 * dimensions.inner_radius * math.tan(math.pi / wall_segments)
        for index in range(wall_segments):
            angle = 2 * math.pi * index / wall_segments
            description.box(
                link,
                f"glass_wall_{index}",
                (
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                    dimensions.bottom_radius + wall_height / 2,
                ),
                (dimensions.wall_thickness, tangent_width, wall_height),
                angle,
            )
        description.mesh(link, "glass_bottom", Artifact.GLASS_BOTTOM_COLLISION)
        description.inertia(
            link,
            0.012,
            (0.0, 0.0, dimensions.height / 2),
            (
                2 * dimensions.outer_radius,
                2 * dimensions.outer_radius,
                dimensions.height,
            ),
        )
        description.write(self.output / item[Field.URDF])

    def stopper(self, item: dict[str, Any]) -> None:
        """Describe the removable stopper as a separate rigid body."""
        description = UrdfDescription.named(item[Field.KEY])
        link = description.link(item[Field.KEY], item[Field.MESH])
        insertion = self.scene[Field.LABORATORY]["stopper"]["insertionDepth"]
        height = item["height"]
        stem_radius = 0.0076
        head_radius = 0.0105
        description.cylinder(
            link, "stopper_stem", stem_radius, insertion, insertion / 2
        )
        description.cylinder(
            link,
            "stopper_head",
            head_radius,
            height - insertion,
            (height + insertion) / 2,
        )
        description.inertia(
            link,
            0.003,
            (0.0, 0.0, height / 2),
            (2 * head_radius, 2 * head_radius, height),
        )
        description.write(self.output / item[Field.URDF])

    def write_json(self, name: Artifact, payload: dict[str, Any]) -> None:
        """Write generated state in a reviewable, Unicode-preserving format."""
        (self.output / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def build(self) -> None:
        """Write descriptions while leaving existing assets and renders intact."""
        self.output.mkdir(parents=True, exist_ok=True)
        self.environment()
        dimensions = TubeDimensions()
        bottom_profile = tuple(
            point
            for point in dimensions.profile()
            if point.height <= dimensions.bottom_radius
        )
        CollisionMesh(LatheMesh.from_profile(bottom_profile)).write(
            self.output / Artifact.GLASS_BOTTOM_COLLISION
        )
        stopper_key = self.scene[Field.LABORATORY]["stopper"][Field.KEY]
        for item in self.scene[Field.OBJECTS]:
            if item[Field.KEY] == stopper_key:
                self.stopper(item)
            else:
                self.tube(item)
        rack = RackDimensions()
        self.write_json(
            Artifact.SEMANTICS,
            {
                "units": "metres",
                "upAxis": "Z",
                "workSurface": {"height": 0.9, "bounds": [-0.9, 0.9, -0.375, 0.375]},
                "rack": {
                    "origin": rack.origin,
                    "holeRadius": rack.hole_radius,
                    "collisionAperture": "square approximation",
                    "slots": self.scene[Field.LABORATORY]["rackSlots"],
                },
                "tube": {
                    "height": dimensions.height,
                    "outerRadius": dimensions.outer_radius,
                    "innerRadius": dimensions.inner_radius,
                    "graspHeight": 0.105,
                    "insertionAxis": [0, 0, -1],
                    "massKilograms": 0.012,
                },
                "physics": {
                    "inertia": "estimated bounding boxes",
                    "contactValidation": "not yet performed",
                    "fluidModel": "separate visual fill bodies",
                    "dynamicRequiresConvexDecomposition": True,
                    "collisionGeometry": "Compound wall boxes with a concave rounded-bottom shell derived from the glass profile; dynamic contact is not validated.",
                },
                "stateScope": "Manual workbench state is local to the browser; no live robot or semantic backend is connected.",
            },
        )
        initial_objects = {
            item[Field.KEY]: item[Field.SPAWN] for item in self.scene[Field.OBJECTS]
        }
        drawer_joint = self.scene[Field.LABORATORY][Field.DRAWER][Field.JOINT]
        self.write_json(
            Artifact.TRAJECTORY,
            {
                "framesPerSecond": 1,
                "frames": [{drawer_joint: 0.0}],
                "objects": [initial_objects],
            },
        )
        self.write_json(Artifact.SCENE, self.scene)


# %% command line


def main() -> None:
    """Build a local scene alongside assets generated by Blender."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path.home() / ".cramera/scenes/precision_lab"
    )
    arguments = parser.parse_args()
    specification = Path(__file__).with_name(Artifact.SPECIFICATION)
    LaboratoryBundle(
        arguments.output, json.loads(specification.read_text(encoding="utf-8"))
    ).build()
    print(arguments.output / Artifact.SCENE)


if __name__ == "__main__":
    main()
