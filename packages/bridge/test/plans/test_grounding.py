"""
Grounding and validating a plan of Tracy's: its steps are tried in a copy of the twin
without moving anything, a pick leaving its arm open is given the arm that reaches, the
nearer one first, a cube out of reach fails the pick as unreachable, and the plan then
runs with the arm its grounding chose.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import timedelta
from importlib.util import find_spec
from pathlib import Path

import numpy as np
import pytest

from coraplex.exceptions import NothingPerceived, PerceptionSourceDidNotAnswer
from coraplex.perception import ROBOKUDO_QUERY_ACTION_NAME, WorldPerception
from coraplex.plans.executors import KinematicallySimulatedPlanExecutor, PlanExecutor
from coraplex.robot_plans.plan_transformations import DetectWithRoboKudo
from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from cramera_bridge.exceptions import (
    ArmOutsideItsWorkspace,
    StepAbandoned,
    StepNotGrounded,
)
from cramera_bridge.execution import (
    PERCEPTION_ENGINE_VARIABLE,
    KinematicExecution,
    StartingPosture,
)
from cramera_bridge.geometry import NumericPose
from cramera_bridge.plans.arms import Arms, RobotArms
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.grounding import PlanGrounding
from cramera_bridge.plans.objects import LIBRARY_VARIABLE
from cramera_bridge.plans.safety import WORKSPACE_MARGIN
from cramera_bridge.plans.steps import StepActions
from cramera_bridge.plans.validating import PlanValidator
from cramera_bridge.plans.world import PlannedGraspableFromAbove
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.detection import DetectionRegion, RegionKind
from cramera_protocol.geometry import Pose
from cramera_protocol.plan_builder import (
    DemoDocument,
    Destination,
    PlacementMode,
    PlanDocument,
)
from cramera_protocol.robot_profile import (
    PerceptionSettings,
    SafetyLimits,
    Zone,
)
from cramera_protocol.validation import (
    StepFailureReason,
    StepVerdict,
    ValidationOrder,
    ValidationReport,
)
from cramera_protocol.viewer import EnvironmentVariable
from cramph.composites import StatementNode
from cramph.context import StatechartContext
from cramph.executor import ExecutorExtension
from giskardpy.motion_statechart.monitors.safety_monitors import SafetyEnvelope
from rclpy.node import Node
from typing_extensions import Sequence
from rclpy.executors import MultiThreadedExecutor
from semantic_digital_twin.robots.tracy import Tracy
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF

from ..dataset.bundles import DATASET

pytestmark = pytest.mark.skipif(
    not Tracy.is_description_installed(),
    reason="iai_tracy_description is not installed",
)

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube, each named by its color.
"""

TRACY_PICK_AND_PLACE = DATASET / "plans" / "tracy_pick_and_place.json"
"""
Tracy picks the red cube with the arm that reaches it and places it.
"""

TRACY_CUBES = DATASET / "plans" / "tracy_cubes.json"
"""
Tracy detects the red cube on its table and picks it up.
"""

OUT_OF_REACH = (1.5, 0.1)
"""
Where on the floor beyond Tracy's table no arm reaches a cube.
"""

RIGHT_ARM_ONLY = (0.55, -1.2)
"""
Where beside Tracy's table only its right arm reaches a cube.
"""

BEHIND_TRACY = (-1.0, 0.0)
"""
Where on the floor behind Tracy its camera does not look.
"""

CUBE_HALF_HEIGHT = 0.025
"""
How high, in metres, the centre of a cube standing on the floor is.
"""

REGION_FIELD = "region"
"""
The field of a detection naming the region it looks in.
"""

RIGHT_OF_CENTRE = (0.55, -0.15)
"""
Where on Tracy's table both arms reach a cube, the right one from nearer.
"""

TRACY_SAFETY = SafetyLimits.from_json(
    {
        "workspaces": [
            {
                "arm": "left",
                "label": "Left arm workspace",
                "box": {
                    "minX": 0.3,
                    "maxX": 1.4,
                    "minY": -0.3,
                    "maxY": 0.9,
                    "minZ": 0.9,
                    "maxZ": 1.6,
                },
            },
            {
                "arm": "right",
                "label": "Right arm workspace",
                "box": {
                    "minX": 0.3,
                    "maxX": 1.4,
                    "minY": -0.9,
                    "maxY": 0.3,
                    "minZ": 0.9,
                    "maxZ": 1.6,
                },
            },
        ]
    }
)
"""
The workspace boxes of Tracy's profile: each arm's box ends where the other arm's half
of the table begins, 30 cm across the middle.
"""

