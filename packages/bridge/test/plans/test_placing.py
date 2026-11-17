"""
Putting an object down where the plan only finds out once it is grounded: into a named
zone of the robot profile, on top of another object, plain or found by a detection, or
at a marker dragged across the scene; and what Tracy's arms reach at most, the quick
check a marker gets while it is dragged.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from coraplex.perception import WorldPerception
from cramera_bridge.exceptions import UnknownZone
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.grounding import GroundedStep, PlanGrounding
from cramera_bridge.plans.objects import LIBRARY_VARIABLE
from cramera_bridge.plans.placing import PlacingTargets
from cramera_bridge.plans.reaching import RobotReachMeasure
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.site import RobotSite
from cramera_bridge.plans.validating import PlanValidator
from cramera_protocol.detection import DetectionRegion, RegionKind
from cramera_protocol.geometry import FloorRectangle, Pose, Vector3
from cramera_protocol.plan_builder import (
    DemoDocument,
    Destination,
    PlacementMode,
    PlanDocument,
    PlanStep,
)
from cramera_protocol.reach import ReachRequest
from cramera_protocol.robot_profile import Zone
from cramera_protocol.validation import (
    StepFailureReason,
    StepVerdict,
    ValidationOrder,
)
from semantic_digital_twin.robots.tracy import Tracy

from ..dataset.bundles import DATASET

pytestmark = pytest.mark.skipif(
    not Tracy.is_description_installed(),
    reason="iai_tracy_description is not installed",
)

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube, each named by its color.
"""

TRACY_TOWER = DATASET / "plans" / "tracy_tower.json"
"""
Tracy puts the red cube into the centre zone and stacks the yellow and the blue cube on
top of it.
"""

CENTRE = Zone(
    identifier="center",
    label="Center",
    area=FloorRectangle(min_x=0.6, max_x=0.8, min_y=-0.1, max_y=0.1),
)
"""
The zone in the middle of Tracy's table.
"""

CUBE_SIZE = 0.05
"""
The side of each cube, in metres.
"""

TABLE_TOP = 0.88
"""
The height of the top of Tracy's table, in metres.
"""

RELEASE = PlacingTargets.release_height
"""
How far above the surface below an object is let go.
"""

OUT_OF_REACH = Pose(1.5, 0.1, 0.0, 0, 0, 0, 1)
"""
A marker on the floor beyond Tracy's table, which no arm reaches.
"""

# %% fixtures


@pytest.fixture(autouse=True)
def cube_library(monkeypatch) -> None:
    """
    Offer the cube library the plans' cubes come from.
    """
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))


@pytest.fixture
def tower() -> PlanDocument:
    """
    :return: The plan of the red, yellow and blue tower in the centre zone.
    """
    return PlanDocument.from_json(json.loads(TRACY_TOWER.read_text("utf-8")))


def with_steps(plan: PlanDocument, steps: list[PlanStep]) -> PlanDocument:
    """
    :return: The plan with its robot taking the steps.
    """
    [robot] = plan.robots
    return replace(plan, robots=[replace(robot, steps=steps)])


def grounding(plan: PlanDocument, zones: list[Zone]) -> PlanGrounding:
    """
    :return: A new grounding of the plan on the twin, before any step is grounded.
    """
    document = DemoDocument(plan=plan, zones=zones)
    demo = PlannedTaskDemo.of_document(document, port=0)
    context = demo.build_context(demo.build_world())
    return PlanGrounding(
        context=context,
        bodies=dict(demo.bodies),
        actions=demo.discovered_actions,
        perception=WorldPerception(),
        site=RobotSite.of_demo(document),
    )


def grounded(plan: PlanDocument, zones: list[Zone]) -> list[GroundedStep]:
    """
    :return: How each step of the plan was grounded on the twin.
    """
    return grounding(plan, zones).ground(plan.performing_robot().steps)


