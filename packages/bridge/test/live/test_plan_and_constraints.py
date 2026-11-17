"""
The plan with native statuses, and the constraints attached to its nodes.
"""

from __future__ import annotations

import json
from http import HTTPStatus

import pytest

from cramera_bridge.exceptions import (
    InvalidConstraint,
    RobotSelectionBusy,
    UnknownBody,
    UnknownPlanNode,
)
from coraplex.robot_plans.actions.composite.transporting import MoveAndPickUpAction
from coraplex.robot_plans.actions.core.navigation import NavigateAction
from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from cramera_bridge.live.constraints import ConstraintMonitorFactory
from cramera_bridge.live.plan import DesignatorSummary, PLAN_NODE_PREFIX, PlanNodeStatus
from cramera_protocol.live import LiveRoute
from cramera_protocol.live_messages import (
    ConstraintActivation,
    ConstraintMonitorType,
    ConstraintRequest,
    LivePlan,
    MonitorSpecification,
    PlanNodeReference,
    RobotSelection,
)
from coraplex.robot_plans.actions.core.robot_body import ParkArmsAction
from cramph.data_types import LifeCycleValues, ObservationStateValues
from giskardpy.motion_statechart.monitors.cartesian_monitors import InFieldOfView
from giskardpy.motion_statechart.tasks.cartesian_tasks import CartesianPose
from krrood.entity_query_language.factories import a, variable
from semantic_digital_twin.datastructures.definitions import TorsoState
from krrood_spatial_math.spatial_types import Pose
from cramera.http.messages import HttpMethod

from ..dataset.live_worlds import HeightChart
from .conftest import dataset_body, live_request

# %% plan


def test_every_node_shows_its_own_native_status(publisher, small_plan):
    small_plan.set(small_plan.root, LifeCycleValues.RUNNING)
    publisher.begin_plan(small_plan.executor)
    plan = publisher.current_plan()
    assert [node.status for node in plan.nodes] == [
        PlanNodeStatus.RUNNING,
        PlanNodeStatus.CREATED,
        PlanNodeStatus.CREATED,
    ]
    assert plan.nodes[1].parent == plan.nodes[0].identifier
    assert plan.nodes[1].label == type(small_plan.action).__name__


def test_a_parent_status_is_never_derived_from_its_children(publisher, small_plan):
    small_plan.set(small_plan.action, LifeCycleValues.SUCCEEDED)
    publisher.begin_plan(small_plan.executor)
    root = publisher.current_plan().nodes[0]
    assert root.status == PlanNodeStatus.CREATED


@pytest.mark.parametrize(
    "action_life_cycle, action_status",
    [
        (LifeCycleValues.NOT_STARTED, PlanNodeStatus.CREATED),
        (LifeCycleValues.RUNNING, PlanNodeStatus.RUNNING),
        (LifeCycleValues.PAUSED, PlanNodeStatus.PAUSE),
        (LifeCycleValues.FAILED, PlanNodeStatus.FAILED),
        (LifeCycleValues.INTERRUPTED, PlanNodeStatus.INTERRUPTED),
    ],
)
def test_an_action_shows_its_own_status_not_its_motions(
    publisher, small_plan, action_life_cycle, action_status
):
    small_plan.set(small_plan.action, action_life_cycle)
    small_plan.set(small_plan.motion, LifeCycleValues.SUCCEEDED)
    publisher.begin_plan(small_plan.executor)
    _, action, motion = publisher.current_plan().nodes
    assert (action.status, motion.status) == (action_status, PlanNodeStatus.SUCCEEDED)


def test_a_running_motion_alone_names_no_running_action(publisher, small_plan):
    small_plan.set(small_plan.motion, LifeCycleValues.RUNNING)
    publisher.begin_plan(small_plan.executor)
    assert publisher.plan.running_action() is None


def test_an_action_names_the_arm_it_is_given(robot_world):
    park = ParkArmsAction(arms=[robot_world.robot.left_arm])
    assert DesignatorSummary(park).arm() == str(robot_world.robot.left_arm.name)