LEFT_TRAY = Zone.from_json(
    {
        "id": "left_tray",
        "label": "Left tray",
        "area": {"minX": 0.6, "maxX": 0.8, "minY": 0.3, "maxY": 0.5},
    }
)
"""
The tray of Tracy's profile on the left of its table, inside the left arm's workspace
only.
"""

SCANNED_RED_CUBE = (0.673, -0.138)
"""
Where the scan of the close-to-real stand-in of Tracy's camera sees the red cube: right
of the table's middle, nearer the right arm, inside both arms' workspaces.
"""

SCANNED_BLUE_CUBE = (0.662, 0.138)
"""
Where the same scan sees the blue cube: on the place the pick-and-place template puts
the red cube down at.
"""

RIGHT_ARM_WORKSPACE_ONLY = (0.6, -0.4)
"""
Where on Tracy's table a cube lies inside the right arm's workspace only.
"""

# %% fixtures


@pytest.fixture(autouse=True)
def cube_library(monkeypatch) -> None:
    """
    Offer the cube library the plans' cubes come from.
    """
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))


@pytest.fixture
def pick_and_place() -> PlanDocument:
    """
    :return: The plan of Tracy picking the red cube with the arm that reaches it.
    """
    return PlanDocument.from_json(json.loads(TRACY_PICK_AND_PLACE.read_text("utf-8")))


def with_red_cube_at(plan: PlanDocument, x: float, y: float) -> PlanDocument:
    """
    :return: The plan with its red cube standing at the position on the table's height.
    """
    [red, *others] = plan.objects
    pose = red.pose.to_json()
    return replace(plan, objects=[replace(red, pose=Pose(x, y, *pose[2:])), *others])


def validated(plan: PlanDocument, safety: SafetyLimits = None) -> ValidationReport:
    """
    :param plan: A plan of Tracy's.
    :param safety: The safety limits the plan's motions are held to, None for none.
    :return: The report of the plan's validation, with the left tray of Tracy's profile.
    """
    return PlanValidator(
        ValidationOrder(demo=DemoDocument(plan=plan, zones=[LEFT_TRAY], safety=safety))
    ).validate()


def with_cube_at(
    plan: PlanDocument, identifier: str, x: float, y: float
) -> PlanDocument:
    """
    :return: The plan with the cube standing at the position on the table's height.
    """
    return replace(
        plan,
        objects=[
            (
                replace(planned, pose=Pose(x, y, *planned.pose.to_json()[2:]))
                if planned.identifier == identifier
                else planned
            )
            for planned in plan.objects
        ],
    )


def placing_at(plan: PlanDocument, destination: Destination) -> PlanDocument:
    """
    :return: The plan with its place step putting the red cube down at the destination.
    """
    [robot] = plan.robots
    steps = [
        (
            replace(
                step,
                values={**step.values, "target_location": destination.to_json()},
            )
            if step.action == "place"
            else step
        )
        for step in robot.steps
    ]
    return replace(plan, robots=[replace(robot, steps=steps)])


def with_pick_arm(plan: PlanDocument, arm: Arms) -> PlanDocument:
    """
    :return: The plan with its pick naming the arm.
    """
    [robot] = plan.robots
    steps = [
        (
            replace(step, values={**step.values, "arm": arm.name.lower()})
            if step.action == "pick_up"
            else step
        )
        for step in robot.steps
    ]
    return replace(plan, robots=[replace(robot, steps=steps)])


# %% validating


def test_a_plan_in_reach_passes_with_the_nearer_arm_that_reaches(pick_and_place):
    report = validated(pick_and_place)

    assert report.passed
    assert [step.verdict for step in report.steps] == [StepVerdict.VALID] * 4
    assert report.steps[1].arm == Arms.LEFT.name.lower()


def test_a_cube_out_of_reach_fails_the_pick_as_unreachable(pick_and_place):
    report = validated(with_red_cube_at(pick_and_place, *OUT_OF_REACH))

    assert not report.passed
    pick, *later = report.steps[1:]
    assert pick.verdict is StepVerdict.FAILED
    assert pick.reason is StepFailureReason.UNREACHABLE
    assert [step.verdict for step in later] == [StepVerdict.UNCHECKED] * 2


