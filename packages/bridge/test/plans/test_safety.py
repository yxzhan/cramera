"""
A plan held to the safety limits of Tracy's profile in MuJoCo: a workspace box the left
arm's tool may not leave below a height its pick reaches down past, which stops the run
at the box's boundary, without another attempt at the pick.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import numpy as np
import pytest

from cramera_bridge.exceptions import ArmOutsideItsWorkspace
from cramera_bridge.execution import PhysicalExecution
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.objects import LIBRARY_VARIABLE
from cramera_bridge.plans.command import PlanDemoCommand
from cramera_bridge.performing import TaskContext
from cramera_bridge.plans.safety import RobotSafety
from cramera_bridge.plans.run_log import (
    AttemptOutcome,
    RunLog,
    SafetyStopped,
    StepAttempt,
)
from cramera_protocol.geometry import AxisAlignedBox
from cramera_protocol.plan_builder import (
    DemoDocument,
    MonitorObservation,
    PlanDocument,
    RunEvent,
    RunEventKind,
    RunMonitor,
    SafetyStop,
    StepState,
)
from cramera_protocol.robot_profile import ArmWorkspace, SafetyLimits
from cramera_protocol.viewer import EnvironmentVariable
from giskardpy.motion_statechart.exceptions import SafetyConstraintViolated
from giskardpy.motion_statechart.monitors.safety_monitors import (
    MaximumJointVelocity,
    MaximumToolVelocity,
)
from semantic_digital_twin.robots.tracy import Tracy
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF

from ..dataset.bundles import DATASET
from ..test_execution import needs_tracy, tracy_with_a_block

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube, each named by its color.
"""

PICK = DATASET / "plans" / "tracy_pick.json"
"""
Tracy parks its arms, then picks up the red cube with its left arm.
"""

LOW_WORKSPACE = ArmWorkspace(
    arm="left",
    label="Left arm workspace",
    box=AxisAlignedBox(
        min_x=0.3, max_x=1.2, min_y=-0.3, max_y=0.7, min_z=1.1, max_z=1.6
    ),
)
"""
A workspace holding the parked left tool, 1.37 m high, but none of the table below
1.1 m, which the pick reaches down past to the cube.
"""

BOUNDARY_TOLERANCE = 0.02
"""
How far, in metres, the tool may end beyond the box's boundary once stopped.
"""


@dataclass
class StoppedRun:
    """
    How a run held to the low workspace ended.
    """

    demo: PlannedTaskDemo
    """
    The demo, its world as the run left it.
    """

    violation: SafetyConstraintViolated
    """
    What stopped the run.
    """

    def tool_height(self) -> float:
        """
        :return: How high the left tool stands where the run stopped, in the plan's
            frame.
        """
        world = self.demo.bodies["red_cube"]._world
        [tracy] = world.get_semantic_annotations_by_type(Tracy)
        tool = tracy.left_arm.end_effector.tool_frame
        return float(world.compute_forward_kinematics_np(world.root, tool)[2, 3])


