"""
Robots of the live world: listing, selecting and moving their hands.
"""

from __future__ import annotations

import threading
import time

import numpy
import pytest

from cramera_bridge.exceptions import (
    TeleoperationUnavailable,
    UnknownArm,
    UnknownRobot,
)
from cramera_bridge.live.publisher import LivePublisher
from cramera_bridge.live.teleoperation import (
    MAXIMUM_STEP_METRES,
    TICKS_PER_SECOND,
    ServoedArm,
)
from cramera_bridge.robots import RobotRoster
from cramera_protocol.geometry import Vector3
from cramera_protocol.live import Capability
from cramera_protocol.live_messages import RobotSelection, TeleoperationRequest
from semantic_digital_twin.datastructures.definitions import GripperState

from ..dataset.worlds import RobotWorld

# %% fixtures


@pytest.fixture
def fresh_robot() -> RobotWorld:
    """
    :return: A PR2 world the test may move.
    """
    return RobotWorld.build()


@pytest.fixture
def robot_publisher(fresh_robot, locations) -> LivePublisher:
    """
    :return: A publisher attached to the PR2 world.
    """
    publisher = LivePublisher(locations=locations)
    publisher.attach(fresh_robot.world)
    return publisher


# %% robots


def test_the_robot_is_listed_and_selected(robot_publisher, fresh_robot):
    roster = robot_publisher.robots()
    identifier = RobotRoster.identifier(fresh_robot.robot)
    assert [robot.identifier for robot in roster.robots] == [identifier]
    assert roster.active_identifier == identifier
    assert robot_publisher.current_state().base is not None
    assert Capability.TELEOPERATION in robot_publisher.info().capabilities
    assert Capability.ROBOT_SELECTION not in robot_publisher.info().capabilities


def test_selecting_the_selected_robot_changes_nothing(robot_publisher, fresh_robot):
    identifier = RobotRoster.identifier(fresh_robot.robot)
    roster = robot_publisher.select_robot(RobotSelection(identifier=identifier))
    assert roster.active_identifier == identifier


def test_an_unknown_robot_cannot_be_selected(robot_publisher):
    with pytest.raises(UnknownRobot):
        robot_publisher.select_robot(RobotSelection(identifier="nobody"))


# %% hands


def left_arm_name(fresh_robot: RobotWorld) -> str:
    """
    :return: The name of the PR2's left arm annotation.
    """
    return str(fresh_robot.robot.get_left_arm_if_specified().name)


def test_a_hand_moves_one_bounded_step_towards_its_target(fresh_robot):
    arm = ServoedArm(fresh_robot.world, fresh_robot.robot.get_left_arm_if_specified())
    start = numpy.asarray(arm.tool_pose().position)
    request = TeleoperationRequest(
        arm=left_arm_name(fresh_robot), position=Vector3(-1.0, 0.0, 0.0)
    )
    assert arm.step_towards(request) is True
    fresh_robot.world.notify_state_change()
    moved = numpy.linalg.norm(numpy.asarray(arm.tool_pose().position) - start)
    assert 0 < moved <= MAXIMUM_STEP_METRES + 1e-3


def test_an_unreachable_step_leaves_the_arm(fresh_robot):
    arm = ServoedArm(fresh_robot.world, fresh_robot.robot.get_left_arm_if_specified())
    start = arm.tool_pose()
    request = TeleoperationRequest(
        arm=left_arm_name(fresh_robot), position=Vector3(1.0, 0.0, 0.0)
    )
    assert arm.step_towards(request) is False
    assert arm.tool_pose() == start


def test_the_gripper_opens_between_its_joint_states(fresh_robot):
    arm = ServoedArm(fresh_robot.world, fresh_robot.robot.get_left_arm_if_specified())
    end_effector = arm.arm.end_effector
    opened = end_effector.get_joint_state_by_type(GripperState.OPEN)
    assert arm.open_gripper(1.0) is True
    assert [connection.position for connection in opened.connections] == pytest.approx(
        opened.target_values
    )


def test_the_driver_thread_is_owned_and_joined(fresh_robot, robot_publisher):
    request = TeleoperationRequest(
        arm=left_arm_name(fresh_robot), position=Vector3(0.0, 0.0, 0.5), gripper=0.5
    )
    robot_publisher.teleoperate(request)
    driver = robot_publisher.teleoperation
    thread = driver.thread
    assert driver.servo() is True
    robot_publisher.stop_teleoperation()
    assert not thread.is_alive()
    assert driver.thread is None


