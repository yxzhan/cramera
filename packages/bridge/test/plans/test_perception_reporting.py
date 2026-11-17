"""
A run reporting what RoboKudo answered every detection of its steps: the pick of the
red cube run kinematically, its detections answered by the world model, while RoboKudo's
answers are those of the real table on 2026-11-12, where the red cube validated at one
spot was reported 19 cm away; here 24 cm from where the plan holds it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

pytest.importorskip("robokudo_msgs")

from geometry_msgs.msg import PoseStamped
from robokudo_msgs.msg import ObjectDesignator, ShapeSize
from typing_extensions import List, Optional

from cramera.locations import EnvironmentVariable
from cramera_bridge.execution import KinematicExecution
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.objects import LIBRARY_VARIABLE
from cramera_bridge.plans.perception_reporting import PerceptionReported
from cramera_bridge.plans.robokudo_answers import QueryAnswer
from cramera_protocol.plan_builder import (
    DemoDocument,
    PlanDocument,
    ReportedObjectVerdict,
    RunEventKind,
)
from semantic_digital_twin.robots.tracy import Tracy

from ..dataset.bundles import DATASET

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube, each named by its color.
"""

TRACY_PICK = DATASET / "plans" / "tracy_pick.json"
"""
Tracy parks its arms, then picks up the red cube, at x 0.55, y 0.1.
"""


@dataclass
class TableAnswers:
    """
    Answers every query as RoboKudo answered on the real table: the red cube 24 cm from
    where the plan holds it, and the table top as two grey objects.
    """

    demo: PlannedTaskDemo
    """
    The demo whose world names the frame of the answers.
    """

    asked: List[int] = field(default_factory=list)
    """
    How many queries each plan asked.
    """

    def since(self, start: float, count: int, timeout: float) -> List[QueryAnswer]:
        self.asked.append(count)
        return [
            QueryAnswer(accepted=start, ended=True, objects=self.objects())
            for _ in range(count)
        ]

    def objects(self) -> List[ObjectDesignator]:
        """
        :return: The objects RoboKudo reported.
        """
        return [
            self.designator(0.79, 0.145, 0.906, (0.066, 0.061, 0.022), ["red"]),
            self.designator(0.82, 0.3, 0.9, (0.8, 0.5, 0.06), ["grey"]),
            self.designator(0.85, -0.3, 0.9, (0.8, 0.5, 0.08), ["grey"]),
        ]

    def designator(
        self, x: float, y: float, z: float, size: tuple, colors: List[str]
    ) -> ObjectDesignator:
        """
        :return: An object as RoboKudo reports it, in the world's root.
        """
        pose = PoseStamped()
        pose.header.frame_id = str(self.demo.step_run.context.world.root.name)
        pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = x, y, z
        pose.pose.orientation.w = 1.0
        shape = ShapeSize()
        shape.dimensions.x, shape.dimensions.y, shape.dimensions.z = size
        return ObjectDesignator(color=colors, pose=[pose], shape_size=[shape])


@dataclass
class AnsweredKinematicExecution(KinematicExecution):
    """
    Moves kinematically, with RoboKudo's answers of the real table.
    """

    answers: Optional[TableAnswers] = None
    """
    The answers.
    """

    def perception_answers(self) -> Optional[TableAnswers]:
        return self.answers