def test_a_cube_only_the_right_arm_reaches_is_picked_with_it(pick_and_place):
    report = validated(with_red_cube_at(pick_and_place, *RIGHT_ARM_ONLY))

    assert report.passed
    assert report.steps[1].arm == Arms.RIGHT.name.lower()


def test_a_cube_both_arms_reach_is_picked_with_the_nearer_one(pick_and_place):
    report = validated(with_red_cube_at(pick_and_place, *RIGHT_OF_CENTRE))

    assert report.passed
    assert report.steps[1].arm == Arms.RIGHT.name.lower()


def test_a_place_before_any_pick_fails_saying_a_pick_must_come_first(pick_and_place):
    [robot] = pick_and_place.robots
    park, pick, place, park_again = robot.steps
    plan = replace(
        pick_and_place,
        robots=[replace(robot, steps=[park, place, pick, park_again])],
    )

    report = validated(plan)

    placing = report.steps[1]
    assert placing.step == place.identifier
    assert placing.verdict is StepVerdict.FAILED
    assert "red cube" in placing.detail
    assert "Pick" in placing.detail
    assert [step.verdict for step in report.steps[2:]] == [StepVerdict.UNCHECKED] * 2


def test_a_second_pick_of_a_held_cube_fails_naming_the_step_holding_it(
    pick_and_place,
):
    [robot] = pick_and_place.robots
    park, pick, place, park_again = robot.steps
    again = replace(pick, identifier="step_5")
    plan = replace(
        pick_and_place,
        robots=[replace(robot, steps=[park, pick, again, place, park_again])],
    )

    report = validated(plan)

    assert report.steps[1].verdict is StepVerdict.VALID
    picking_again = report.steps[2]
    assert picking_again.step == again.identifier
    assert picking_again.verdict is StepVerdict.FAILED
    assert "red cube" in picking_again.detail
    assert "since step 2" in picking_again.detail
    assert [step.verdict for step in report.steps[3:]] == [StepVerdict.UNCHECKED] * 2


def test_a_cube_placed_and_picked_again_validates(pick_and_place):
    [robot] = pick_and_place.robots
    park, pick, place, park_again = robot.steps
    again = replace(pick, identifier="step_5")
    back = replace(
        place,
        identifier="step_6",
        values={
            **place.values,
            "target_location": {
                "placement": "pose",
                "pose": [0.68, -0.05, 0.906, 0, 0, 0, 1],
            },
        },
    )
    plan = replace(
        pick_and_place,
        robots=[replace(robot, steps=[park, pick, place, again, back, park_again])],
    )

    report = validated(plan)

    assert [step.verdict for step in report.steps] == [StepVerdict.VALID] * 6


def test_validating_moves_nothing_of_the_twin(pick_and_place):
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=pick_and_place), port=0)
    world = demo.build_world()
    context = demo.build_context(world)
    before = world.state.positions.copy()

    PlanGrounding(
        context=context,
        bodies=dict(demo.bodies),
        actions=demo.discovered_actions,
        perception=WorldPerception(),
    ).ground(demo.steps())

    np.testing.assert_array_equal(world.state.positions, before)


# %% validating where the safety monitor watches


def test_a_place_onto_another_object_fails_saying_which_object_stands_there(
    pick_and_place,
):
    report = validated(with_cube_at(pick_and_place, "blue_cube", *SCANNED_BLUE_CUBE))

    place = report.steps[2]
    assert place.verdict is StepVerdict.FAILED
    assert place.reason is StepFailureReason.UNSUCCESSFUL
    assert "blue_cube" in place.detail


def test_a_cube_the_nearer_arm_cannot_put_into_the_zone_is_carried_by_the_other(
    pick_and_place,
):
    plan = placing_at(
        with_cube_at(pick_and_place, "red_cube", *SCANNED_RED_CUBE),
        Destination(placement=PlacementMode.ZONE, zone=LEFT_TRAY.identifier),
    )

    report = validated(plan, TRACY_SAFETY)

    assert report.passed
    assert report.steps[1].arm == Arms.LEFT.name.lower()


def test_a_cube_no_arm_can_pick_and_put_into_the_zone_fails_saying_so(
    pick_and_place,
):
    plan = placing_at(
        with_cube_at(pick_and_place, "red_cube", *RIGHT_ARM_WORKSPACE_ONLY),
        Destination(placement=PlacementMode.ZONE, zone=LEFT_TRAY.identifier),
    )

    report = validated(plan, TRACY_SAFETY)

    pick = report.steps[1]
    assert pick.verdict is StepVerdict.FAILED
    assert pick.reason is StepFailureReason.SAFETY_LIMIT
    assert "No arm can pick red_cube" in pick.detail
    assert "left_tray" in pick.detail


