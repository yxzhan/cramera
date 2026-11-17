"""
Steps looking for objects on Tracy's table: the region they look in, the kind of object
they look for, and the object a later step names by the step that finds it.
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from coraplex.robot_plans.actions.core.misc import DetectAction
from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from cramera_bridge.exceptions import AmbiguousDetectedObject, DetectedObjectNotInTwin
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.objects import LIBRARY_VARIABLE
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.world import PlannedGraspableFromAbove
from cramera_protocol.detection import DetectedObjectReference, DetectionRegion
from cramera_protocol.plan_builder import DemoDocument, PlanDocument
from cramera_protocol.viewer import EnvironmentVariable
from semantic_digital_twin.robots.tracy import Tracy

from ..dataset.bundles import DATASET

pytestmark = pytest.mark.skipif(
    not Tracy.is_description_installed(),
    reason="iai_tracy_description is not installed",
)

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube.
"""

TRACY_CUBES = DATASET / "plans" / "tracy_cubes.json"
"""
Tracy detects the red cube among three on its table and picks up what it found.
"""

# %% fixtures


@pytest.fixture
def plan(monkeypatch, tmp_path) -> PlanDocument:
    """
    :return: The plan of Tracy and its cubes, the cube library offered.
    """
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "data"))
    return PlanDocument.from_json(json.loads(TRACY_CUBES.read_text("utf-8")))


def built_demo(plan: PlanDocument):
    """
    :return: The plan's demo, its world built and its plan of native actions.
    """
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    world = demo.build_world()
    context = demo.build_context(world)
    return demo, context, demo.build_plan(context)


def with_detected_color(plan: PlanDocument, color: str | None) -> PlanDocument:
    """
    :return: The plan with its detection looking for ``color``, any color for None.
    """
    [robot] = plan.robots
    detect, *rest = robot.steps
    values = {name: value for name, value in detect.values.items() if name != "color"}
    if color is not None:
        values["color"] = color
    return replace(
        plan, robots=[replace(robot, steps=[replace(detect, values=values), *rest])]
    )


# %% regions


def test_the_whole_table_spans_the_table_top_and_holds_the_cubes(plan):
    demo, context, _ = built_demo(plan)

    table = RegionBoxes(context.robot).box(DetectionRegion())

    assert table.min_z == pytest.approx(context.robot.table.top_z, abs=1e-6)
    for body in demo.bodies.values():
        assert table.contains(body.global_pose.position)


# %% native steps


def test_a_detect_step_looks_for_its_kind_of_object_on_the_table(plan):
    _, context, native = built_demo(plan)

    detect = native.nodes[0]

    assert isinstance(detect, DetectAction)
    assert detect.object_sem_annotation is PlannedGraspableFromAbove
    region = RegionBoxes(context.robot).table()
    np.testing.assert_allclose(
        [detect.detection_box.min_x, detect.detection_box.max_z],
        [region.min_x, region.max_z],
    )


def test_a_later_step_takes_the_object_the_detection_finds(plan):
    demo, _, native = built_demo(plan)

    pick = native.nodes[1]

    reference = DetectedObjectReference("step_1").text
    assert demo.bodies[reference] is demo.bodies["red_cube"]
    assert isinstance(pick, PickUpAction)
    assert pick.grasp.graspable.root is demo.bodies["red_cube"]


def test_a_detection_finding_several_objects_cannot_be_named(plan):
    with pytest.raises(AmbiguousDetectedObject):
        built_demo(with_detected_color(plan, None))


def test_a_detection_finding_nothing_in_the_twin_cannot_be_named(plan):
    with pytest.raises(DetectedObjectNotInTwin):
        built_demo(with_detected_color(plan, "magenta"))
