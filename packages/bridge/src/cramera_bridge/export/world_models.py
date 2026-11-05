"""
The models and free objects of a world, written into a scene bundle: one URDF per robot,
one for the environment, and one description per free object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import numpy
from typing_extensions import Dict, List, Optional, Sequence

from cramera_bridge.exceptions import MissingMeshFile
from cramera_bridge.export.assets import BundledAssets, MaterialLibrary, MeshFormat
from cramera_bridge.export.textures import SurfaceFile, TexturedSurface
from cramera_bridge.export.urdf import ModelReport, UrdfDocument
from cramera_bridge.export.world_annotations import WorldAnnotations
from cramera_bridge.geometry import (
    NumericPose,
    PublishedColor,
    PublishedScale,
    SIZE_PRECISION,
)
from cramera_bridge.names import class_path
from cramera_bridge.robots import RobotParts, RobotRoster
from cramera_protocol.bundle.files import BundleFile
from cramera_protocol.bundle.scene import (
    ModelDescription,
    ObjectDescription,
    RobotDescription,
    SceneAnnotation,
    SceneClasses,
)
from cramera_protocol.geometry import Pose
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.geometry import Box, Mesh, Scale, Shape
from semantic_digital_twin.world_description.shape_collection import ShapeCollection
from semantic_digital_twin.world_description.world_entity import Body

ENVIRONMENT_MODEL_NAME = "environment"
"""
The name of the model holding every body outside the robots and the free objects.
"""

OBJECT_MESH_DIRECTORY = "objects"
"""
The folder below ``meshes`` the free objects' meshes go to.
"""

# %% models


@dataclass
class WorldModelsBundle:
    """
    The models of a world as a scene bundle describes them.
    """

    models: List[ModelDescription] = field(default_factory=list)
    """
    The environment model first, when it has bodies, then one model per robot.
    """

    robot: Optional[RobotDescription] = None
    """
    The selected robot, or None without one.
    """

    missing_assets: List[str] = field(default_factory=list)
    """
    The mesh files no model could copy, sorted.
    """

    classes: SceneClasses = field(default_factory=SceneClasses)
    """
    The classes of the world, its robots, the written bodies and connections, the free
    objects and the selected robot's parts.
    """

    annotations: List[SceneAnnotation] = field(default_factory=list)
    """
    The world's semantic annotations other than its robots and their parts, each naming
    the written bodies and free objects it gives a meaning to.
    """


@dataclass(frozen=True)
class WorldModels:
    """
    Writes the robot and environment models of a world as URDF files into a bundle.

    Link and joint names are the full names of the world's bodies and connections, which
    are also the names the trajectory uses, so every model's prefix is empty.
    """

    world: World
    """
    The world whose models are written.
    """

    output_directory: Path
    """
    The bundle directory.
    """

    mesh_directory: str
    """
    The folder below ``meshes`` the models' meshes go to.
    """

    def write(
        self, objects: Sequence[Body], selected_robot: Optional[AbstractRobot]
    ) -> WorldModelsBundle:
        """
        Write one URDF per robot, holding the bodies of its annotation, and one for the
        environment, holding all remaining bodies; the free objects belong to neither.

        :param objects: The free objects, written separately by :class:`ObjectFiles`.
        :param selected_robot: The robot the bundle's ``robot`` describes, or None.
        :return: The models, the selected robot, and the classes and annotations of what
            they show.
        """
        self.output_directory.mkdir(parents=True, exist_ok=True)
        roster = RobotRoster(self.world)
        excluded = {body.id for body in objects}
        robot_bodies: Dict[str, List[Body]] = {}
        for robot in roster.robots():
            members = {body.id for body in robot.bodies}
            robot_bodies[roster.identifier(robot)] = [
                body
                for body in self.world.bodies_topologically_sorted
                if body.id in members and body.id not in excluded
            ]
        claimed = excluded | {
            body.id for bodies in robot_bodies.values() for body in bodies
        }
        bundle = WorldModelsBundle()
        bundle.classes.world = class_path(type(self.world))
        body_names: Dict[UUID, str] = {body.id: str(body.name) for body in objects}
        environment = [
            body
            for body in self.world.bodies_topologically_sorted
            if body.id not in claimed
        ]
        if environment:
            report = self.document().write(environment, ENVIRONMENT_MODEL_NAME)
            bundle.models.append(self.description(report, robot=False))
            bundle.missing_assets.extend(report.missing)
            self.add_classes(bundle.classes, report)
            body_names.update(report.body_links)
        for robot in roster.robots():
            bodies = robot_bodies[roster.identifier(robot)]
            if not bodies:
                continue
            report = self.document().write(
                bodies, roster.model_name(robot), identity_root=robot.root
            )
            model = self.description(report, robot=True)
            model.identifier = roster.identifier(robot)
            model.pose = NumericPose.of_entity(self.world, robot.root).published()
            bundle.models.append(model)
            bundle.missing_assets.extend(report.missing)
            self.add_classes(bundle.classes, report)
            body_names.update(report.body_links)
            bundle.classes.models[model.name] = class_path(type(robot))
        bundle.missing_assets = sorted(set(bundle.missing_assets))
        for body in objects:
            bundle.classes.bodies[str(body.name)] = class_path(type(body))
        bundle.annotations = WorldAnnotations(self.world).descriptions(body_names)
        if selected_robot is not None:
            bundle.robot = self.robot_description(selected_robot)
            bundle.classes.annotations = {
                str(part.name): class_path(type(part))
                for part in RobotParts(self.world, selected_robot).annotations()
            }
        return bundle

    @staticmethod
    def add_classes(classes: SceneClasses, report: ModelReport) -> None:
        """
        :param classes: The classes of a bundle's entities.
        :param report: What a document wrote, whose bodies and connections are added.
        """
        classes.bodies.update(report.body_classes)
        classes.connections.update(report.connection_classes)

    def document(self) -> UrdfDocument:
        """
        :return: A new document copying meshes into this bundle.
        """
        return UrdfDocument(
            world=self.world,
            assets=BundledAssets(bundle_root=self.output_directory),
            mesh_directory=self.mesh_directory,
        )

    @staticmethod
    def description(report: ModelReport, robot: bool) -> ModelDescription:
        """
        :param report: What a document wrote.
        :param robot: Whether the model is a robot.
        :return: The model as ``scene.json`` describes it.
        """
        return ModelDescription(
            name=report.name,
            urdf=report.urdf,
            prefix="",
            robot=robot,
            links=len(report.links),
            movable_joints=report.movable_joints,
            posed_joints=report.posed_joints,
        )

    def robot_description(self, robot: AbstractRobot) -> RobotDescription:
        """
        :param robot: A robot of the world.
        :return: The robot with its arms and end effectors.
        """
        parts = RobotParts(self.world, robot).descriptions()
        return RobotDescription(
            name=RobotRoster(self.world).model_name(robot),
            prefix="",
            base_body=str(robot.root.name),
            parts={part.name: list(part.links) for part in parts},
            part_annotations=parts,
            identifier=RobotRoster.identifier(robot),
            label=RobotRoster.label(robot),
        )


# %% free objects


@dataclass(frozen=True)
class ObjectFiles:
    """
    Describes free objects for ``scene.json``, writing their mesh files into the bundle.
    """

    output_directory: Path
    """
    The bundle directory.
    """

    def describe(self, body: Body, key: str, spawn: Pose) -> ObjectDescription:
        """
        :param body: A free object.
        :param key: The object's key in the trajectory's object poses.
        :param spawn: The object's pose before the run.
        :return: The object: a combined binary glTF mesh when a shape has a
            :class:`TexturedSurface`, a box for a single untransformed box, a copied
            mesh for a single untransformed mesh at unit scale, a combined OBJ mesh
            otherwise, and no geometry or colour for a body without shapes.
        :raises MissingMeshFile: When a mesh shape names a file that does not exist.
        """
        description = ObjectDescription(identifier=str(body.name), key=key, spawn=spawn)
        shapes = self.shapes_of(body)
        if not shapes:
            return description
        for shape in shapes:
            if isinstance(shape, Mesh) and not Path(shape.filename).is_file():
                raise MissingMeshFile(Path(shape.filename))
        description.color = PublishedColor(shapes[0].color).hexadecimal()
        description.height = self.height_of(shapes)
        surfaces = [TexturedSurface.of(shape) for shape in shapes]
        if any(surface is not None for surface in surfaces):
            self.write_textured_surfaces(body, shapes, surfaces, key, description)
            return description
        if len(shapes) == 1 and self.is_untransformed(shapes[0]):
            if isinstance(shapes[0], Box):
                description.box = PublishedScale(shapes[0].scale).published()
                return description
            if isinstance(shapes[0], Mesh) and shapes[0].scale == Scale():
                self.copy_mesh(shapes[0], key, description)
                return description
        self.combine_meshes(body, shapes, key, description)
        return description

    @staticmethod
    def shapes_of(body: Body) -> List[Shape]:
        """
        :param body: A body.
        :return: Its visual shapes, or its collision shapes when it has no visual ones.
        """
        if body.visual.shapes:
            return list(body.visual.shapes)
        return list(body.collision.shapes)

    @staticmethod
    def is_untransformed(shape: Shape) -> bool:
        """
        :param shape: A shape.
        :return: Whether it sits at its body's origin.
        """
        return bool(numpy.allclose(shape.origin.to_np(), numpy.eye(4)))

    @staticmethod
    def height_of(shapes: List[Shape]) -> float:
        """
        :param shapes: A body's shapes.
        :return: Their height along the body's z axis.
        """
        bounds = ShapeCollection(shapes=shapes).combined_mesh.bounds
        return round(float(bounds[1][2] - bounds[0][2]), SIZE_PRECISION)

    def object_directory(self, key: str) -> Path:
        """
        :param key: An object's key.
        :return: The object's own mesh folder, so equally named files of different
            objects never overwrite each other.
        """
        return self.output_directory / BundleFile.OBJECT_MESHES / key.replace("/", "_")

    def copy_mesh(self, mesh: Mesh, key: str, description: ObjectDescription) -> None:
        """
        Copy a mesh file with the files it references.

        :param mesh: The mesh shape.
        :param key: The object's key.
        :param description: The description receiving the mesh and material library.
        """
        source = Path(mesh.filename)
        assets = BundledAssets(bundle_root=self.output_directory)
        destination = self.object_directory(key) / source.name
        assets.copy_mesh(source, destination)
        description.mesh = assets.relative(destination)
        library = MaterialLibrary.companion_of(destination)
        if library is not None:
            description.material_library = assets.relative(library)

    def combine_meshes(
        self,
        body: Body,
        shapes: List[Shape],
        key: str,
        description: ObjectDescription,
    ) -> None:
        """
        Export the shapes, each at its pose and scale in the body, as one OBJ mesh.

        :param body: The body.
        :param shapes: Its shapes.
        :param key: The object's key.
        :param description: The description receiving the mesh.
        """
        destination = self.object_directory(key) / f"{body.name.name}{MeshFormat.OBJ}"
        assets = BundledAssets(bundle_root=self.output_directory)
        assets.require_inside(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        ShapeCollection(shapes=shapes).combined_mesh.export(str(destination))
        description.mesh = assets.relative(destination)

    def write_textured_surfaces(
        self,
        body: Body,
        shapes: List[Shape],
        surfaces: List[Optional[TexturedSurface]],
        key: str,
        description: ObjectDescription,
    ) -> None:
        """
        Write the shapes, each at its pose in the body, as one binary glTF mesh that
        keeps their textures.

        :param body: The body.
        :param shapes: Its shapes.
        :param surfaces: The textured surface of each shape, or None for a shape drawn
            in its colour.
        :param key: The object's key.
        :param description: The description receiving the mesh.
        """
        placed = []
        for shape, surface in zip(shapes, surfaces):
            mesh = shape.mesh.copy() if surface is None else surface.mesh.copy()
            mesh.apply_transform(shape.origin.to_np())
            placed.append(mesh)
        assets = BundledAssets(bundle_root=self.output_directory)
        written = SurfaceFile(assets).write(
            placed, self.object_directory(key), body.name.name
        )
        description.mesh = assets.relative(written)