def test_a_place_riding_the_edge_of_its_arms_workspace_fails_validation(
    pick_and_place,
):
    plan = placing_at(
        with_pick_arm(
            with_cube_at(pick_and_place, "red_cube", *SCANNED_RED_CUBE), Arms.RIGHT
        ),
        Destination(
            placement=PlacementMode.POSE, pose=Pose(0.7, 0.295, 0.906, 0, 0, 0, 1)
        ),
    )

    report = validated(plan, TRACY_SAFETY)

    place = report.steps[2]
    assert place.verdict is StepVerdict.FAILED
    assert place.reason is StepFailureReason.SAFETY_LIMIT
    assert "Right arm workspace" in place.detail


def parked_left_tool_height(plan: PlanDocument) -> float:
    """
    :return: How high, in metres, the left arm's tool stands when the plan starts, its
        arms parked.
    """
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    world = demo.build_world()
    context = demo.build_context(world)
    [left] = RobotArms(context.robot).arms(Arms.LEFT)
    return float(NumericPose.of_entity(world, left.end_effector.tool_frame).position[2])


def with_left_box_top_at(safety: SafetyLimits, height: float) -> SafetyLimits:
    """
    :return: The safety limits with the left arm's workspace box ending at the height.
    """
    left, *others = safety.workspaces
    return replace(
        safety,
        workspaces=[replace(left, box=replace(left.box, max_z=height)), *others],
    )


def test_a_plan_starting_with_a_tool_inside_its_box_but_near_its_edge_validates(
    pick_and_place,
):
    height = parked_left_tool_height(pick_and_place) + WORKSPACE_MARGIN / 2
    plan = with_pick_arm(pick_and_place, Arms.LEFT)

    report = validated(plan, with_left_box_top_at(TRACY_SAFETY, height))

    assert report.passed, report


def test_a_plan_starting_with_a_tool_outside_its_box_fails_saying_where_it_stands(
    pick_and_place,
):
    height = parked_left_tool_height(pick_and_place) - 0.006

    report = validated(pick_and_place, with_left_box_top_at(TRACY_SAFETY, height))

    first, *later = report.steps
    assert first.verdict is StepVerdict.FAILED
    assert first.reason is StepFailureReason.SAFETY_LIMIT
    assert "left arm stands outside its workspace box" in first.detail
    assert "6.0 mm past the box's upper z bound" in first.detail
    assert ArmOutsideItsWorkspace.recovery("the left arm") in first.detail
    assert all(step.verdict is StepVerdict.UNCHECKED for step in later)


def test_a_grounding_holds_the_boxes_where_its_copy_stands_once_it_grounds(
    pick_and_place,
):
    """
    A run on the real robot puts its grounding's copy where the drivers report the arms
    only once the grounding is made, as the world Giskard serves holds every arm joint
    at 0 before its first motion.
    """
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=pick_and_place), port=0)
    world = demo.build_world()
    context = demo.build_context(world)
    for arm in context.robot.all_arms:
        for connection in arm.connections:
            if isinstance(connection, ActiveConnection1DOF):
                connection.position = 0.0
    grounding = PlanGrounding(
        context=context,
        bodies=dict(demo.bodies),
        actions=demo.discovered_actions,
        perception=WorldPerception(),
        site=demo.site,
        safety=TRACY_SAFETY,
    )

    StartingPosture().apply(grounding.copied_context.world)
    grounded = grounding.ground(demo.steps())

    assert all(step.succeeded for step in grounded), [
        step.to_step_validation() for step in grounded
    ]


# %% validating in a process started before its order


@dataclass
class ValidatorProcess:
    """
    A validator process of its own, answering one order through files.
    """

    directory: Path
    """
    The directory holding its order and its report.
    """

    process: subprocess.Popen = field(init=False)
    """
    The running validator.
    """

    @property
    def order_file(self) -> Path:
        """
        :return: The file the validator reads its order from.
        """
        return self.directory / "order.json"

    @property
    def report_file(self) -> Path:
        """
        :return: The file the validator writes its report to.
        """
        return self.directory / "report.json"

    def start(self, stdin: int) -> None:
        """
        Start the validator, which reads its order once its input closes.

        :param stdin: The validator's input, a pipe to close once the order is written.
        """
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "cramera_bridge.plans.validating",
                str(self.order_file),
                str(self.report_file),
            ],
            stdin=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def write_order(self, plan: PlanDocument) -> None:
        """
        Write the order to validate the plan.
        """
        self.directory.mkdir(exist_ok=True)
        order = ValidationOrder(demo=DemoDocument(plan=plan))
        self.order_file.write_text(json.dumps(order.to_json()), encoding="utf-8")

    def report(self) -> ValidationReport:
        """
        :return: The report the validator wrote, once it ended.
        """
        assert self.process.wait(timeout=600) == 0
        return ValidationReport.from_json(
            json.loads(self.report_file.read_text("utf-8"))
        )


