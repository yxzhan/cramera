"""
The arm watchdog: holding an arm still once the velocity commands that moved it stopped
arriving, decided without ROS, and the watchdog process against arms taking commands.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest
from typing_extensions import Iterator

pytest.importorskip("rclpy")

from cramera_bridge.arm_watchdog import ArmCommandWatchdog, CommandStaleness
from cramera_bridge.real_robot import (
    ArmWatchdogTopic,
    GiskardScript,
    PreflightCheck,
    PreflightReport,
    RealRobotPreflight,
    RequirementKind,
)
from cramera_bridge.ros_node import SpinningNode
from semantic_digital_twin.robots.tracy import TracyVelocityCommandTopic
from std_msgs.msg import Empty, Float64MultiArray

MOVING = [0.0] * 5 + [0.2]
"""
A command moving one joint of an arm.
"""

STILL = [0.0] * 6
"""
A command holding an arm still.
"""

# %% staleness


@pytest.fixture
def staleness() -> CommandStaleness:
    """
    :return: The staleness of an arm whose commands were last heard of at time 0, with
        nothing commanded before.
    """
    return CommandStaleness(timeout=0.5, halt_due=None)


def test_an_arm_moving_on_fresh_commands_is_not_halted(staleness):
    for tick in range(10):
        staleness.receive(MOVING, now=tick * 0.1)

    assert not staleness.needs_halt(now=1.3)


def test_an_arm_whose_moving_command_went_stale_is_halted(staleness):
    staleness.receive(MOVING, now=0.0)

    assert not staleness.needs_halt(now=0.5)
    assert staleness.needs_halt(now=0.51)


def test_an_arm_commanded_to_stand_still_is_never_halted(staleness):
    staleness.receive(MOVING, now=0.0)
    staleness.receive(STILL, now=0.1)

    assert not staleness.needs_halt(now=100.0)


def test_a_halted_arm_stays_halted_through_zero_velocities(staleness):
    staleness.receive(MOVING, now=0.0)
    staleness.needs_halt(now=1.0)
    staleness.receive(STILL, now=1.1)

    assert staleness.needs_halt(now=1.2)
    assert staleness.needs_halt(now=100.0)


def test_a_halted_arm_is_released_by_a_fresh_moving_command(staleness):
    staleness.receive(MOVING, now=0.0)
    staleness.needs_halt(now=1.0)
    staleness.receive(MOVING, now=1.1)

    assert not staleness.needs_halt(now=1.2)
    assert staleness.needs_halt(now=1.7)


def test_an_arm_nothing_was_heard_of_is_halted_once_its_first_deadline_passed():
    staleness = CommandStaleness(timeout=0.5, halt_due=2.0)

    assert not staleness.needs_halt(now=1.9)
    assert staleness.needs_halt(now=2.1)


def test_an_arm_heard_of_before_its_first_deadline_follows_its_commands():
    staleness = CommandStaleness(timeout=0.5, halt_due=2.0)
    staleness.receive(STILL, now=1.0)

    assert not staleness.needs_halt(now=10.0)


# %% the watchdog process


@contextmanager
def watchdog_process() -> Iterator[subprocess.Popen]:
    """
    :return: The watchdog's process, ended with SIGTERM after the block.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "cramera_bridge.arm_watchdog"],
        start_new_session=True,
    )
    yield process
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=10)


@contextmanager
def commands_received(node, topic: str) -> Iterator[list[tuple[float, list[float]]]]:
    """
    :return: Every velocity command on `topic` while the block runs, with the
        :func:`time.monotonic` time it arrived.
    """
    received: list[tuple[float, list[float]]] = []
    subscription = node.create_subscription(
        Float64MultiArray,
        topic,
        lambda message: received.append((time.monotonic(), list(message.data))),
        10,
    )
    yield received
    node.destroy_subscription(subscription)


def wait_for_heartbeat(node, timeout: float = 20.0) -> None:
    """
    Wait until the watchdog says it runs.
    """
    beats = []
    subscription = node.create_subscription(
        Empty, ArmWatchdogTopic.HEARTBEAT, beats.append, 10
    )
    deadline = time.monotonic() + timeout
    while not beats:
        assert time.monotonic() < deadline, "the watchdog never said it runs"
        time.sleep(0.05)
    node.destroy_subscription(subscription)