def resolved_position(step: GroundedStep) -> list[float]:
    """
    :return: The position the step's destination resolved to.
    """
    destination = Destination.from_json(step.resolved_step().values["target_location"])
    assert destination.placement is PlacementMode.POSE
    return destination.pose.to_json()[:3]


# %% zones and towers


def test_a_tower_is_stacked_on_the_cube_placed_into_the_zone_before(tower):
    steps = grounded(tower, [CENTRE])

    assert all(step.succeeded for step in steps), [
        step.to_step_validation() for step in steps
    ]
    red, yellow, blue = (resolved_position(steps[index]) for index in (2, 4, 6))
    assert red == pytest.approx(
        [0.7, 0.0, TABLE_TOP + CUBE_SIZE / 2 + RELEASE], abs=1e-3
    )
    assert yellow == pytest.approx([*red[:2], red[2] + CUBE_SIZE + RELEASE], abs=1e-3)
    assert blue == pytest.approx(
        [*red[:2], red[2] + 2 * (CUBE_SIZE + RELEASE)], abs=1e-3
    )


def test_a_step_expects_the_cube_it_picks_or_stacks_onto_where_it_was_grounded(
    tower,
):
    steps = {step.step.identifier: step for step in grounded(tower, [CENTRE])}

    [red] = [item for item in tower.objects if item.identifier == "red_cube"]
    assert steps["step_2"].expected_positions["red_cube"] == pytest.approx(
        red.pose.to_json()[:3], abs=1e-3
    )
    assert steps["step_3"].expected_positions == {}
    assert list(steps["step_5"].expected_positions) == ["red_cube"]
    assert steps["step_5"].expected_positions["red_cube"][:2] == pytest.approx(
        resolved_position(steps["step_3"])[:2], abs=1e-3
    )


def test_a_zone_holding_a_cube_at_its_centre_takes_the_next_one_beside_it(tower):
    [park, pick_red, place_red, pick_yellow, *_] = tower.performing_robot().steps
    place_yellow = replace(
        place_red,
        identifier="step_5",
        values={
            "object_designator": "yellow_cube",
            "target_location": place_red.values["target_location"],
        },
    )
    steps = grounded(
        with_steps(tower, [park, pick_red, place_red, pick_yellow, place_yellow]),
        [CENTRE],
    )

    assert all(step.succeeded for step in steps), [
        step.to_step_validation() for step in steps
    ]
    red, yellow = resolved_position(steps[2]), resolved_position(steps[4])
    assert abs(yellow[0] - red[0]) + abs(yellow[1] - red[1]) >= CUBE_SIZE
    assert CENTRE.area.min_x <= yellow[0] <= CENTRE.area.max_x
    assert CENTRE.area.min_y <= yellow[1] <= CENTRE.area.max_y
    assert yellow[2] == pytest.approx(red[2], abs=1e-3)


def test_a_zone_leaves_its_cube_room_for_the_gripper_to_let_go_beside_another(tower):
    """
    Tracy holds a cube from above, its fingers closing along the table's x axis: opened
    to let go, they reach about 5 cm past the cube, so the spot beside the cube at the
    zone's centre along x would open them into it, and the spot beside it along y not.
    """
    [park, pick_red, place_red, pick_yellow, *_] = tower.performing_robot().steps
    plan = with_steps(tower, [park, pick_red, place_red, pick_yellow])
    planned = grounding(plan, [CENTRE])
    assert all(step.succeeded for step in planned.ground(plan.performing_robot().steps))
    context = planned.copied_context
    placing = PlacingTargets(
        world=context.world,
        robot=context.robot,
        site=RobotSite.of_demo(DemoDocument(plan=plan, zones=[CENTRE])),
        bodies=planned.copied_bodies,
        object_identifier="yellow_cube",
    )
    red = planned.copied_bodies["red_cube"].global_pose.to_np()[:3, 3]
    beside = CUBE_SIZE + 2 * placing.clearance

    assert not placing.is_free(placing.resting(red[0] - beside, red[1], TABLE_TOP))
    assert placing.is_free(placing.resting(red[0], red[1] - beside, TABLE_TOP))