def reaching_the_milk(kitchen) -> CartesianPose:
    """
    :return: A motion moving the milk to a pose above the table.
    """
    return CartesianPose(
        root_link=kitchen.root,
        tip_link=kitchen.milk,
        goal_pose=Pose.from_xyz_rpy(1.0, 0.0, 0.9, reference_frame=kitchen.root),
    )


def test_a_motion_targets_the_published_object_it_names(publisher, kitchen):
    keys = set(publisher.model.object_keys())
    summary = DesignatorSummary(reaching_the_milk(kitchen))
    assert summary.target(keys) == str(kitchen.milk.id)
    assert summary.target(set()) == str(kitchen.root.name)
    assert summary.arm() is None


def test_a_node_renders_every_parameter_it_is_described_by(publisher, small_plan):
    publisher.begin_plan(small_plan.executor)
    root, action, motion = publisher.current_plan().nodes
    assert root.parameters == {}
    assert action.parameters == {"torso_state": TorsoState.HIGH.name}
    assert motion.label == type(small_plan.motion).__name__


def test_a_motion_renders_the_fields_its_class_adds(publisher, kitchen):
    keys = set(publisher.model.object_keys())
    parameters = DesignatorSummary(reaching_the_milk(kitchen)).parameters(keys)
    assert parameters["tip_link"] == str(kitchen.milk.id)
    assert parameters["root_link"] == str(kitchen.root.name)
    assert parameters["goal_pose"] == "1.000, 0.000, 0.900"


def test_a_node_is_found_by_its_identity(publisher, small_plan):
    publisher.begin_plan(small_plan.executor)
    identifier = publisher.plan.active_plan.identifier(small_plan.action)
    assert identifier.startswith(PLAN_NODE_PREFIX)
    assert publisher.plan.published_node(identifier).identifier == identifier
    with pytest.raises(UnknownPlanNode):
        publisher.plan.published_node("plan_node_99")


def test_no_plan_has_no_nodes(publisher):
    assert publisher.current_plan() == LivePlan()
    assert publisher.plan.running_action() is None


def test_the_running_action_names_the_step(publisher, small_plan):
    small_plan.set(small_plan.action, LifeCycleValues.RUNNING)
    publisher.begin_plan(small_plan.executor)
    assert publisher.plan.running_action() == type(small_plan.action).__name__


def test_a_later_plan_keeps_its_node_identities_apart_from_the_first(
    publisher, robot_plans
):
    first, second = robot_plans
    publisher.begin_plan(first.executor)
    first_identities = {node.identifier for node in publisher.current_plan().nodes}
    publisher.begin_plan(second.executor)
    second_identities = {node.identifier for node in publisher.current_plan().nodes}
    assert len(second_identities) == len(second.nodes)
    assert first_identities.isdisjoint(second_identities)


def test_a_plan_begun_again_is_published_with_its_first_identities(
    publisher, robot_plans
):
    first, second = robot_plans
    publisher.begin_plan(first.executor)
    identities = [node.identifier for node in publisher.current_plan().nodes]
    publisher.begin_plan(second.executor)
    publisher.begin_plan(first.executor)
    assert [node.identifier for node in publisher.current_plan().nodes] == identities


def test_a_robot_cannot_be_selected_while_the_plan_runs(publisher, small_plan):
    small_plan.set(small_plan.root, LifeCycleValues.RUNNING)
    publisher.begin_plan(small_plan.executor)
    with pytest.raises(RobotSelectionBusy):
        publisher.select_robot(RobotSelection(identifier="any"))


# %% constraints


def tick(publisher, chart: HeightChart) -> None:
    """
    Tick the chart once and let the publisher observe the tick, in which no plan node
    started or ended.
    """
    chart.executor.tick()
    publisher.observe_tick(chart.chart, False)


def height_constraint(publisher, kitchen, small_plan, activation) -> ConstraintRequest:
    """
    :return: A height constraint on the milk attached to the root of the plan being
        performed.
    """
    return ConstraintRequest(
        target_plan_node=PlanNodeReference(
            identifier=publisher.plan.active_plan.identifier(small_plan.root)
        ),
        monitor=MonitorSpecification(
            monitor_type=ConstraintMonitorType.HEIGHT,
            parameters={"tip_link": kitchen.milk.name.name, "lower_limit": 0.5},
        ),
        text="keep the milk up",
        apply=activation,
    )


