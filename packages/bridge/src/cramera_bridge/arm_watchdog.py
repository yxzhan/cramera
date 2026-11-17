"""
``python -m cramera_bridge.arm_watchdog``: holds an arm of the real robot still once the
velocity command that last moved it is no longer followed by fresh ones, as when the
process commanding it died; it runs apart from the viewer and everything the viewer
starts, so it outlives them.

It only ever commands zero velocities, and only to an arm whose last command moved it
and went stale; a commander sending fresh commands is never interrupted. It says that
it runs on :attr:`ArmWatchdogTopic.HEARTBEAT`, which a run's preflight waits for.
"""

from __future__ import annotations

import signal
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from threading import Lock

from rclpy.node import Node
from rclpy.publisher import Publisher
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Empty, Float64MultiArray
from typing_extensions import Iterator, Optional, Sequence

from cramera_bridge.real_robot import STOP_SIGNALS, ArmWatchdogTopic
from cramera_bridge.ros_node import SpinningNode
from semantic_digital_twin.robots.tracy import TracyVelocityCommandTopic

# %% staleness


@dataclass
class CommandStaleness:
    """
    Whether one arm has to be halted: once its last command moved it and nothing newer
    arrived within :attr:`timeout`, until a fresh moving command arrives; zero
    velocities, whoever sends them, keep a halted arm halted.
    """

    timeout: float
    """
    How long, in seconds, a moving command stays fresh.
    """

    halt_due: Optional[float]
    """
    The :func:`time.monotonic` time the arm is to be halted at unless a command arrives,
    None while its last command held it still.
    """

    halted: bool = False
    """
    Whether the arm is halted until a fresh moving command arrives.
    """

    def receive(self, velocities: Sequence[float], now: float) -> None:
        """
        :param velocities: A command the arm's controller received.
        :param now: When it arrived.
        """
        if any(velocity != 0.0 for velocity in velocities):
            self.halted = False
            self.halt_due = now + self.timeout
        elif not self.halted:
            self.halt_due = None

    def needs_halt(self, now: float) -> bool:
        """
        :param now: The time asked about.
        :return: Whether the arm is to be commanded zero velocities now.
        """
        if self.halt_due is not None and now > self.halt_due:
            self.halted = True
        return self.halted


# %% the watchdog


@dataclass
class ArmCommandWatchdog:
    """
    Watches every arm's velocity command topic and commands an arm zero velocities
    every :attr:`check_period` while its :class:`CommandStaleness` asks for a halt.

    An arm nothing was heard of yet counts as moving from the start, so an arm left
    moving before the watchdog started is halted once :attr:`startup_wait` passed.
    """

    command_topics: Sequence[str] = tuple(TracyVelocityCommandTopic)
    """
    The topics the arms' velocity controllers take their commands on, one per arm.
    """

    joints_per_arm: int = 6
    """
    How many joints each controller commands; it refuses a command of another length.
    """

    timeout: float = 0.5
    """
    How long, in seconds, a moving command stays fresh: Giskard commands every 12.5 ms
    while it moves an arm and halts it itself after a 0.2 s stall.
    """

    startup_wait: float = 2.0
    """
    How long, in seconds after the start, a commander already moving an arm has to be
    discovered before the arm counts as left moving.
    """

    check_period: float = 0.05
    """
    How often, in seconds, the arms are checked, and a halted arm commanded again.
    """

    heartbeat_period: float = 0.5
    """
    How often, in seconds, the watchdog says that it runs.
    """

    staleness: dict[str, CommandStaleness] = field(default_factory=dict, init=False)
    """
    The staleness of every arm's commands, by its command topic.
    """

    publishers: dict[str, Publisher] = field(default_factory=dict, init=False)
    """
    The publisher of every arm's zero velocities, by its command topic.
    """

    lock: Lock = field(default_factory=Lock, init=False, repr=False)
    """
    Keeps a received command and a check from interleaving.
    """

    @contextmanager
    def watching(self, node: Node) -> Iterator[ArmCommandWatchdog]:
        """
        Watch the arms through `node` while the block runs.

        :param node: A spinning node.
        :return: This watchdog.
        """
        started = time.monotonic()
        # Best effort takes reliable and best-effort commanders alike.
        commands = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        with ExitStack() as watching:
            for topic in self.command_topics:
                self.staleness[topic] = CommandStaleness(
                    timeout=self.timeout, halt_due=started + self.startup_wait
                )
                self.publishers[topic] = node.create_publisher(
                    Float64MultiArray, topic, 10
                )
                watching.callback(node.destroy_publisher, self.publishers[topic])
                subscription = node.create_subscription(
                    Float64MultiArray,
                    topic,
                    lambda message, topic=topic: self.receive(topic, message),
                    commands,
                )
                watching.callback(node.destroy_subscription, subscription)
            heartbeat = node.create_publisher(Empty, ArmWatchdogTopic.HEARTBEAT, 10)
            watching.callback(node.destroy_publisher, heartbeat)
            for timer in (
                node.create_timer(self.check_period, self.halt_stale_arms),
                node.create_timer(
                    self.heartbeat_period, lambda: heartbeat.publish(Empty())
                ),
            ):
                watching.callback(node.destroy_timer, timer)
            yield self

    def receive(self, topic: str, message: Float64MultiArray) -> None:
        """
        :param topic: The command topic `message` arrived on.
        :param message: A command an arm's controller received.
        """
        with self.lock:
            self.staleness[topic].receive(message.data, time.monotonic())

    def halt_stale_arms(self) -> None:
        """
        Command zero velocities to every arm whose commands went stale.
        """
        with self.lock:
            now = time.monotonic()
            stale = [
                topic
                for topic, staleness in self.staleness.items()
                if staleness.needs_halt(now)
            ]
        for topic in stale:
            self.publishers[topic].publish(
                Float64MultiArray(data=[0.0] * self.joints_per_arm)
            )


def main() -> None:
    """
    Watch the arms until SIGINT or SIGTERM arrives.
    """
    signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    with (
        SpinningNode("cramera_arm_watchdog").running() as node,
        ArmCommandWatchdog().watching(node),
    ):
        print("arm watchdog: watching the arms' velocity commands", flush=True)
        signal.sigwait(STOP_SIGNALS)


if __name__ == "__main__":
    main()