def test_a_zone_the_profile_does_not_name_fails_its_step(tower):
    steps = grounded(tower, [])

    assert steps[2].check.verdict.name == "FAILED"
    assert steps[2].to_step_validation().verdict is StepVerdict.FAILED
    assert isinstance(steps[2].check.failure, UnknownZone)
    assert "center" in steps[2].to_step_validation().detail


def test_a_cube_is_put_on_top_of_the_one_a_detection_found(tower):
    [park, _, _, pick_yellow, place_yellow, *_] = tower.performing_robot().steps
    detect = PlanStep(
        identifier="step_2",
        action="detect",
        values={
            "technique": "types",
            "object_sem_annotation": "block",
            "region": {"kind": "table"},
            "color": "red",
        },
    )
    on_detected = replace(
        place_yellow,
        values={
            **place_yellow.values,
            "target_location": Destination(
                placement=PlacementMode.ON_TOP_OF, object="detected:step_2"
            ).to_json(),
        },
    )
    steps = grounded(
        with_steps(tower, [park, detect, pick_yellow, on_detected]), [CENTRE]
    )

    assert all(step.succeeded for step in steps)
    assert resolved_position(steps[3]) == pytest.approx(
        [0.55, 0.10, TABLE_TOP + 1.5 * CUBE_SIZE + RELEASE], abs=1e-3
    )


# %% markers


def test_a_marker_no_arm_taking_the_cube_reaches_fails_its_pick_as_unreachable(tower):
    [park, pick_red, place_red, *_] = tower.performing_robot().steps
    at_marker = replace(
        place_red,
        values={
            **place_red.values,
            "target_location": Destination(
                placement=PlacementMode.MARKER, pose=OUT_OF_REACH
            ).to_json(),
        },
    )

    report = PlanValidator(
        ValidationOrder(
            demo=DemoDocument(plan=with_steps(tower, [park, pick_red, at_marker]))
        )
    ).validate()

    pick = report.steps[1]
    assert pick.verdict is StepVerdict.FAILED
    assert pick.reason is StepFailureReason.UNREACHABLE
    assert f"at ({OUT_OF_REACH.x:.2f}, {OUT_OF_REACH.y:.2f})" in pick.detail
    assert report.steps[2].verdict is StepVerdict.UNCHECKED


def test_tracys_reach_holds_its_table_and_leaves_out_the_floor_beyond_it():
    reach = RobotReachMeasure(ReachRequest(robot="tracy")).measure()

    assert [arm.arm for arm in reach.arms] == ["left", "right"]
    assert reach.table.height == pytest.approx(TABLE_TOP, abs=1e-3)
    assert reach.reaches(Vector3(0.7, 0.0, TABLE_TOP + CUBE_SIZE / 2))
    assert reach.surface_height(OUT_OF_REACH.x, OUT_OF_REACH.y) == 0.0
    assert not reach.reaches(Vector3(OUT_OF_REACH.x, OUT_OF_REACH.y, CUBE_SIZE / 2))


# %% regions


def test_a_zone_and_a_markers_square_are_regions_above_the_table(tower):
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=tower, zones=[CENTRE]), port=0)
    context = demo.build_context(demo.build_world())
    boxes = RegionBoxes(context.robot, site=demo.site)

    zone = boxes.box(DetectionRegion(kind=RegionKind.ZONE, zone="center"))
    marker = boxes.box(
        DetectionRegion(
            kind=RegionKind.MARKER, pose=Pose(0.5, 0.2, TABLE_TOP, 0, 0, 0, 1)
        )
    )

    assert [zone.min_x, zone.max_x, zone.min_y, zone.max_y] == pytest.approx(
        [0.6, 0.8, -0.1, 0.1]
    )
    assert zone.min_z == pytest.approx(TABLE_TOP, abs=1e-3)
    assert [marker.min_x, marker.max_x, marker.min_z] == pytest.approx(
        [0.4, 0.6, TABLE_TOP]
    )