@pytest.fixture(scope="module")
def reports(tmp_path_factory) -> List[PerceptionReported]:
    """
    :return: What the run of the pick, its arm left open, reported of its detections.
    """
    if not Tracy.is_description_installed():
        pytest.skip("iai_tracy_description is not installed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
        patch.setenv(EnvironmentVariable.DATA, str(tmp_path_factory.mktemp("data")))
        document = json.loads(TRACY_PICK.read_text("utf-8"))
        document["robots"][0]["steps"][1]["values"]["arm"] = "automatic"
        plan = PlanDocument.from_json(document)
        demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
        demo.execution = AnsweredKinematicExecution(answers=TableAnswers(demo))
        demo.run()
    return [
        entry for entry in demo.run_log.entries if isinstance(entry, PerceptionReported)
    ]


@dataclass
class MovedCubeDemo(PlannedTaskDemo):
    """
    Runs the pick with the red cube moved, after the plan was validated, to where
    RoboKudo reported it on the real table.
    """

    def perform_plan(self, plan, context, performance, extensions) -> None:
        z = self.step_run.steps[1].expected_positions["red_cube"][2]
        self.step_run.put_back(self.bodies["red_cube"], [0.79, 0.145, z])
        super().perform_plan(plan, context, performance, extensions)


@pytest.fixture(scope="module")
def moved_reports(tmp_path_factory) -> List[PerceptionReported]:
    """
    :return: What the run of the pick reported of its detections when the cube lay
        24 cm from where the plan was validated and the step was planned again where it
        was found.
    """
    if not Tracy.is_description_installed():
        pytest.skip("iai_tracy_description is not installed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
        patch.setenv(EnvironmentVariable.DATA, str(tmp_path_factory.mktemp("data")))
        document = json.loads(TRACY_PICK.read_text("utf-8"))
        document["robots"][0]["steps"][1]["values"]["arm"] = "automatic"
        plan = PlanDocument.from_json(document)
        demo = MovedCubeDemo.of_document(DemoDocument(plan=plan), port=0)
        demo.execution = AnsweredKinematicExecution(answers=TableAnswers(demo))
        demo.run()
    return [
        entry for entry in demo.run_log.entries if isinstance(entry, PerceptionReported)
    ]


def test_the_check_of_the_picked_cube_reports_it_24_cm_away(reports):
    first = reports[0]
    report = first.report
    assert first.step == "step_2"
    assert (report.target, report.color, report.tolerance) == ("red_cube", "red", 0.02)
    assert (report.grounded.x, report.grounded.y) == pytest.approx((0.55, 0.1))
    assert [seen.verdict for seen in report.objects] == [
        ReportedObjectVerdict.OUTSIDE_BOX,
        ReportedObjectVerdict.WRONG_COLOR,
        ReportedObjectVerdict.WRONG_COLOR,
    ]
    assert report.summary.startswith("The red cube was seen 24 cm from where the plan")


def test_the_report_names_the_object_as_the_person_knows_it(reports):
    report = reports[0].report
    assert report.looked_for == "the red cube"
    assert "planned" not in report.summary.lower()


def test_a_step_planned_again_measures_from_where_the_cube_was_last_seen(
    moved_reports,
):
    """
    The table-wide search found the cube 24 cm away and the step was planned again
    there; its next detection found it where it was last seen, which is not where the
    plan was validated.
    """
    again = next(
        entry.report
        for entry in moved_reports
        if entry.report.validated is not None and entry.report.objects
    )
    assert (again.validated.x, again.validated.y) == pytest.approx((0.55, 0.1))
    assert (again.grounded.x, again.grounded.y) == pytest.approx((0.79, 0.145))
    assert again.summary == (
        "The red cube was found 0 cm from where it was last seen, 24 cm from where "
        "the plan was validated."
    )


def test_every_detection_the_run_ran_is_reported_as_an_event(reports):
    assert len(reports) >= 2
    assert all(entry.step == "step_2" for entry in reports)
    assert all(entry.event().kind is RunEventKind.PERCEPTION for entry in reports)


def test_a_grasp_looks_around_the_cube_where_it_lay_before_the_pick(reports):
    grasp = reports[-1].report
    low, high = grasp.box.minimum, grasp.box.maximum
    centre = ((low.x + high.x) / 2, (low.y + high.y) / 2, (low.z + high.z) / 2)
    assert centre == pytest.approx((0.55, 0.1, 0.906), abs=0.01)


def test_an_object_reported_inside_the_table_says_how_far_the_cube_would_lie_inside(
    reports,
):
    """
    RoboKudo reported the grey table top with its centre at 0.9 m, which would put the
    5 cm red cube 5 mm inside the table, whose top lies at 0.88 m.
    """
    [_, table_top, _] = reports[0].report.objects
    assert table_top.inside_surface.surface == "table"
    assert table_top.inside_surface.depth == pytest.approx(0.005, abs=1e-6)