def test_hand_control_needs_a_known_arm_and_an_idle_world(fresh_robot, robot_publisher):
    with pytest.raises(UnknownArm):
        robot_publisher.teleoperate(
            TeleoperationRequest(arm="tail", position=Vector3(0, 0, 0))
        )
    robot_publisher.activity.tick()
    with pytest.raises(TeleoperationUnavailable):
        robot_publisher.teleoperate(
            TeleoperationRequest(
                arm=left_arm_name(fresh_robot), position=Vector3(0, 0, 0)
            )
        )


def test_hand_control_needs_a_robot(publisher):
    with pytest.raises(TeleoperationUnavailable):
        publisher.teleoperate(
            TeleoperationRequest(arm="any", position=Vector3(0, 0, 0))
        )


# %% hand control alongside other threads

WAITED_TICKS = 5
"""
How many driver periods a test waits for the driver to act.
"""


def left_arm_positions(fresh_robot: RobotWorld) -> list:
    """
    :return: The joint values of the PR2's left arm.
    """
    arm = fresh_robot.robot.get_left_arm_if_specified()
    return [
        connection.position
        for connection in fresh_robot.world.connections
        if connection.child in arm.bodies and connection.dofs
    ]


def wait_ticks(count: int = WAITED_TICKS) -> None:
    """
    Let the driver thread run for a number of its periods.
    """
    time.sleep(count / TICKS_PER_SECOND)


def test_a_hand_at_its_target_writes_nothing(fresh_robot):
    arm = ServoedArm(fresh_robot.world, fresh_robot.robot.get_left_arm_if_specified())
    request = TeleoperationRequest(
        arm=left_arm_name(fresh_robot), position=Vector3(0.0, 0.0, 0.0)
    )
    assert arm.step_towards(request) is False


def test_an_open_gripper_is_not_written_again(fresh_robot):
    arm = ServoedArm(fresh_robot.world, fresh_robot.robot.get_left_arm_if_specified())
    arm.open_gripper(1.0)
    assert arm.open_gripper(1.0) is False


def test_the_driver_waits_while_another_thread_holds_the_world(
    fresh_robot, robot_publisher
):
    robot_publisher.teleoperate(
        TeleoperationRequest(
            arm=left_arm_name(fresh_robot), position=Vector3(-1.0, 0.0, 0.0)
        )
    )
    holding, released = threading.Event(), threading.Event()
    held = []

    def hold_world() -> None:
        with robot_publisher.world_lock:
            held.append(left_arm_positions(fresh_robot))
            holding.set()
            released.wait()
            held.append(left_arm_positions(fresh_robot))

    holder = threading.Thread(target=hold_world)
    holder.start()
    holding.wait()
    wait_ticks()
    released.set()
    holder.join()
    held_before, held_after = held
    wait_ticks()
    released_after = left_arm_positions(fresh_robot)
    robot_publisher.stop_teleoperation()
    assert held_after == held_before
    assert released_after != held_before


def test_stopping_while_holding_the_world_does_not_wait_for_the_driver(
    fresh_robot, robot_publisher
):
    robot_publisher.teleoperate(
        TeleoperationRequest(
            arm=left_arm_name(fresh_robot), position=Vector3(-1.0, 0.0, 0.0)
        )
    )
    thread = robot_publisher.teleoperation.thread
    with robot_publisher.world_lock:
        robot_publisher.stop_teleoperation()
    assert not thread.is_alive()


# %% locking


def test_listing_the_robots_while_the_world_reports_a_change_does_not_deadlock(
    fresh_robot, robot_publisher
):
    """
    A physics simulation writes the world's state and reports the change while it
    holds the world's lock; a request listing the robots at that moment reads the same
    state, and both have to finish.
    """
    simulating, listing = threading.Event(), threading.Event()

    def report_a_change() -> None:
        with fresh_robot.world.state.world_lock:
            simulating.set()
            listing.wait()
            time.sleep(0.2)
            robot_publisher.observe_state_change()

    def list_robots() -> None:
        simulating.wait()
        listing.set()
        robot_publisher.robots()

    threads = [
        threading.Thread(target=report_a_change, daemon=True),
        threading.Thread(target=list_robots, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    assert [thread.is_alive() for thread in threads] == [False, False]
