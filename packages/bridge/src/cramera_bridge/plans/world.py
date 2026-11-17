"""
The world a plan runs in: its environment, an empty floor when it names none, and the
free objects it places, each a mesh or a library model's URDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from types import MappingProxyType

from typing_extensions import Dict, List, Type

from cramera_bridge.scenes.bundle_world import ColorText, PoseText
from cramera_bridge.scenes.environment import SceneEnvironment
from cramera_bridge.plans.object_types import ObjectType
from cramera_bridge.plans.objects import ObjectGrasp, ObjectLibraries
from cramera_bridge.plans.workspace import OBJECT_DIRECTORY
from cramera_protocol.plan_builder import PlannedObject
from semantic_digital_twin.adapters.package_resolver import (
    CompositePathResolver,
    PackageUriResolver,
    SearchPathFileResolver,
)
from krrood_spatial_math.spatial_types import Point3, Pose, RotationMatrix, Vector3
from semantic_digital_twin.adapters.urdf import URDFParser
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.exceptions import NoGraspGeometry
from semantic_digital_twin.grasping.grasp_candidates import GraspCandidate
from semantic_digital_twin.semantic_annotations.mixins import HasGraspCandidates
from semantic_digital_twin.specifications.connections import (
    Connection6DoFSpecification,
)
from semantic_digital_twin.specifications.kinematic_structure_entities import (
    BodySpecification,
)
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import Connection6DoF
from semantic_digital_twin.world_description.world_entity import Body

URDF_SUFFIX = ".urdf"
"""
The suffix of a library model's description.
"""

PREFIX_SEPARATOR = "_"
"""
Joins an object's identity to the names of the links its URDF brings.
"""

# %% environment


@dataclass
class EmptyFloor(SceneEnvironment):
    """
    The environment of a plan that names none: nothing but the world's root.
    """

    def populate(self, world: World) -> None:
        """
        Add nothing.

        :param world: The world.
        """


# %% objects


@dataclass(eq=False)
class PlannedGraspable(HasGraspCandidates):
    """
    A free object of a plan, which coraplex's actions take hold of and put down: grasped
    around its own origin, as nothing more is known of its shape.
    """

    @classmethod
    def of(cls, body: Body) -> PlannedGraspable:
        """
        :param body: The body of an object the plan placed.
        :return: The object's annotation.
        """
        (annotation,) = [
            annotation
            for annotation in body._world.get_semantic_annotations_by_type(cls)
            if annotation.root is body
        ]
        return annotation


@dataclass(eq=False)
class PlannedGraspableFromAbove(PlannedGraspable):
    """
    A free object of a plan that is taken from straight above, at the top of its
    collision geometry, as a small block standing on a table is.
    """

    grasp_depth: float = 0.0
    """
    How far, in metres, below the top of its collision geometry the object is held.

    The grasp is the point the fingertips' middles meet at, so held at its top the
    object stays clear of the gripper's knuckles, which push it away while the gripper
    opens, and the lower half of the fingertips still holds it. Tracy's Robotiq tips
    then overlap a 5 cm cube by 2.5 cm and stay 2.5 cm above the table it stands on,
    so the grasp keeps clear of its workspace floor even where its arm sags 1 cm in
    physics.
    """

    def default_grasp_candidates(self) -> List[GraspCandidate]:
        """
        :return: The grasp approaching straight down along the object's own z-axis to
            the middle of its collision geometry's footprint, :attr:`grasp_depth` below
            its top, closing along the object's x-axis.
        :raises NoGraspGeometry: When the object has no collision geometry.
        """
        if not self.root.has_collision():
            raise NoGraspGeometry(self)
        bounding_box = self.root.collision.as_bounding_box_collection_in_frame(
            self.root
        ).bounding_box()
        return [
            GraspCandidate(
                self,
                Pose(
                    position=Point3(
                        (bounding_box.min_x + bounding_box.max_x) / 2,
                        (bounding_box.min_y + bounding_box.max_y) / 2,
                        bounding_box.max_z - self.grasp_depth,
                        reference_frame=self.root,
                    ),
                    orientation=RotationMatrix.from_vectors(
                        x=Vector3.NEGATIVE_Z(), y=Vector3.X()
                    ).quaternion,
                    reference_frame=self.root,
                ),
            )
        ]


GRASPABLE_TYPES: MappingProxyType[ObjectGrasp, Type[PlannedGraspable]] = (
    MappingProxyType(
        {
            ObjectGrasp.ANY_SIDE: PlannedGraspable,
            ObjectGrasp.FROM_ABOVE: PlannedGraspableFromAbove,
        }
    )
)
"""
The annotation a free object of a plan gets, by how its model is taken hold of.
"""

OBJECT_TYPE_ANNOTATIONS: MappingProxyType[ObjectType, Type[PlannedGraspable]] = (
    MappingProxyType(
        {
            ObjectType.OBJECT: PlannedGraspable,
            ObjectType.BLOCK: PlannedGraspableFromAbove,
        }
    )
)
"""
The annotation of each kind of free object.
"""


@dataclass(frozen=True)
class PlannedObjects:
    """
    The free objects of a plan, each a copy of an object mesh or of a library model that
    moves on its own.
    """

    objects: List[PlannedObject]
    """
    The objects.
    """

    directory: Path = field(default=OBJECT_DIRECTORY)
    """
    The directory of the object meshes.
    """

    libraries: ObjectLibraries = field(default_factory=ObjectLibraries.of_environment)
    """
    The object libraries the plan's models may come from.
    """

    def spawn(self, world: World) -> Dict[str, Body]:
        """
        :param world: The world receiving the objects at their start poses, below its
            root, each annotated as graspable as its model is taken hold of.
        :return: Each object's body, a library model's root link, by its identity.
        """
        bodies = {
            planned.identifier: self.spawn_one(world, planned)
            for planned in self.objects
        }
        with world.modify_world():
            for planned in self.objects:
                graspable_type = GRASPABLE_TYPES[self.libraries.grasp_of(planned.model)]
                world.add_semantic_annotation(
                    graspable_type(root=bodies[planned.identifier])
                )
        return bodies

    def spawn_one(self, world: World, planned: PlannedObject) -> Body:
        """
        :param world: The world receiving the object below its root.
        :param planned: The object.
        :return: Its body, free to move and named by the object's identity: the mesh's,
            or the root link of the URDF of a library model, whose other links are named
            below the identity.
        """
        path = self.libraries.path_of(planned.model, self.directory)
        pose = PoseText(planned.pose).native(world.root)
        if path.suffix.lower() != URDF_SUFFIX:
            return BodySpecification.mesh(
                planned.identifier,
                str(path),
                color=(
                    ColorText(planned.color).native()
                    if planned.color is not None
                    else None
                ),
                connection_specification=Connection6DoFSpecification(),
            ).spawn(world, parent_T_self=pose)
        # a library model names its meshes relative to its URDF, not to the process
        resolver = CompositePathResolver(
            resolvers=[
                SearchPathFileResolver(root_directories=[str(path.parent)]),
                PackageUriResolver(),
            ]
        )
        model = URDFParser.from_file(
            str(path),
            prefix=planned.identifier + PREFIX_SEPARATOR,
            path_resolver=resolver,
        ).create_world()
        # the live world names the object as the plan does, as for a mesh
        model.root.name = PrefixedName(planned.identifier)
        root = model.root.id
        with world.modify_world():
            connection = Connection6DoF.create_with_dofs(
                world=world, parent=world.root, child=model.root
            )
            world.merge_world(model, root_connection=connection)
        body = world.get_kinematic_structure_entity_by_id(root)
        body.parent_connection.origin = pose
        return body
