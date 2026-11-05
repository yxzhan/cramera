"""
Small worlds built from native CRAM classes for the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy
import PIL.Image
import trimesh
from typing_extensions import List

from cramera_bridge.performing import TaskContext
from semantic_digital_twin.adapters.urdf import URDFParser
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.robots.minimal_robot import MinimalRobot
from semantic_digital_twin.robots.pr2 import PR2
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import (
    Connection6DoF,
    FixedConnection,
)
from semantic_digital_twin.world_description.geometry import (
    Box,
    Color,
    Cylinder,
    Mesh,
    Scale,
    Texture,
)
from semantic_digital_twin.world_description.shape_collection import ShapeCollection
from semantic_digital_twin.world_description.world_entity import Body

MESHES = Path(__file__).parent / "meshes"
"""
The mesh files the test worlds use.
"""

CUP_MESH = MESHES / "cup.obj"
"""
An OBJ mesh with a material library beside it.
"""

ROBOT_NAMES = ("carrier", "refiller")
"""
The names of the robots of :class:`TwoRobots`, in the order they perform their plans.
"""

# %% kitchen


@dataclass
class KitchenWorld:
    """
    A world with a fixed table, a cylinder milk carton, a mesh cup and a frame without
    shapes, the last three held by six degrees of freedom.
    """

    world: World
    """
    The world.
    """

    root: Body
    """
    The world's root, ``map``.
    """

    table: Body
    """
    A box fixed to the root.
    """

    milk: Body
    """
    A free cylinder standing on the table.
    """

    cup: Body
    """
    A free body drawn from :data:`CUP_MESH`.
    """

    frame: Body
    """
    A free body without any shapes.
    """

    milk_connection: Connection6DoF
    """
    The connection holding the milk.
    """

    @classmethod
    def build(cls) -> KitchenWorld:
        """
        :return: A new kitchen world.
        """
        world = World()
        root = Body(name=PrefixedName("map"))
        table = Body(
            name=PrefixedName("table", "kitchen"),
            visual=ShapeCollection(
                shapes=[
                    Box(
                        scale=Scale(1.0, 0.6, 0.05),
                        color=Color.from_rgba(0.6, 0.4, 0.2),
                    )
                ]
            ),
        )
        milk = Body(
            name=PrefixedName("milk", "kitchen"),
            visual=ShapeCollection(
                shapes=[
                    Cylinder(
                        width=0.06,
                        height=0.2,
                        color=Color.from_rgba(0.2, 0.4, 0.8, 0.5),
                    )
                ]
            ),
        )
        cup = Body(
            name=PrefixedName("cup", "kitchen"),
            visual=ShapeCollection(shapes=[Mesh(filename=str(CUP_MESH))]),
        )
        frame = Body(name=PrefixedName("frame", "kitchen"))
        with world.modify_world():
            world.add_kinematic_structure_entity(root)
            world.add_connection(
                FixedConnection(
                    parent=root,
                    child=table,
                    parent_T_connection_expression=HomogeneousTransformationMatrix.from_xyz_rpy(
                        1.0, 0.0, 0.75
                    ),
                )
            )
            milk_connection = Connection6DoF.create_with_dofs(
                parent=root, child=milk, world=world
            )
            world.add_connection(milk_connection)
            world.add_connection(
                Connection6DoF.create_with_dofs(parent=root, child=cup, world=world)
            )
            world.add_connection(
                Connection6DoF.create_with_dofs(parent=root, child=frame, world=world)
            )
        milk_connection.origin = HomogeneousTransformationMatrix.from_xyz_rpy(
            1.0, 0.1, 0.9, reference_frame=root
        )
        return cls(
            world=world,
            root=root,
            table=table,
            milk=milk,
            cup=cup,
            frame=frame,
            milk_connection=milk_connection,
        )


# %% textured surfaces

BRICKS_TEXTURE = Path(__file__).parent / "textures" / "bricks.png"
"""
A small texture image.
"""

UV_MAPPED_MESH = MESHES / "uv_mapped" / "panel.obj"
"""
An OBJ mesh with texture coordinates but no material library, as MuJoCo scenes ship
them.
"""


@dataclass
class TexturedWorld:
    """
    A world with a textured wall, a textured panel whose texture its mesh file does not
    name, and the same panel without a texture, each fixed to the root.
    """

    world: World
    """
    The world.
    """

    wall: Body
    """
    A box carrying :data:`BRICKS_TEXTURE`, repeated per meter.
    """

    panel: Body
    """
    A body drawn from :data:`UV_MAPPED_MESH` with :data:`BRICKS_TEXTURE` applied.
    """

    bare_panel: Body
    """
    A body drawn from :data:`UV_MAPPED_MESH` alone.
    """

    @classmethod
    def build(cls) -> TexturedWorld:
        """
        :return: A new textured world.
        """
        world = World()
        root = Body(name=PrefixedName("map"))
        wall = Body(
            name=PrefixedName("wall", "kitchen"),
            visual=ShapeCollection(
                shapes=[
                    Box(
                        scale=Scale(2.0, 0.1, 1.0),
                        texture=Texture(
                            file_path=str(BRICKS_TEXTURE),
                            repeat=(3.0, 3.0),
                            uniform=True,
                        ),
                    )
                ]
            ),
        )
        panel = Body(
            name=PrefixedName("panel", "kitchen"),
            visual=ShapeCollection(
                shapes=[
                    Mesh.from_file(
                        file_path=str(UV_MAPPED_MESH),
                        texture_file_path=str(BRICKS_TEXTURE),
                    )
                ]
            ),
        )
        bare_panel = Body(
            name=PrefixedName("bare_panel", "kitchen"),
            visual=ShapeCollection(shapes=[Mesh(filename=str(UV_MAPPED_MESH))]),
        )
        with world.modify_world():
            world.add_kinematic_structure_entity(root)
            for body in (wall, panel, bare_panel):
                world.add_connection(FixedConnection(parent=root, child=body))
        return cls(world=world, wall=wall, panel=panel, bare_panel=bare_panel)


@dataclass(frozen=True)
class WrittenSurface:
    """
    A binary glTF file holding one textured surface, read back.
    """

    path: Path
    """
    The file.
    """

    def mesh(self) -> trimesh.Trimesh:
        """
        :return: The file's only mesh.
        """
        [mesh] = trimesh.load_scene(str(self.path)).geometry.values()
        return mesh

    def image(self) -> numpy.ndarray:
        """
        :return: The pixels of the mesh's texture.
        """
        return numpy.asarray(
            self.mesh().visual.material.baseColorTexture.convert("RGB")
        )

    @staticmethod
    def pixels_of(image_file: Path) -> numpy.ndarray:
        """
        :param image_file: An image file.
        :return: Its pixels.
        """
        return numpy.asarray(PIL.Image.open(image_file).convert("RGB"))


# %% robot


@dataclass
class RobotWorld:
    """
    A PR2 parsed from its URDF, standing in the world's root.
    """

    world: World
    """
    The world.
    """

    robot: PR2
    """
    The robot's annotation.
    """

    @classmethod
    def build(cls) -> RobotWorld:
        """
        :return: A new world holding a PR2.
        """
        world = URDFParser.from_file(file_path=PR2.get_ros_file_path()).create_world()
        return cls(world=world, robot=PR2.from_world(world))


# %% robots of several plans


@dataclass
class TwoRobots:
    """
    Two bodiless robots standing in a world of their own, each to perform a plan of its
    own; the world a publisher shows does not hold them.
    """

    world: World
    """
    The robots' world.
    """

    robots: List[MinimalRobot]
    """
    The robots, named after :data:`ROBOT_NAMES`.
    """

    @classmethod
    def build(cls) -> TwoRobots:
        """
        :return: A new world holding the two robots.
        """
        world = World()
        root = Body(name=PrefixedName("map"))
        bases = [Body(name=PrefixedName("base", name)) for name in ROBOT_NAMES]
        with world.modify_world():
            world.add_kinematic_structure_entity(root)
            for base in bases:
                world.add_connection(FixedConnection(parent=root, child=base))
        robots = [
            MinimalRobot(name=PrefixedName(name), root=base)
            for name, base in zip(ROBOT_NAMES, bases)
        ]
        with world.modify_world():
            for robot in robots:
                world.add_semantic_annotation(robot)
        return cls(world=world, robots=robots)

    def context(self, robot: MinimalRobot) -> TaskContext:
        """
        :param robot: One of the robots.
        :return: The context of a plan the robot performs.
        """
        return TaskContext(world=self.world, robot=robot)
