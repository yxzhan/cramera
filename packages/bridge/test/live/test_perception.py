"""
Perception steps in the published and recorded plan: what a step was asked and which
objects it reported once it ended.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import List, Optional

from coraplex.datastructures.enums import DetectionTechnique
from coraplex.robot_plans.actions.core.misc import DetectAction
from cramera_bridge.live.bundles import PlanTrees
from cramera_bridge.live.plan import PlanNodeStatus
from cramera_bridge.performing import TaskContext
from cramera_bridge.visualization import PublishedPlanProgress
from cramph.state_history import StateHistoryItem
from cramph.data_types import LifeCycleValues
from semantic_digital_twin.semantic_annotations.semantic_annotations import Handle
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

from ..dataset.live_worlds import SmallPlan


@dataclass
class SeenQuery:
    """
    Stands in for a perception query: it answers the bodies the robot's camera sees.
    """

    seen: List[Body]
    """
    The bodies seen.
    """

    world: Optional[World] = None
    """
    The world asked about.
    """

    robot: Optional[object] = None
    """
    The robot that perceives, None for a step whose images are not taken.
    """

    def from_world(self) -> List[Body]:
        """
        :return: The bodies seen.
        """
        return self.seen


def detecting(context: TaskContext, query: SeenQuery) -> SmallPlan:
    """
    :param context: The world and the robot that detects.
    :param query: Stands in for the query of the plan's perception step.
    :return: A plan of a root, a detect action looking for handles and the perception
        step it expanded into, answering the stand-in query.
    """
    plan = SmallPlan.prepare(
        DetectAction(technique=DetectionTechnique.TYPES, object_sem_annotation=Handle),
        context,
    )
    plan.motion.query = query
    return plan


@pytest.fixture
def detection(kitchen, robot_world) -> SmallPlan:
    """
    :return: A plan detecting handles, whose perception step sees the milk.
    """
    return detecting(
        TaskContext(robot_world.world, robot_world.robot), SeenQuery([kitchen.milk])
    )


def nodes(publisher):
    """
    :return: The published plan's root, action and perception step.
    """
    return publisher.current_plan().nodes


def ended(publisher, detection: SmallPlan, life_cycle: LifeCycleValues) -> None:
    """
    Let the perception step of a published plan end, and publish the plan again as the
    tick it ended in does.

    :param life_cycle: How it ended.
    """
    detection.set(detection.motion, life_cycle)
    publisher.observe_node_ended(detection.motion)
    publisher.refresh_plan()


def test_a_perception_step_is_marked_and_names_the_class_it_was_asked_for(
    publisher, detection
):
    publisher.begin_plan(detection.executor)
    root, action, step = nodes(publisher)
    assert action.parameters["object_sem_annotation"] == Handle.__name__
    assert (root.perceived, action.perceived, step.perceived) == (None, None, [])


def test_a_detect_action_asks_its_step_for_the_class(robot_world):
    plan = SmallPlan.prepare(
        DetectAction(technique=DetectionTechnique.TYPES, object_sem_annotation=Handle),
        TaskContext(robot_world.world, robot_world.robot),
    )
    assert plan.motion.query.semantic_annotation is Handle


def test_a_perception_step_that_ended_reports_what_it_saw(
    publisher, detection, kitchen
):
    publisher.begin_plan(detection.executor)
    ended(publisher, detection, LifeCycleValues.SUCCEEDED)
    *_, step = nodes(publisher)
    assert step.perceived == [str(kitchen.milk.id)]
    assert step.reason is None


def test_a_failed_perception_step_reports_nothing(publisher, detection):
    publisher.begin_plan(detection.executor)
    ended(publisher, detection, LifeCycleValues.FAILED)
    *_, step = nodes(publisher)
    assert step.perceived == []


def test_the_recording_keeps_what_a_perception_step_saw(publisher, detection, kitchen):
    publisher.begin_plan(detection.executor)
    ended(publisher, detection, LifeCycleValues.SUCCEEDED)
    (root,) = PlanTrees(plan=publisher.current_plan(), robot=None).roots()
    (action,) = root.children
    (step,) = action.children
    assert step.perceived == [str(kitchen.milk.id)]
    assert action.perceived is None


def reported_by_the_motion_server(detection: SmallPlan, life_cycle: LifeCycleValues):
    """
    Let the perception step end as a motion server reports it on the real robot: its
    state is recorded in the plan's history, and no executor of this process ticks.

    :param life_cycle: How it ended.
    """
    detection.set(detection.motion, life_cycle)
    statechart = detection.statechart
    statechart.state_history.append(
        next_item=StateHistoryItem(
            tick_count=len(statechart.state_history.items) + 1,
            life_cycle_state=statechart.life_cycle_state,
            observation_state=statechart.observation_state,
        )
    )


def test_a_perception_step_the_motion_server_ran_reports_what_it_saw(
    publisher, detection, kitchen
):
    progress = PublishedPlanProgress(publisher=publisher)
    progress.after_compile(detection.executor)
    reported_by_the_motion_server(detection, LifeCycleValues.SUCCEEDED)
    *_, step = nodes(publisher)
    assert step.perceived == [str(kitchen.milk.id)]
    assert step.status == PlanNodeStatus.SUCCEEDED
