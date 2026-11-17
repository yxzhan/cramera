"""
A plan's objects taken from an object library: their URDFs merged into the plan's world
as free, graspable objects, and offered in the workspace's catalog.
"""

from __future__ import annotations

import pytest

from cramera_bridge.geometry import NumericPose
from cramera_bridge.plans.objects import ObjectLibraries, ObjectLibrary
from cramera_bridge.plans.workspace import PlanWorkspace
from cramera_bridge.plans.world import (
    PlannedGraspable,
    PlannedGraspableFromAbove,
    PlannedObjects,
)
from cramera_protocol.geometry import Pose
from cramera_protocol.plan_builder import PlannedObject
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.exceptions import NoGraspGeometry
from semantic_digital_twin.specifications.connections import (
    Connection6DoFSpecification,
)
from semantic_digital_twin.specifications.kinematic_structure_entities import (
    BodySpecification,
)
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.geometry import Scale
from semantic_digital_twin.world_description.connections import Connection6DoF
from semantic_digital_twin.world_description.world_entity import Body

from .test_objects import BOX, CUP, LIBRARY

LIBRARIES = ObjectLibraries([ObjectLibrary(LIBRARY)])
"""
The stand-in library alone.
"""


@pytest.fixture
def world() -> World:
    """
    :return: A world of nothing but its root.
    """
    world = World()
    with world.modify_world():
        world.add_kinematic_structure_entity(Body(name=PrefixedName("root")))
    return world


def test_a_library_model_joins_the_world_free_and_graspable_where_the_plan_puts_it(
    world, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    planned = PlannedObject(
        identifier="cup", model=CUP, pose=Pose.from_json([1.0, 0.5, 0.8, 0, 0, 0, 1])
    )
    (body,) = PlannedObjects([planned], libraries=LIBRARIES).spawn(world).values()
    assert isinstance(body.parent_connection, Connection6DoF)
    assert NumericPose.of_entity(world, body).position == pytest.approx((1.0, 0.5, 0.8))
    assert PlannedGraspable.of(body).root is body
    lid = world.get_kinematic_structure_entity_by_name(PrefixedName("cup_lid", "cup_"))
    assert lid.parent_connection.parent is body


def test_a_library_model_is_named_by_the_object_its_links_below_it(world):
    planned = PlannedObject(
        identifier="cup", model=CUP, pose=Pose.from_json([0] * 6 + [1])
    )
    (body,) = PlannedObjects([planned], libraries=LIBRARIES).spawn(world).values()
    assert body.name == PrefixedName(planned.identifier)
    lid = world.get_kinematic_structure_entity_by_name(PrefixedName("cup_lid", "cup_"))
    assert lid.parent_connection.parent is body


def test_a_model_its_collection_takes_from_above_is_annotated_so(world):
    planned = [
        PlannedObject(identifier=name, model=model, pose=Pose.from_json([0] * 6 + [1]))
        for name, model in (("cup", CUP), ("box", BOX))
    ]
    bodies = PlannedObjects(planned, libraries=LIBRARIES).spawn(world)
    assert isinstance(PlannedGraspable.of(bodies["cup"]), PlannedGraspableFromAbove)
    assert type(PlannedGraspable.of(bodies["box"])) is PlannedGraspable


def test_a_block_taken_from_above_is_held_at_its_top(world):
    block = BodySpecification.box(
        "block",
        Scale(0.05, 0.05, 0.05),
        origin=HomogeneousTransformationMatrix.from_xyz_rpy(z=0.025),
        connection_specification=Connection6DoFSpecification(),
    ).spawn(world, parent_T_self=HomogeneousTransformationMatrix.from_xyz_rpy(1, 0, 0))
    with world.modify_world():
        graspable = PlannedGraspableFromAbove(root=block)
        world.add_semantic_annotation(graspable)
    [grasp] = graspable.grasp_candidates()
    object_T_grasp = grasp.grasp_pose.to_np()
    top = block.collision.max_point.to_np()[2]
    assert object_T_grasp[:3, 3] == pytest.approx([0, 0, top])
    assert object_T_grasp[:3, 0] == pytest.approx([0, 0, -1])


def test_an_object_without_collision_cannot_be_taken_from_above(world):
    with world.modify_world():
        graspable = PlannedGraspableFromAbove(root=world.root)
        world.add_semantic_annotation(graspable)
    with pytest.raises(NoGraspGeometry):
        graspable.grasp_candidates()


def test_the_catalog_offers_the_library_models_after_the_meshes(tmp_path):
    (tmp_path / "milk.stl").write_text("solid milk\nendsolid milk\n", encoding="utf-8")
    workspace = PlanWorkspace(directory=tmp_path, libraries=LIBRARIES)
    assert [model.identifier for model in workspace.objects()][:2] == ["milk.stl", CUP]
    assert workspace.object_roots() == {LIBRARY.name: LIBRARY}