def test_an_attached_constraint_is_observed_on_the_next_tick(
    publisher, kitchen, small_plan
):
    publisher.begin_plan(small_plan.executor)
    publisher.attach_constraint(
        height_constraint(
            publisher, kitchen, small_plan, ConstraintActivation.IMMEDIATE
        )
    )
    chart = HeightChart.build(kitchen.world, kitchen.milk)
    tick(publisher, chart)
    constraint = publisher.refresh_plan().nodes[0].constraints[0]
    assert constraint.observation == ObservationStateValues.TRUE.name


def test_a_constraint_waits_for_the_next_motion(publisher, kitchen, small_plan):
    publisher.begin_plan(small_plan.executor)
    publisher.attach_constraint(
        height_constraint(
            publisher, kitchen, small_plan, ConstraintActivation.NEXT_ACTIVATION
        )
    )
    chart = HeightChart.build(kitchen.world, kitchen.milk)
    tick(publisher, chart)
    assert publisher.refresh_plan().nodes[0].constraints[0].observation is None
    publisher.observe_node_started(small_plan.motion)
    tick(publisher, chart)
    observed = publisher.refresh_plan().nodes[0].constraints[0]
    assert observed.observation == ObservationStateValues.TRUE.name


def test_a_constraint_sees_the_world_change(publisher, kitchen, small_plan):
    publisher.begin_plan(small_plan.executor)
    publisher.attach_constraint(
        height_constraint(
            publisher, kitchen, small_plan, ConstraintActivation.IMMEDIATE
        )
    )
    chart = HeightChart.build(kitchen.world, kitchen.milk)
    tick(publisher, chart)
    kitchen.milk_connection.origin = type(kitchen.milk_connection.origin).from_xyz_rpy(
        1.0, 0.1, 0.1, reference_frame=kitchen.root
    )
    tick(publisher, chart)
    observed = publisher.refresh_plan().nodes[0].constraints[0]
    assert observed.observation == ObservationStateValues.FALSE.name


def test_a_constraint_reaches_a_node_of_a_later_plan(publisher, kitchen, robot_plans):
    first, second = robot_plans
    publisher.begin_plan(first.executor)
    publisher.begin_plan(second.executor)
    plan = publisher.attach_constraint(
        height_constraint(publisher, kitchen, second, ConstraintActivation.IMMEDIATE)
    )
    assert [node.constraints != [] for node in plan.nodes] == [True, False, False]


@pytest.mark.parametrize(
    "monitor_type, parameters",
    [
        (ConstraintMonitorType.VECTORS_ALIGNED, {"tip_link": "milk"}),
        (
            ConstraintMonitorType.POINTING_AT,
            {"tip_link": "milk", "goal_point_body": "cup"},
        ),
        (
            ConstraintMonitorType.POINTING_AT,
            {"tip_link": "milk", "goal_point": [1, 0, 0]},
        ),
        (ConstraintMonitorType.DISTANCE, {"root_link": "map", "tip_link": "milk"}),
    ],
)
def test_every_monitor_type_is_built_and_observed(
    publisher, kitchen, small_plan, monitor_type, parameters
):
    publisher.begin_plan(small_plan.executor)
    request = ConstraintRequest(
        target_plan_node=PlanNodeReference(
            identifier=publisher.plan.active_plan.identifier(small_plan.action)
        ),
        monitor=MonitorSpecification(monitor_type=monitor_type, parameters=parameters),
        apply=ConstraintActivation.IMMEDIATE,
    )
    publisher.attach_constraint(request)
    tick(publisher, HeightChart.build(kitchen.world, kitchen.milk))
    constraint = publisher.refresh_plan().nodes[1].constraints[0]
    assert constraint.monitor == monitor_type
    assert constraint.observation in ObservationStateValues.__members__