def test_the_watchdog_commands_zero_velocities_once_a_moving_command_went_stale():
    right = TracyVelocityCommandTopic.RIGHT_ARM.value
    left = TracyVelocityCommandTopic.LEFT_ARM.value
    with (
        SpinningNode("arm_watchdog_test").running() as node,
        watchdog_process(),
    ):
        wait_for_heartbeat(node)
        publisher = node.create_publisher(Float64MultiArray, right, 10)
        with (
            commands_received(node, right) as on_right,
            commands_received(node, left) as on_left,
        ):
            time.sleep(1.0)
            sent = time.monotonic()
            publisher.publish(Float64MultiArray(data=MOVING))
            time.sleep(1.5)

    zeros = [arrived for arrived, data in on_right if data == STILL]
    assert zeros, on_right
    assert ArmCommandWatchdog().timeout < zeros[0] - sent < 1.0
    assert all(data == STILL for _, data in on_right if _ > zeros[0])
    assert all(data == STILL for _, data in on_left)


def test_the_watchdog_leaves_an_arm_alone_while_its_commands_arrive():
    right = TracyVelocityCommandTopic.RIGHT_ARM.value
    with (
        SpinningNode("arm_watchdog_test").running() as node,
        watchdog_process(),
    ):
        wait_for_heartbeat(node)
        publisher = node.create_publisher(Float64MultiArray, right, 10)
        with commands_received(node, right) as on_right:
            time.sleep(1.0)
            commanding_from = time.monotonic()
            while time.monotonic() - commanding_from < 2.0:
                publisher.publish(Float64MultiArray(data=MOVING))
                time.sleep(0.0125)
            publisher.publish(Float64MultiArray(data=STILL))
            commanding_until = time.monotonic()
            time.sleep(0.5)

    assert STILL not in [
        data
        for arrived, data in on_right
        if commanding_from + 0.1 < arrived < commanding_until
    ]


# %% the preflight of a real run


def missing_watchdog(report: PreflightReport) -> list[PreflightCheck]:
    """
    :return: The report's missing checks of the arm watchdog.
    """
    return [
        check for check in report.missing if check.kind is RequirementKind.ARM_WATCHDOG
    ]


def test_a_real_run_is_not_ready_without_the_arm_watchdog():
    with SpinningNode("arm_watchdog_test").running() as node:
        report = RealRobotPreflight(
            giskard_script=GiskardScript.VELOCITY,
            wait_seconds=0.2,
            requires_arm_watchdog=True,
        ).check(node)

    assert missing_watchdog(report) == [
        PreflightCheck(
            RequirementKind.ARM_WATCHDOG, ArmWatchdogTopic.HEARTBEAT.value, False
        )
    ]


def test_a_real_run_finds_the_arm_watchdog_that_runs():
    with (
        SpinningNode("arm_watchdog_test").running() as node,
        watchdog_process(),
    ):
        wait_for_heartbeat(node)
        report = RealRobotPreflight(
            giskard_script=GiskardScript.VELOCITY,
            wait_seconds=0.2,
            requires_arm_watchdog=True,
        ).check(node)

    assert missing_watchdog(report) == []


def test_the_check_before_the_platform_starts_leaves_the_arm_watchdog_out():
    with SpinningNode("arm_watchdog_test").running() as node:
        report = RealRobotPreflight(
            giskard_script=GiskardScript.VELOCITY, wait_seconds=0.2
        ).check(node)

    assert missing_watchdog(report) == []


def test_a_run_refused_without_the_arm_watchdog_says_so_and_what_to_do():
    report = PreflightReport(
        [
            PreflightCheck(
                RequirementKind.GISKARD_SCRIPT, GiskardScript.VELOCITY.value, True
            ),
            PreflightCheck(
                RequirementKind.ARM_WATCHDOG, ArmWatchdogTopic.HEARTBEAT.value, False
            ),
        ]
    )

    assert report.refusal() == (
        "The real robot is not ready: the arm watchdog is not running (no heartbeat "
        "on cramera/arm_watchdog/heartbeat). No motion was started. Run ./aicor start "
        "in the platform's folder to start it again, then run the plan again."
    )
    assert report.to_text().splitlines()[-1] == (
        "The real robot is not ready: the arm watchdog is not running (no heartbeat "
        "on cramera/arm_watchdog/heartbeat)."
    )


def test_a_run_refused_for_a_missing_driver_says_to_start_it():
    report = PreflightReport(
        [
            PreflightCheck(
                RequirementKind.GRIPPER_ACTION_SERVER, "left_gripper/gripper_cmd", False
            )
        ]
    )

    assert report.refusal() == (
        "The real robot is not ready: gripper action server present: "
        "left_gripper/gripper_cmd. No motion was started. Start what is missing "
        "(docs/real-tracy.md), then run the plan again."
    )
