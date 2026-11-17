"""
``python -m cramera_bridge.arm_halt``: the plan builder's own halt of the real robot's
arms once it stopped a run, independent of the run, which may have been killed before it
could halt them.

Started as the plan builder asks the run to end, it reaches the arms' controllers while
the run ends and says so (:attr:`ArmHaltLine.READY`); once its input closes, which the
plan builder does after the run and its Giskard ended, and the operating system does
when the plan builder ends, it commands every arm's velocity controller zero velocities
and ends with status 0 only when a controller took them on every arm's topic and every
arm's driver then reports every joint standing still; the controllers take their
commands best effort, so their acknowledgement alone confirms little.
"""

from __future__ import annotations

import sys
import time

from cramera_bridge.real_robot import ArmHalt, JointStateMessages, RealRobotPreflight
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.plan_builder import ArmHaltLine

DISCOVERY_TIMEOUT = 5.0
"""
How long, in seconds, the controllers may take to be discovered once the halt is due.
"""

STANDSTILL_TIMEOUT = 3.0
"""
How long, in seconds, the arms may take to report standing still once halted.
"""

STANDSTILL_TOLERANCE = 0.01
"""
The highest joint velocity, in radians per second, a joint standing still may report.
"""


def stands_still(node, timeout: float = STANDSTILL_TIMEOUT) -> bool:
    """
    :param node: A spinning node.
    :param timeout: How long, in seconds, to wait.
    :return: Whether every arm's driver reported every joint below
        :data:`STANDSTILL_TOLERANCE` within `timeout`.
    """
    topics = RealRobotPreflight.joint_state_topics
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        received = JointStateMessages(topics).receive(node, deadline - time.monotonic())
        if len(received) == len(topics) and all(
            message.velocity
            and all(
                abs(velocity) <= STANDSTILL_TOLERANCE for velocity in message.velocity
            )
            for message in received.values()
        ):
            return True
    return False


def main() -> None:
    """
    Wait until the input closes, halt the arms and end with whether they were
    commanded zero velocities and report standing still.
    """
    with (
        SpinningNode("cramera_arm_halt").running() as node,
        ArmHalt().ready(node) as halt,
    ):
        print(ArmHaltLine.READY.value, flush=True)
        sys.stdin.read()
        halted = (
            halt.discovered(DISCOVERY_TIMEOUT) and halt.command() and stands_still(node)
        )
        controllers = halt.controllers()
    if halted:
        print(ArmHaltLine.HALTED.value, flush=True)
        sys.exit(0)
    print(
        f"{ArmHaltLine.NOT_HALTED.value}; controllers by topic: {controllers}",
        flush=True,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