@pytest.mark.parametrize(
    "parameters, error",
    [
        ({"tip_link": "fridge"}, UnknownBody),
        ({"tip_link": 3}, InvalidConstraint),
        ({"tip_link": "milk", "goal_normal": [1, 0]}, InvalidConstraint),
    ],
)
def test_an_unreadable_constraint_is_refused(publisher, small_plan, parameters, error):
    publisher.begin_plan(small_plan.executor)
    request = ConstraintRequest(
        target_plan_node=PlanNodeReference(
            identifier=publisher.plan.active_plan.identifier(small_plan.root)
        ),
        monitor=MonitorSpecification(
            monitor_type=ConstraintMonitorType.VECTORS_ALIGNED, parameters=parameters
        ),
    )
    with pytest.raises(error):
        publisher.attach_constraint(request)


def test_a_limit_must_be_a_number(publisher, small_plan):
    publisher.begin_plan(small_plan.executor)
    request = ConstraintRequest(
        target_plan_node=PlanNodeReference(
            identifier=publisher.plan.active_plan.identifier(small_plan.root)
        ),
        monitor=MonitorSpecification(
            monitor_type=ConstraintMonitorType.HEIGHT,
            parameters={"tip_link": "milk", "lower_limit": "low"},
        ),
    )
    with pytest.raises(InvalidConstraint):
        publisher.attach_constraint(request)


def test_keeping_in_view_watches_the_target_through_the_robots_camera(robot_world):
    request = ConstraintRequest(
        target_plan_node=PlanNodeReference(identifier="step"),
        monitor=MonitorSpecification(
            monitor_type=ConstraintMonitorType.IN_FIELD_OF_VIEW,
            parameters={"target": "base_link", "margin": 0.02},
        ),
    )

    monitor = ConstraintMonitorFactory(robot_world.world).monitor(request)

    assert isinstance(monitor, InFieldOfView)
    assert monitor.camera is robot_world.robot.get_default_camera()
    assert monitor.target.name.name == "base_link"
    assert monitor.margin == 0.02


def test_keeping_in_view_needs_a_robot(publisher, small_plan, kitchen):
    request = ConstraintRequest(
        target_plan_node=PlanNodeReference(identifier="step"),
        monitor=MonitorSpecification(
            monitor_type=ConstraintMonitorType.IN_FIELD_OF_VIEW,
            parameters={"target": kitchen.milk.name.name},
        ),
    )
    with pytest.raises(InvalidConstraint):
        ConstraintMonitorFactory(kitchen.world).monitor(request)


def test_pointing_needs_a_goal(publisher, small_plan):
    publisher.begin_plan(small_plan.executor)
    request = ConstraintRequest(
        target_plan_node=PlanNodeReference(
            identifier=publisher.plan.active_plan.identifier(small_plan.root)
        ),
        monitor=MonitorSpecification(
            monitor_type=ConstraintMonitorType.POINTING_AT,
            parameters={"tip_link": "milk"},
        ),
    )
    with pytest.raises(InvalidConstraint):
        publisher.attach_constraint(request)


def test_a_constraint_posted_by_the_plan_view_reaches_its_node(
    publisher, answerer, small_plan, kitchen
):
    publisher.begin_plan(small_plan.executor)
    answer = answerer.answer(
        live_request(
            HttpMethod.POST,
            LiveRoute.CONSTRAINT,
            dataset_body("height_constraint.json"),
        )
    )
    plan = LivePlan.from_json(json.loads(answer.body))
    assert answer.status is HTTPStatus.OK
    assert [node.constraints != [] for node in plan.nodes] == [False, True, False]


def test_a_sub_action_left_to_be_found_is_named_by_what_it_matches():
    pick_up = a(MoveAndPickUpAction)(
        navigate=a(NavigateAction)(target_location=variable(Pose, domain=[])),
        pick_up=a(PickUpAction)(),
    )
    assert DesignatorSummary.render(pick_up, set()) == (
        "MoveAndPickUpAction: navigate NavigateAction, pick_up PickUpAction"
    )
    assert DesignatorSummary.render(a(NavigateAction)(), set()) == "NavigateAction"


def test_a_value_left_open_is_named_by_its_type():
    assert DesignatorSummary.render(variable(Pose, domain=[]), set()) == "any Pose"