def test_a_validator_started_before_its_order_reports_as_one_started_with_it(
    tmp_path, pick_and_place
):
    failing = with_red_cube_at(pick_and_place, *OUT_OF_REACH)
    plans = [pick_and_place, failing, pick_and_place]
    started_first = [ValidatorProcess(tmp_path / f"first_{n}") for n in range(3)]
    started_with_order = [ValidatorProcess(tmp_path / f"with_{n}") for n in range(2)]
    for validator in started_first:
        validator.directory.mkdir()
        validator.start(subprocess.PIPE)
    for validator, plan in zip(started_with_order, plans):
        validator.write_order(plan)
        validator.start(subprocess.DEVNULL)

    reports_started_first = []
    for validator, plan in zip(started_first, plans):
        validator.write_order(plan)
        validator.process.stdin.close()
        reports_started_first.append(validator.report())
    reports_started_with_order = [
        validator.report() for validator in started_with_order
    ]

    assert [report.passed for report in reports_started_first] == [True, False, True]
    assert reports_started_first == [
        *reports_started_with_order,
        reports_started_with_order[0],
    ]


# %% running with the arm chosen


def test_a_step_leaving_its_arm_open_is_a_statement(pick_and_place):
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=pick_and_place), port=0)
    world = demo.build_world()
    context = demo.build_context(world)
    pick = demo.steps()[1]

    node = StepActions(context, demo.bodies, demo.discovered_actions).action(pick)

    assert isinstance(node, StatementNode)


def test_a_plan_runs_with_the_arm_its_grounding_chose(pick_and_place):
    demo = PlannedTaskDemo.of_document(
        DemoDocument(plan=with_red_cube_at(pick_and_place, *RIGHT_ARM_ONLY)), port=0
    )
    world = demo.build_world()
    context = demo.build_context(world)

    plan = demo.build_plan(context)

    step = plan.nodes[1]
    [pick] = [
        node for node in [step, *step.descendants] if isinstance(node, PickUpAction)
    ]
    assert pick.arm is context.robot.get_right_arm_if_specified()


def test_a_plan_no_arm_reaches_is_refused_before_it_runs(pick_and_place):
    demo = PlannedTaskDemo.of_document(
        DemoDocument(plan=with_red_cube_at(pick_and_place, *OUT_OF_REACH)), port=0
    )
    world = demo.build_world()

    with pytest.raises(StepNotGrounded):
        demo.build_plan(demo.build_context(world))


# %% detecting what the camera does not see


@pytest.fixture
def detection_out_of_view() -> PlanDocument:
    """
    :return: A plan of Tracy detecting the red cube on the floor behind it, where its
        camera does not look, the cube standing there in the twin.
    """
    cubes = PlanDocument.from_json(json.loads(TRACY_CUBES.read_text("utf-8")))
    [robot] = cubes.robots
    [detect, *_] = robot.steps
    behind = DetectionRegion(
        kind=RegionKind.MARKER, pose=Pose(*BEHIND_TRACY, 0.0, 0, 0, 0, 1)
    )
    looking_behind = replace(
        detect, values={**detect.values, REGION_FIELD: behind.to_json()}
    )
    [red, *others] = cubes.objects
    on_the_floor = replace(red, pose=Pose(*BEHIND_TRACY, CUBE_HALF_HEIGHT, 0, 0, 0, 1))
    return replace(
        cubes,
        robots=[replace(robot, steps=[looking_behind])],
        objects=[on_the_floor, *others],
    )


