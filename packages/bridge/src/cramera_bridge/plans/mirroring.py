"""
Mirroring where the real robot stands into the plan builder, before and between runs:
the joint positions its arms' drivers publish are written out at a steady rate. Nothing
is commanded; the drivers' topics are only read.

Run as ``python -m cramera_bridge.plans.mirroring``, it writes a
:class:`~cramera_protocol.plan_builder.RobotPosture` as one line of JSON to its standard
output at every interval, with how long ago each arm's driver reported (none yet
before it did), and ends when its standard input
closes, as the plan builder that started it ends.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import partial

from rclpy.node import Node
from sensor_msgs.msg import JointState
from typing_extensions import Callable, Dict, Sequence, TextIO

from cramera.lifeline import Lifeline
from cramera_bridge.real_robot import RealRobotPreflight
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.plan_builder import ArmReport, RobotPosture

# %% mirror


@dataclass
class PostureMirror:
    """
    Keeps the latest joint positions the arms' drivers report and writes them out at a
    steady rate.
    """

    joint_state_topics: Sequence[str] = RealRobotPreflight.joint_state_topics
    """
    The topics the drivers publish the joint positions on.
    """

    interval: float = 0.1
    """
    How long, in seconds, lies between two postures written.
    """

    node_name: str = "cramera_posture_mirror"
    """
    The name of the node the joint positions are read through.
    """

    clock: Callable[[], float] = time.monotonic
    """
    Tells the time each report arrives at and each posture is written at, in seconds.
    """

    positions: Dict[str, float] = field(default_factory=dict, init=False)
    """
    The latest position of every joint reported so far, by its name.
    """

    received: Dict[str, float] = field(default_factory=dict, init=False)
    """
    When each topic last brought joint positions, by the topic, as :attr:`clock` tells.
    """

    lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    """
    Guards :attr:`positions` and :attr:`received` between the node's thread and the
    writing one.
    """

    def keep(self, topic: str, message: JointState) -> None:
        """
        :param topic: The topic the driver reported on.
        :param message: Joint positions a driver reported.
        """
        with self.lock:
            self.positions.update(zip(message.name, message.position))
            self.received[topic] = self.clock()

    def subscribe(self, node: Node) -> None:
        """
        :param node: A spinning node that receives the drivers' joint positions for
            :meth:`keep`.
        """
        for topic in self.joint_state_topics:
            node.create_subscription(JointState, topic, partial(self.keep, topic), 1)

    def posture(self) -> RobotPosture:
        """
        :return: The latest joint positions reported, and how long ago each arm's
            driver reported them.
        """
        now = self.clock()
        with self.lock:
            return RobotPosture(
                joint_positions=dict(self.positions),
                arms=[
                    ArmReport(
                        arm=self.arm_of(topic),
                        age_seconds=(
                            now - self.received[topic]
                            if topic in self.received
                            else None
                        ),
                    )
                    for topic in self.joint_state_topics
                ],
            )

    @staticmethod
    def arm_of(topic: str) -> str:
        """
        :param topic: An arm's joint-state topic, such as ``left_arm/joint_states``.
        :return: The arm's name, the namespace of the topic, such as ``left_arm``.
        """
        return topic.strip("/").split("/")[0]

    def write(self, output: TextIO, stopped: threading.Event) -> None:
        """
        Write the latest posture to `output` at every interval, also before any driver
        reported, so that the arms' silence shows; until `stopped` is set.

        :param output: Where each posture goes, one line of JSON each.
        :param stopped: Ends the writing.
        """
        while not stopped.wait(self.interval):
            output.write(json.dumps(self.posture().to_json()) + "\n")
            output.flush()

    def run(self) -> None:
        """
        Mirror the drivers' joint positions to the standard output until the standard
        input closes.
        """
        stopped = threading.Event()
        Lifeline(sys.stdin.fileno(), end=stopped.set).watch()
        with SpinningNode(self.node_name).running() as node:
            self.subscribe(node)
            self.write(sys.stdout, stopped)


if __name__ == "__main__":
    PostureMirror().run()
