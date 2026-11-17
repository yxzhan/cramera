"""
The robot profile a deployment configures: its templates and how its skills keep saved
plans current.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cramera_plan_builder.exceptions import RunModeNotOffered
from cramera_plan_builder.profiles import ConfiguredProfile
from cramera_protocol.geometry import Pose
from cramera_protocol.validation import ValidationOrder
from cramera_protocol.plan_builder import (
    DemoDocument,
    PlanDocument,
    PlannedRobot,
    PlanStep,
    RunMode,
)
from cramera_protocol.robot_profile import (
    CameraMount,
    ExecutorKind,
    RobotProfile,
    RobotConnection,
    RobotProfileKey,
    RobotProfileVariable,
)

# %% reading


@pytest.fixture
def profile_file() -> Path:
    """
    :return: The test dataset's profile of Tracy, with one template.
    """
    return Path(__file__).parent / "dataset" / "profile" / "tracy.json"


@pytest.fixture
def configured(profile_file) -> ConfiguredProfile:
    """
    :return: The test dataset's profile with its templates.
    """
    return ConfiguredProfile.of_file(profile_file)


def test_the_templates_are_read_relative_to_the_profile_file(configured):
    [template] = configured.templates
    assert template.identifier == "park"
    assert template.plan.robots[0].steps[0].skill == "park"


def test_the_environment_names_the_profile_file(profile_file, configured):
    named = ConfiguredProfile.of_environment(
        {RobotProfileVariable.PROFILE: str(profile_file)}
    )
    assert named == configured


def test_without_a_named_file_there_is_no_profile():
    assert ConfiguredProfile.of_environment({}) == ConfiguredProfile()


def test_the_simulated_scene_is_read_relative_to_the_profile_file(configured):
    assert [item.identifier for item in configured.simulated_objects] == [
        "left_cup",
        "right_cup",
    ]


def test_the_profile_names_how_its_robots_camera_is_mounted(configured):
    assert configured.camera_mount() is CameraMount.FIXED


def test_without_a_profile_the_camera_mount_is_unknown():
    assert ConfiguredProfile().camera_mount() is None


def test_without_a_simulated_scene_the_robot_perceives_its_own(tmp_path, profile_file):
    without = tmp_path / "tracy.json"
    document = json.loads(profile_file.read_text("utf-8"))
    del document[RobotProfileKey.SIMULATED_SCENE]
    without.write_text(json.dumps(document), "utf-8")
    (tmp_path / "templates").symlink_to(profile_file.parent / "templates")
    assert ConfiguredProfile.of_file(without).simulated_objects is None


# %% resolving skills


def plan_of(*steps: PlanStep) -> PlanDocument:
    """
    :param steps: The steps of the plan's one robot.
    :return: A plan of Tracy taking the steps.
    """
    return PlanDocument(
        name="plan",
        robots=[
            PlannedRobot(
                identifier="robot-1",
                model="tracy",
                label="Tracy",
                pose=Pose(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
                steps=list(steps),
            )
        ],
    )


def test_a_step_of_a_skill_names_the_action_the_profile_maps_it_to(configured):
    step = PlanStep(identifier="step-1", skill="pick", action="grasp_older_name")
    resolved = configured.resolved(plan_of(step))
    pick = configured.profile.skill_named("pick")
    assert resolved.robots[0].steps[0].action == pick.action


def test_a_step_of_a_native_action_or_an_unknown_skill_keeps_its_action(configured):
    native = PlanStep(identifier="step-1", action="navigate")
    unknown = PlanStep(identifier="step-2", skill="dance", action="navigate")
    resolved = configured.resolved(plan_of(native, unknown))
    assert resolved.robots[0].steps == [native, unknown]


def test_a_resolved_plan_carries_the_profiles_platform_version(configured):
    resolved = configured.resolved(plan_of())
    assert resolved.platform_version == configured.profile.platform_version


def test_without_a_profile_a_plan_stays_as_it_is():
    plan = plan_of(PlanStep(identifier="step-1", skill="pick", action="x"))
    assert ConfiguredProfile().resolved(plan) == plan


# %% executors


@pytest.mark.parametrize(
    "mode",
    [mode for mode in RunMode if mode.default_executor or not mode.performs_plan],
)
def test_without_a_profile_each_mode_runs_on_its_default_executor(mode):
    assert ConfiguredProfile().executor_for(mode) is mode.default_executor


def test_a_run_on_the_robot_needs_a_profile_naming_its_executor():
    with pytest.raises(RunModeNotOffered):
        ConfiguredProfile().executor_for(RunMode.ROBOT)
    with pytest.raises(RunModeNotOffered):
        ConfiguredProfile(
            RobotProfile(
                robot="tracy",
                label="Tracy",
                executors={RunMode.SIMULATE: ExecutorKind.PERCEIVED_PHYSICS},
            )
        ).executor_for(RunMode.ROBOT)


def test_a_profile_chooses_the_executor_of_each_mode_it_offers():
    configured = ConfiguredProfile(
        RobotProfile(
            robot="tracy",
            label="Tracy",
            executors={RunMode.PERFORM: ExecutorKind.PHYSICS},
        )
    )
    assert configured.executor_for(RunMode.STAGE) is None
    assert configured.executor_for(RunMode.PERFORM) is ExecutorKind.PHYSICS
    with pytest.raises(RunModeNotOffered):
        configured.executor_for(RunMode.SIMULATE)


# %% the real robot


@pytest.fixture
def profile_with_a_real_run(tmp_path, profile_file) -> Path:
    """
    :return: The test dataset's profile, its robot run executed on the real robot.
    """
    document = json.loads(profile_file.read_text("utf-8"))
    document[RobotProfileKey.EXECUTORS] = {
        RunMode.PERFORM: ExecutorKind.KINEMATIC,
        RunMode.ROBOT: ExecutorKind.REAL,
    }
    real = tmp_path / "tracy.json"
    real.write_text(json.dumps(document), "utf-8")
    for referenced in ("templates", "scenes"):
        (tmp_path / referenced).symlink_to(profile_file.parent / referenced)
    return real


def test_a_deployment_attached_to_the_real_robot_runs_on_it_and_perceives_with_it(
    profile_with_a_real_run,
):
    configured = ConfiguredProfile.of_environment(
        {
            RobotProfileVariable.PROFILE: str(profile_with_a_real_run),
            RobotProfileVariable.CONNECTION: RobotConnection.REAL,
        }
    )
    assert configured.connection is RobotConnection.REAL
    assert configured.executor_for(RunMode.ROBOT) is ExecutorKind.REAL
    assert configured.simulated_objects is None


def test_a_validation_starts_where_the_attached_real_robot_stands(
    profile_with_a_real_run,
):
    demo = DemoDocument(plan=plan_of())
    attached = ConfiguredProfile.of_file(profile_with_a_real_run, RobotConnection.REAL)
    simulated = ConfiguredProfile.of_file(profile_with_a_real_run)
    assert attached.validation_order(demo).reads_robot_posture is True
    assert simulated.validation_order(demo) == ValidationOrder(
        demo=demo, simulated_objects=simulated.simulated_objects
    )


def test_a_simulated_deployment_offers_no_run_on_the_real_robot(
    profile_with_a_real_run,
):
    configured = ConfiguredProfile.of_environment(
        {RobotProfileVariable.PROFILE: str(profile_with_a_real_run)}
    )
    assert configured.connection is RobotConnection.SIMULATED
    assert configured.profile.executors == {RunMode.PERFORM: ExecutorKind.KINEMATIC}
    with pytest.raises(RunModeNotOffered):
        configured.executor_for(RunMode.ROBOT)
    assert configured.simulated_objects is not None