@pytest.fixture(scope="module")
def stopped_run(tmp_path_factory) -> StoppedRun:
    """
    :return: The pick run in MuJoCo, held to the low workspace and the profile's
        velocity limits.
    """
    if not Tracy.is_description_installed():
        pytest.skip("iai_tracy_description is not installed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
        patch.setenv(EnvironmentVariable.DATA, str(tmp_path_factory.mktemp("data")))
        plan = PlanDocument.from_json(json.loads(PICK.read_text("utf-8")))
        safety = SafetyLimits(
            workspaces=[LOW_WORKSPACE],
            maximum_tool_velocity=0.5,
        )
        demo = PlannedTaskDemo.of_document(
            DemoDocument(plan=plan, safety=safety), port=0
        )
        demo.execution = PhysicalExecution()
        with pytest.raises(SafetyConstraintViolated) as violated:
            demo.run()
    return StoppedRun(demo=demo, violation=violated.value)


def test_leaving_the_workspace_stops_the_run_naming_the_box(stopped_run):
    assert stopped_run.violation.constraint == LOW_WORKSPACE.label


def test_the_tool_stops_at_the_boundary_of_the_box(stopped_run):
    overshoot = LOW_WORKSPACE.box.min_z - stopped_run.tool_height()
    print(f"the tool stopped {overshoot * 1000:.1f} mm beyond the box")
    assert 0.0 <= overshoot < BOUNDARY_TOLERANCE


def test_a_safety_stop_is_never_attempted_again(stopped_run):
    outcomes = [
        entry.outcome
        for entry in stopped_run.demo.run_log.entries
        if isinstance(entry, StepAttempt)
    ]
    assert outcomes == [AttemptOutcome.SUCCEEDED, AttemptOutcome.STOPPED]


def test_the_run_events_tell_which_step_the_safety_stop_ended(stopped_run):
    events = stopped_run.demo.run_log.events()
    [watched, *_] = [event for event in events if event.monitor is RunMonitor.SAFETY]
    [stop] = [event for event in events if event.kind is RunEventKind.SAFETY_STOP]
    pick = [
        event.state
        for event in events
        if event.kind is RunEventKind.STEP and event.step == "step_2"
    ]
    assert watched.observation is MonitorObservation.HOLDS
    assert pick == [StepState.PENDING, StepState.RUNNING, StepState.FAILED]
    assert stop == RunEvent(
        kind=RunEventKind.SAFETY_STOP,
        step="step_2",
        constraint=(
            f"{LOW_WORKSPACE.label}: {stopped_run.violation.violation}. "
            f"{ArmOutsideItsWorkspace.recovery('the left arm')}"
        ),
    )


def test_the_safety_stop_says_how_far_the_tool_left_the_box(stopped_run):
    """
    The plan builder's banner shows the stop's constraint as the run log wrote it.
    """
    [stop] = [
        entry
        for entry in stopped_run.demo.run_log.entries
        if isinstance(entry, SafetyStopped)
    ]
    assert stop.constraint.startswith(f"{LOW_WORKSPACE.label}: ")
    assert "mm past the box's lower z bound" in stop.constraint
    assert stop.constraint in stop.line()


def test_a_tool_leaving_its_box_tells_how_to_bring_the_arm_back(stopped_run):
    [stop] = [
        entry
        for entry in stopped_run.demo.run_log.entries
        if isinstance(entry, SafetyStopped)
    ]
    assert stop.constraint.endswith(ArmOutsideItsWorkspace.recovery("the left arm"))


def test_a_tool_leaving_its_box_leaves_the_new_validation_to_the_page(stopped_run):
    """
    The plan builder's banner asks for the new validation once, where one is needed
    before the restart.
    """
    [stop] = [
        entry
        for entry in stopped_run.demo.run_log.entries
        if isinstance(entry, SafetyStopped)
    ]
    assert "validate" not in stop.constraint


def test_an_arm_outside_its_box_before_a_run_says_to_validate_once_it_is_back():
    outside = ArmOutsideItsWorkspace(arm="left", axis="z", position=1.0, bound=1.1)
    assert outside.message().endswith(
        f"{ArmOutsideItsWorkspace.recovery('the left arm')}, then validate again."
    )


def test_a_demos_safety_stop_is_reported_as_the_line_the_plan_builder_reads(capsys):
    stopped = (
        f"{LOW_WORKSPACE.label}: tracy/left_tool_frame is 20.0 mm past the box's "
        "lower z bound: z=0.200 m against 0.220 m in tracy/base_footprint"
    )
    log = RunLog(stream=io.StringIO(), listeners=[PlanDemoCommand.report_safety_stop])
    log.write(SafetyStopped(constraint=stopped, step="step_2"))
    assert SafetyStop.from_output(capsys.readouterr().out) == SafetyStop(
        constraint=stopped
    )


# %% velocity limits


@pytest.fixture
def tracy_context(tracy_with_a_block) -> TaskContext:
    """
    :return: The world of Tracy and a block, Tracy performing.
    """
    [tracy] = tracy_with_a_block.get_semantic_annotations_by_type(Tracy)
    return TaskContext(world=tracy_with_a_block, robot=tracy)


TOOL_LIMITS = SafetyLimits(
    maximum_tool_velocity=0.5, maximum_real_robot_tool_velocity=0.1
)
"""
A tool velocity for every run, and a lower one for a run on the real robot.
"""


@needs_tracy
def test_every_arm_joint_is_held_to_its_own_velocity_limit(tracy_context):
    """
    The joints are held to the limits the robot's description sets each of them, which
    the controller plans every motion within.
    """
    envelope = RobotSafety(limits=SafetyLimits()).envelope(tracy_context)

    watched = [
        connection
        for constraint in envelope.constraints
        if isinstance(constraint, MaximumJointVelocity)
        for connection in constraint.create_monitor().limited_connections
    ]
    arm_joints = {
        connection
        for arm in tracy_context.robot.all_arms
        for connection in arm.connections
        if isinstance(connection, ActiveConnection1DOF)
    }
    assert set(watched) == arm_joints
    assert len(watched) == len(arm_joints)


@needs_tracy
@pytest.mark.parametrize(
    "on_the_real_robot, expected",
    [
        (False, TOOL_LIMITS.maximum_tool_velocity),
        (True, TOOL_LIMITS.maximum_real_robot_tool_velocity),
    ],
)
def test_a_run_on_the_real_robot_holds_the_tools_to_its_own_velocity(
    tracy_context, on_the_real_robot, expected
):
    envelope = RobotSafety(
        limits=TOOL_LIMITS, on_the_real_robot=on_the_real_robot
    ).envelope(tracy_context)

    limits = [
        constraint.maximum_velocity
        for constraint in envelope.constraints
        if isinstance(constraint, MaximumToolVelocity)
    ]
    assert limits == [expected] * 2
