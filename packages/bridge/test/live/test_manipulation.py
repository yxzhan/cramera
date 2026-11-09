"""
Objects and joints a person moves through the viewer, and the world's surfaces.
"""

from __future__ import annotations

import pytest

from cramera_bridge.exceptions import ObjectNotMovable, UnknownJoint, UnknownObject
from cramera_bridge.names import ANNOTATION_NAMES
from cramera_bridge.geometry import NumericPose
from cramera_bridge.live.manipulation import WorldSurfaces
from cramera_bridge.live.publisher import LivePublisher
from cramera_protocol.geometry import Vector3
from cramera_protocol.live_messages import (
    JointRequest,
    MoveRequest,
    Quaternion,
    TransformWriter,
)
from semantic_digital_twin.semantic_annotations.semantic_annotations import Table

from ..dataset.live_worlds import HeightChart

# %% objects


def test_an_idle_world_is_moved_at_once(publisher, kitchen):
    publisher.move(
        MoveRequest(object_key=str(kitchen.milk.id), position=Vector3(2.0, 0.5, 1.0))
    )
    pose = NumericPose.of_entity(kitchen.world, kitchen.milk).published()
    assert (pose.x, pose.y, pose.z) == (2.0, 0.5, 1.0)
    assert publisher.current_state().objects[str(kitchen.milk.id)] == pose


def test_a_move_names_the_viewer_as_writer(publisher, kitchen):
    publisher.move(
        MoveRequest(object_key=str(kitchen.milk.id), position=Vector3(2.0, 0.5, 1.0))
    )
    name = str(kitchen.milk_connection.name)
    entry = next(
        entry for entry in publisher.transform_graph().connections if entry.name == name
    )
    assert entry.writer is TransformWriter.VIEWER


def test_a_move_while_a_plan_runs_waits_for_the_next_tick(publisher, kitchen):
    chart = HeightChart.build(kitchen.world, kitchen.milk)
    publisher.activity.tick()
    publisher.move(
        MoveRequest(
            object_key=str(kitchen.milk.id),
            position=Vector3(3.0, 0.0, 1.0),
            quaternion=Quaternion(0.0, 0.0, 0.0, 1.0),
        )
    )
    assert NumericPose.of_entity(kitchen.world, kitchen.milk).position[0] != 3.0
    assert publisher.captured_objects().objects[str(kitchen.milk.id)].x == 3.0
    chart.executor.tick()
    publisher.observe_tick(chart.chart, False)
    assert NumericPose.of_entity(kitchen.world, kitchen.milk).position[0] == 3.0


def test_unknown_and_fixed_objects_are_not_moved(publisher, kitchen):
    with pytest.raises(UnknownObject):
        publisher.move(MoveRequest(object_key="table", position=Vector3(0, 0, 0)))
    publisher.model.objects.append(kitchen.table)
    with pytest.raises(ObjectNotMovable):
        publisher.move(
            MoveRequest(object_key=str(kitchen.table.id), position=Vector3(0, 0, 0))
        )


# %% joints


def test_a_joint_is_held_within_its_limits(robot_world, locations):
    robot_publisher = LivePublisher(locations=locations, snapshot_interval=0.0)
    robot_publisher.attach(robot_world.world)
    joint = robot_publisher.model.joint_named("pr2/torso_lift_joint")
    previous = joint.position
    robot_publisher.set_joint(JointRequest(joint=str(joint.name), position=99.0))
    assert joint.position == joint.dof.limits.upper.position
    joint.position = previous


def test_an_unknown_joint_is_refused(publisher):
    with pytest.raises(UnknownJoint):
        publisher.set_joint(JointRequest(joint="nothing", position=0.0))


# %% surfaces


def test_annotated_surfaces_are_listed(kitchen):
    with kitchen.world.modify_world():
        kitchen.world.add_semantic_annotation(Table(root=kitchen.table))
    surfaces = WorldSurfaces(kitchen.world).surfaces().surfaces
    assert [surface.type_name for surface in surfaces] == [
        ANNOTATION_NAMES.identifier_of(Table)
    ]
