"""
Several robots in one world.
"""

from __future__ import annotations

import pytest

from cramera_bridge.exceptions import InvalidRobotPlacement, RobotPlacementUnavailable
from cramera_bridge.robots import RobotRoster
from cramera_bridge.scenes.bundle_world import BundleEnvironment
from cramera_bridge.scenes.multi_robot import RobotPlacement, RobotScene
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.robots.tracy import Tracy
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix

TORSO = "torso_lift_joint"
"""
A movable joint of the PR2.
"""


def placement(namespace: str, x: float, **joint_positions) -> RobotPlacement:
    return RobotPlacement(
        namespace=namespace,
        label=f"{namespace} robot",
        robot_type=PR2,
        pose=HomogeneousTransformationMatrix.from_xyz_rpy(x, 0.0, 0.0),
        joint_positions=joint_positions,
    )


@pytest.fixture(scope="module")
def scene() -> RobotScene:
    return RobotScene(
        placements=[
            placement("left", 0.0, **{f"left/{TORSO}": 0.2}),
            placement("right", 2.0),
        ],
        selected_namespace="right",
    )


@pytest.fixture(scope="module")
def world(scene):
    return scene.build_world()


# %% building


def test_every_robot_is_identified_by_its_annotation(scene, world):
    identifiers = scene.identifiers(world)
    roster = RobotRoster(world)
    assert set(identifiers.values()) == {
        roster.identifier(robot) for robot in roster.robots()
    }
    assert roster.named(identifiers["right"]) is scene.selected_robot(world)


def test_robots_carry_their_names_and_postures(scene, world):
    left = scene.placements[0].resolve(world)
    assert str(left.name) == "left/left robot"
    [torso] = [
        connection
        for connection in world.connections
        if str(connection.name) == f"left/{TORSO}"
    ]
    assert torso.position == pytest.approx(0.2)


@pytest.mark.skipif(
    not Tracy.is_description_installed(),
    reason="iai_tracy_description is not installed",
)
def test_a_robot_is_listed_where_its_description_is_rooted():
    """
    Tracy's annotation is rooted at its table, 0.88 m above the frame its description
    starts at; the plan builder draws the description at the listed pose, so the pose is
    the description's root, which the placement puts where the plan says.
    """
    tracy = RobotPlacement(
        namespace="tracy_1",
        label="Tracy",
        robot_type=Tracy,
        pose=HomogeneousTransformationMatrix.from_xyz_rpy(0.5, -0.25, 0.0),
    )
    world = RobotScene(placements=[tracy], selected_namespace="tracy_1").build_world()
    pose = RobotRoster(world).instance(tracy.resolve(world)).pose
    assert [pose.x, pose.y, pose.z] == pytest.approx([0.5, -0.25, 0.0])


def test_a_moving_base_is_listed_where_it_stands(scene, world):
    right = scene.placements[1].resolve(world)
    pose = RobotRoster(world).instance(right).pose
    assert [pose.x, pose.y, pose.z] == pytest.approx([2.0, 0.0, 0.0])


def test_an_environment_is_added_to_the_robots(scene, annotated_bundle):
    world = scene.build_world(BundleEnvironment(directory=annotated_bundle))
    assert len(RobotRoster(world).robots()) == 2
    assert any(body.name.name == "World" for body in world.bodies)


# %% invalid scenes


@pytest.mark.parametrize(
    "arguments",
    [
        {"namespace": "1robot"},
        {"label": " "},
        {"joint_positions": {"other/torso_lift_joint": 0.1}},
        {"joint_positions": {f"robot/{TORSO}": float("nan")}},
    ],
)
def test_an_invalid_placement_is_refused(arguments):
    values = {
        "namespace": "robot",
        "label": "robot",
        "robot_type": PR2,
        "pose": HomogeneousTransformationMatrix(),
        **arguments,
    }
    with pytest.raises(InvalidRobotPlacement):
        RobotPlacement(**values)


def test_scenes_need_distinct_robots_and_a_present_selection():
    with pytest.raises(InvalidRobotPlacement):
        RobotScene(placements=[], selected_namespace="robot")
    with pytest.raises(InvalidRobotPlacement):
        RobotScene(
            placements=[placement("robot", 0.0), placement("robot", 1.0)],
            selected_namespace="robot",
        )
    with pytest.raises(InvalidRobotPlacement):
        RobotScene(placements=[placement("robot", 0.0)], selected_namespace="other")


def test_a_placement_missing_from_a_world_is_reported(world):
    with pytest.raises(RobotPlacementUnavailable):
        placement("absent", 0.0).resolve(world)


def test_a_posture_naming_an_unknown_joint_is_refused(world):
    unknown = placement("left", 0.0, **{"left/no_such_joint": 0.1})
    with pytest.raises(InvalidRobotPlacement):
        unknown.restore_joint_positions(world)