def test_a_detection_assumed_from_the_twin_is_checked_again_when_the_plan_runs(
    detection_out_of_view, tmp_path, monkeypatch
):
    """
    The validation takes the cube from the twin; the run looks for it again through
    Tracy's camera, which does not see it, so the detection fails instead of trusting
    the twin.
    """
    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "data"))
    report = validated(detection_out_of_view)
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=detection_out_of_view), port=0)

    with pytest.raises(StepAbandoned) as abandoned:
        demo.run()

    [detect] = report.steps
    assert detect.verdict is StepVerdict.ASSUMED
    assert abandoned.value == StepAbandoned(
        detect.step,
        demo.step_run.attempts,
        NothingPerceived(PlannedGraspableFromAbove).error_message(),
    )


# %% detecting through the simulated camera


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_a_detection_the_simulated_camera_sees_is_valid_and_its_pick_holds(tmp_path):
    cubes = PlanDocument.from_json(json.loads(TRACY_CUBES.read_text("utf-8")))
    order_file = tmp_path / "order.json"
    report_file = tmp_path / "report.json"
    order = ValidationOrder(
        demo=DemoDocument(plan=cubes), simulated_objects=cubes.objects
    )
    order_file.write_text(json.dumps(order.to_json()), encoding="utf-8")

    finished = subprocess.run(
        [
            sys.executable,
            "-m",
            "cramera_bridge.plans.validating",
            str(order_file),
            str(report_file),
        ],
        env={
            **os.environ,
            LIBRARY_VARIABLE: str(CUBE_LIBRARY),
            PERCEPTION_ENGINE_VARIABLE: "tracy_cubes",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=600,
    )

    assert finished.returncode == 0, finished.stderr.decode()[-4000:]
    report = ValidationReport.from_json(json.loads(report_file.read_text("utf-8")))
    assert [step.verdict for step in report.steps] == [StepVerdict.VALID] * 2


# %% detecting through a perception that does not answer


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_a_validation_robokudo_does_not_answer_fails_after_the_answer_timeout(
    monkeypatch,
):
    from ..dataset.real_robot import RoboKudoThatNeverAnswers

    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    cubes = PlanDocument.from_json(json.loads(TRACY_CUBES.read_text("utf-8")))
    perception = PerceptionSettings(answer_timeout=0.5)
    order = ValidationOrder(demo=DemoDocument(plan=cubes, perception=perception))

    with SpinningNode("robokudo_stand_in", MultiThreadedExecutor).running() as node:
        with RoboKudoThatNeverAnswers().serving(node):
            with pytest.raises(PerceptionSourceDidNotAnswer) as raised:
                PlanValidator(order).validate()

    assert raised.value.answer_timeout == timedelta(seconds=perception.answer_timeout)


@dataclass
class KinematicExecutionAskingRoboKudo(KinematicExecution):
    """
    Moves the world kinematically and answers the plans' detections with RoboKudo, as a
    run of the real robot does.
    """

    ros_node: Node
    """
    The node the plans reach RoboKudo through.
    """

    def executor(
        self,
        context: StatechartContext,
        collision_avoidance: bool,
        extensions: Sequence[ExecutorExtension],
        safety: SafetyEnvelope,
    ) -> PlanExecutor:
        return KinematicallySimulatedPlanExecutor(
            context,
            collision_avoidance=collision_avoidance,
            extensions=list(extensions),
            safety=safety,
            ros_node=self.ros_node,
            plan_adaptations=[DetectWithRoboKudo()],
        )


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_a_run_robokudo_does_not_answer_fails_its_detection_without_another_attempt(
    tmp_path, monkeypatch
):
    """
    A pipeline that does not answer would not answer a second attempt either.
    """
    from ..dataset.real_robot import RoboKudoThatNeverAnswers

    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "data"))
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    cubes = PlanDocument.from_json(json.loads(TRACY_CUBES.read_text("utf-8")))
    [detect, *_] = cubes.robots[0].steps
    perception = PerceptionSettings(answer_timeout=0.5)
    demo = PlannedTaskDemo.of_document(
        DemoDocument(plan=cubes, perception=perception), port=0
    )

    with SpinningNode("robokudo_stand_in", MultiThreadedExecutor).running() as node:
        demo.execution = KinematicExecutionAskingRoboKudo(ros_node=node)
        with RoboKudoThatNeverAnswers().serving(node):
            with pytest.raises(StepAbandoned) as abandoned:
                demo.run()

    assert abandoned.value == StepAbandoned(
        detect.identifier,
        1,
        PerceptionSourceDidNotAnswer(
            ROBOKUDO_QUERY_ACTION_NAME,
            timedelta(seconds=perception.answer_timeout),
            "",
        ).error_message(),
    )
