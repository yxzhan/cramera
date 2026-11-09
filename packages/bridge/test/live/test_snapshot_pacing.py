"""
Taking state snapshots only once the state is settled, and not more often than the
viewer can use.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cramera_bridge.live.publisher import LivePublisher
from cramera_bridge.live.snapshot_pacing import FRAME_INTERVAL, SnapshotPacing
from cramera_bridge.questions.workspace_classes import WorkspaceClassIndex
from cramera_bridge.visualization import PublishedStateChange
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix

PAST_AN_INTERVAL = 1.5 * FRAME_INTERVAL
"""
A step of the clock that clearly passes the interval between snapshots.
"""


@dataclass
class ManualClock:
    """
    A clock that only moves when a test moves it.
    """

    now: float = 100.0
    """
    The current time, in seconds.
    """

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = PAST_AN_INTERVAL) -> None:
        """
        :param seconds: How far to move the clock.
        """
        self.now += seconds


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def paced(kitchen, locations, clock):
    """
    :return: A publisher attached to the kitchen world, told of its state changes and
        spacing its snapshots by the manual clock.
    """
    publisher = LivePublisher(
        locations=locations, class_index=WorkspaceClassIndex(), clock=clock
    )
    publisher.attach(kitchen.world)
    callback = PublishedStateChange(_world=kitchen.world, publisher=publisher)
    yield publisher
    callback.stop()


def move_milk(kitchen, x: float) -> None:
    """
    Put the milk at another place along the table, telling the world.
    """
    kitchen.milk_connection.origin = HomogeneousTransformationMatrix.from_xyz_rpy(
        x, 0.1, 0.9, reference_frame=kitchen.root
    )
    kitchen.world.notify_state_change()


def recorded_milk_x(publisher, kitchen) -> float:
    """
    :return: Where the last recorded frame has the milk along the table.
    """
    return publisher.recording.frames[-1].objects[str(kitchen.milk.id)].x


# %% pacing


def test_the_first_change_is_due_and_the_next_only_after_the_interval(clock):
    pacing = SnapshotPacing(clock=clock)
    assert pacing.state_changed()
    pacing.taken()
    assert not pacing.state_changed()
    clock.advance()
    assert pacing.state_changed()


def test_nothing_is_due_without_a_change(clock):
    pacing = SnapshotPacing(clock=clock)
    pacing.taken()
    clock.advance()
    assert not pacing.is_due()


def test_changes_while_a_plan_performs_wait_for_its_ticks_and_its_end(clock):
    pacing = SnapshotPacing(clock=clock)
    executor = object()
    pacing.plan_started(executor)
    assert not pacing.state_changed()
    assert pacing.is_due()
    assert pacing.plan_ended(executor)
    assert not pacing.is_performing()


# %% the publisher


def test_a_burst_of_changes_outside_a_plan_records_one_frame_and_the_last_at_the_end(
    paced, kitchen, clock
):
    first = paced.recording.frame_count()
    clock.advance()
    for step in range(10):
        move_milk(kitchen, 1.0 + 0.01 * step)
    assert paced.recording.frame_count() == first + 1
    paced.finish_recording()
    assert paced.recording.frame_count() == first + 2
    assert recorded_milk_x(paced, kitchen) == pytest.approx(1.09)


def test_changes_outside_a_plan_are_recorded_once_per_interval(paced, kitchen, clock):
    first = paced.recording.frame_count()
    for step in range(3):
        clock.advance()
        move_milk(kitchen, 1.0 + 0.1 * step)
    assert paced.recording.frame_count() == first + 3


def test_a_state_a_plan_checks_and_restores_is_never_recorded(
    paced, kitchen, small_plan, clock
):
    paced.begin_plan(small_plan.executor)
    first = paced.recording.frame_count()
    clock.advance()
    with kitchen.world.reset_state_context():
        move_milk(kitchen, 2.0)
    assert paced.recording.frame_count() == first
    paced.observe_tick(small_plan.statechart, False)
    assert paced.recording.frame_count() == first + 1
    assert recorded_milk_x(paced, kitchen) == pytest.approx(1.0)


def test_a_plan_records_at_its_ticks_and_its_last_state_when_it_ends(
    paced, kitchen, small_plan, clock
):
    paced.begin_plan(small_plan.executor)
    first = paced.recording.frame_count()
    clock.advance()
    move_milk(kitchen, 1.2)
    paced.observe_tick(small_plan.statechart, False)
    move_milk(kitchen, 1.4)
    paced.observe_tick(small_plan.statechart, False)
    assert paced.recording.frame_count() == first + 1
    paced.end_plan(small_plan.executor)
    assert paced.recording.frame_count() == first + 2
    assert recorded_milk_x(paced, kitchen) == pytest.approx(1.4)
