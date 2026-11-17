"""
The robot profile a deployment configures the plan builder with.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cramera_protocol.browser import BROWSER_ENUMERATIONS
from cramera_protocol.exceptions import UnsupportedProtocolVersion
from cramera_protocol.geometry import AxisAlignedBox, BoxKey, FloorRectangle
from cramera_protocol.plan_builder import RunMode
from cramera_protocol.robot_profile import (
    PROFILE_FORMAT_VERSION,
    ArmWorkspace,
    ArmWorkspaceKey,
    ExecutorKind,
    PerceptionSettings,
    PerceptionSettingsKey,
    RobotProfile,
    RobotProfileKey,
    RuntimeCheck,
    SafetyLimits,
    SafetyLimitsKey,
    Zone,
    ZoneKey,
)
from cramera_protocol.version import ProtocolVersion

# %% reading


@pytest.fixture
def profile_file() -> Path:
    """
    :return: The test dataset's profile of Tracy.
    """
    return Path(__file__).parent / "dataset" / "robot_profiles" / "tracy.json"


def test_a_profile_file_names_the_robot_its_label_and_environment(profile_file):
    profile = RobotProfile.from_file(profile_file)
    assert profile == RobotProfile(
        robot="tracy", label="Tracy", environment="tracy_lab"
    )


def test_a_profile_without_environment_stands_on_the_empty_floor():
    profile = RobotProfile.from_json(
        {RobotProfileKey.ROBOT: "tracy", RobotProfileKey.LABEL: "Tracy"}
    )
    assert profile.environment is None
    assert profile.format_version == PROFILE_FORMAT_VERSION


def test_a_profile_round_trips(profile_file):
    profile = RobotProfile.from_file(profile_file)
    assert RobotProfile.from_json(profile.to_json()) == profile


def test_a_profile_of_a_newer_major_version_is_refused():
    newer = ProtocolVersion(major=PROFILE_FORMAT_VERSION.major + 1, minor=0)
    with pytest.raises(UnsupportedProtocolVersion):
        RobotProfile.from_json(
            {
                RobotProfileKey.FORMAT_VERSION: newer.to_json(),
                RobotProfileKey.ROBOT: "tracy",
                RobotProfileKey.LABEL: "Tracy",
            }
        )


def test_the_browser_reads_the_profile_keys():
    assert RobotProfileKey in BROWSER_ENUMERATIONS


# %% executors


def test_a_profile_naming_no_executors_runs_every_mode_on_its_default():
    profile = RobotProfile(robot="tracy", label="Tracy")
    assert (
        profile.executor_for(RunMode.SIMULATE, ExecutorKind.PHYSICS)
        is ExecutorKind.PHYSICS
    )


def test_a_profile_runs_the_modes_it_names_on_their_executors_and_no_others():
    profile = RobotProfile.from_json(
        {
            RobotProfileKey.ROBOT: "tracy",
            RobotProfileKey.LABEL: "Tracy",
            RobotProfileKey.EXECUTORS: {RunMode.PERFORM: ExecutorKind.PHYSICS},
        }
    )
    assert (
        profile.executor_for(RunMode.PERFORM, ExecutorKind.KINEMATIC)
        is ExecutorKind.PHYSICS
    )
    assert profile.executor_for(RunMode.SIMULATE, ExecutorKind.PHYSICS) is None
    assert RobotProfile.from_json(profile.to_json()) == profile


def test_the_browser_reads_the_executor_kinds():
    assert ExecutorKind in BROWSER_ENUMERATIONS


def test_a_profile_names_the_simulated_scene_its_camera_sees():
    profile = RobotProfile.from_json(
        {
            RobotProfileKey.ROBOT: "tracy",
            RobotProfileKey.LABEL: "Tracy",
            RobotProfileKey.SIMULATED_SCENE: "scenes/cubes.json",
        }
    )
    assert profile.simulated_scene == "scenes/cubes.json"
    assert profile.to_json()[RobotProfileKey.SIMULATED_SCENE] == "scenes/cubes.json"


# %% zones


def test_a_profile_names_its_zones_on_the_robots_table():
    profile = RobotProfile.from_json(
        {
            RobotProfileKey.ROBOT: "tracy",
            RobotProfileKey.LABEL: "Tracy",
            RobotProfileKey.ZONES: [
                {
                    ZoneKey.IDENTIFIER: "left_tray",
                    ZoneKey.LABEL: "Left tray",
                    ZoneKey.AREA: {"minX": 0.6, "maxX": 0.8, "minY": 0.3, "maxY": 0.5},
                }
            ],
        }
    )
    assert profile.zone_named("left_tray") == Zone(
        identifier="left_tray",
        label="Left tray",
        area=FloorRectangle(min_x=0.6, max_x=0.8, min_y=0.3, max_y=0.5),
    )
    assert profile.zone_named("right_tray") is None
    assert RobotProfile.from_json(profile.to_json()) == profile
    assert ZoneKey in BROWSER_ENUMERATIONS


# %% safety limits


def test_a_profile_holds_its_arms_to_workspaces_and_velocity_limits():
    profile = RobotProfile.from_json(
        {
            RobotProfileKey.ROBOT: "tracy",
            RobotProfileKey.LABEL: "Tracy",
            RobotProfileKey.SAFETY: {
                SafetyLimitsKey.WORKSPACES: [
                    {
                        ArmWorkspaceKey.ARM: "left",
                        ArmWorkspaceKey.LABEL: "Left arm workspace",
                        ArmWorkspaceKey.BOX: {
                            BoxKey.MIN_X: 0.3,
                            BoxKey.MAX_X: 1.2,
                            BoxKey.MIN_Y: -0.3,
                            BoxKey.MAX_Y: 0.7,
                            BoxKey.MIN_Z: 0.875,
                            BoxKey.MAX_Z: 1.6,
                        },
                    }
                ],
                SafetyLimitsKey.MAXIMUM_REAL_ROBOT_TOOL_VELOCITY: 0.1,
            },
        }
    )
    assert profile.safety == SafetyLimits(
        workspaces=[
            ArmWorkspace(
                arm="left",
                label="Left arm workspace",
                box=AxisAlignedBox(
                    min_x=0.3, max_x=1.2, min_y=-0.3, max_y=0.7, min_z=0.875, max_z=1.6
                ),
            )
        ],
        maximum_real_robot_tool_velocity=0.1,
    )
    assert RobotProfile.from_json(profile.to_json()) == profile


def test_a_profile_without_safety_limits_holds_its_runs_to_none(profile_file):
    assert RobotProfile.from_file(profile_file).safety is None


# %% perception


def test_a_profile_says_how_long_its_robot_s_perception_may_take_to_answer():
    profile = RobotProfile.from_json(
        {
            RobotProfileKey.ROBOT: "tracy",
            RobotProfileKey.LABEL: "Tracy",
            RobotProfileKey.PERCEPTION: {PerceptionSettingsKey.ANSWER_TIMEOUT: 5.0},
        }
    )

    assert profile.perception == PerceptionSettings(answer_timeout=5.0)
    assert RobotProfile.from_json(profile.to_json()) == profile


def test_a_profile_without_perception_settings_waits_the_default_time(profile_file):
    assert RobotProfile.from_file(profile_file).perception == PerceptionSettings()


def test_a_profile_says_how_a_run_treats_what_it_perceives_before_a_step():
    profile = RobotProfile.from_json(
        {
            RobotProfileKey.ROBOT: "tracy",
            RobotProfileKey.LABEL: "Tracy",
            RobotProfileKey.PERCEPTION: {
                PerceptionSettingsKey.RUNTIME_CHECK: RuntimeCheck.LENIENT.value
            },
        }
    )

    assert profile.perception.runtime_check is RuntimeCheck.LENIENT
    assert RobotProfile.from_json(profile.to_json()) == profile


def test_a_profile_without_a_runtime_check_holds_its_runs_to_the_strict_one(
    profile_file,
):
    assert (
        RobotProfile.from_file(profile_file).perception.runtime_check
        is RuntimeCheck.STRICT
    )


def test_the_browser_reads_how_strictly_a_run_checks_what_it_perceives():
    assert {PerceptionSettingsKey, RuntimeCheck} <= set(BROWSER_ENUMERATIONS)
