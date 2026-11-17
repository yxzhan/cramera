"""
The real robot a run moves: the Giskard that commands it, the preflight checking that
its drivers are up before a run starts, and reading where it stands.

Run as ``cramera-preflight`` (or ``python -m cramera_bridge.real_robot``), the preflight
prints what a real run would miss and ends with a nonzero status when anything is
missing.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from threading import Event, Lock, Thread

import psutil
from control_msgs.action import ParallelGripperCommand
from cramph.executor import Executor, ExecutorExtension
from rclpy.action import ActionClient
from rclpy.action.graph import get_action_names_and_types
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.publisher import Publisher
from sensor_msgs.msg import JointState
from std_msgs.msg import Empty, Float64MultiArray
from typing_extensions import Callable, Iterator, Mapping, Optional, Sequence

from cramera.process_sessions import ProcessSession
from cramera_bridge.exceptions import JointStatesNotReceived
from cramera_bridge.ros_node import SpinningNode
from semantic_digital_twin.robots.tracy import (
    TracyParallelGripperCommandTopic,
    TracyTopic,
    TracyVelocityCommandTopic,
)
from semantic_digital_twin.world import World

# %% Giskard


class RealRobotVariable(StrEnum):
    """
    The environment variables a deployment configures its real runs with.
    """

    GISKARD_SCRIPT = "CRAMERA_GISKARD_SCRIPT"
    """
    The :class:`GiskardScript` a real run starts Giskard with; the one commanding the
    real robot when it is not set.
    """

    GISKARD_OWNER = "CRAMERA_GISKARD_OWNER"
    """
    Set in the environment of a Giskard a real run started, to the process identity of
    that run; it tells a Giskard of this platform from a foreign one.
    """


STOP_SIGNALS = frozenset({signal.SIGINT, signal.SIGTERM})
"""
The signals that end a run on the real robot and its Giskard.
"""

GISKARD_PROGRAM = "giskardpy"
"""
How the arguments of a Giskard's process begin, as a module or a launched program.
"""


class GiskardScript(StrEnum):
    """
    The scripts of giskardpy that run a Giskard for Tracy as a ROS node, started as a
    process of their own; no launch file, so nothing else, such as RViz or an
    interactive marker sending goals, starts with them.
    """

    VELOCITY = "tracy_velocity"
    """
    A Giskard commanding the velocity controllers of the real Tracy, reading its joint
    states and its robot description (``/robot_description``) from its drivers.
    """

    STANDALONE = "tracy_standalone"
    """
    A Giskard simulating Tracy's arms instead of commanding them, needing no driver.
    """

    @property
    def module(self) -> str:
        """
        The module running the Giskard as its main module.
        """
        return f"giskardpy.middleware.ros2.scripts.iai_robots.tracy.{self.value}"

    @property
    def commands_the_real_robot(self) -> bool:
        """
        Whether the Giskard it starts moves the real robot, and reads its drivers.
        """
        return self is GiskardScript.VELOCITY

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> GiskardScript:
        """
        :param environment: The process environment, which may name the script.
        :return: The script it names, else :attr:`VELOCITY`.
        """
        return cls(
            environment.get(RealRobotVariable.GISKARD_SCRIPT, cls.VELOCITY.value)
        )


class ProcessControl(IntEnum):
    """
    The operations of Linux's ``prctl`` a started process is set up with.
    """

    SET_PARENT_DEATH_SIGNAL = 1
    """
    Have the kernel send the process a signal once the process that started it ends.
    """


@dataclass(frozen=True)
class GiskardProcess:
    """
    A Giskard run as a process group of its own, so that stopping it stops Giskard and
    nothing else; it is interrupted as well when the process that started it ends
    without stopping it, such as when killed. Its environment names the run that started
    it (:attr:`RealRobotVariable.GISKARD_OWNER`).

    Interrupted, Giskard commands the robot's controllers zero velocities and ends.
    """

    script: GiskardScript
    """
    The script Giskard is started with.
    """

    stop_timeout: float = 3.0
    """
    How long, in seconds, Giskard may take to end once interrupted before it is killed.
    """

    @property
    def is_installed(self) -> bool:
        """
        Whether this interpreter finds the script's module.
        """
        return importlib.util.find_spec(self.script.module) is not None

    @contextmanager
    def running(self) -> Iterator[subprocess.Popen]:
        """
        Run Giskard while the block runs, then end it.

        :return: Giskard's process.
        """
        with ExitStack() as running:
            process = subprocess.Popen(
                [sys.executable, "-m", self.script.module],
                start_new_session=True,
                preexec_fn=self.interruption_with_starting_process(),
                env={**os.environ, RealRobotVariable.GISKARD_OWNER: str(os.getpid())},
            )
            running.callback(self.stop, process)
            yield process

    @staticmethod
    def interruption_with_starting_process() -> Callable[[], None]:
        """
        :return: What Giskard's process runs before it starts, so that it is
            interrupted once the process starting it ends, and an interruption reaches
            it even when the starting thread blocked the stop signals, which a started
            process inherits; looked up beforehand, as the forked child must not load
            libraries while other threads ran.
        """
        process_control = ctypes.CDLL(None).prctl

        def prepare() -> None:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, STOP_SIGNALS)
            process_control(ProcessControl.SET_PARENT_DEATH_SIGNAL, signal.SIGINT)

        return prepare

    def stop(self, process: subprocess.Popen) -> None:
        """
        Interrupt Giskard's process group, killing it once :attr:`stop_timeout` passed.

        :param process: Giskard's process.
        """
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGINT)
        deadline = time.monotonic() + self.stop_timeout
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@dataclass(frozen=True)
class RunningGiskard:
    """
    A Giskard process this process sees, and the run that started it if it is one of
    this platform's.
    """

    pid: int
    """
    Its process identity, which is its session's as well when a run started it.
    """

    owner: Optional[int]
    """
    The process identity of the run that started it; None for a foreign Giskard.
    """

    @classmethod
    def all(cls) -> list[RunningGiskard]:
        """
        :return: Every Giskard process this process sees but itself; the environment of
            a process of another user cannot be read, so such a Giskard is foreign.
        """
        return [
            cls(
                pid=process.info["pid"],
                owner=cls.owner_in(process.info["environ"] or {}),
            )
            for process in psutil.process_iter(["pid", "cmdline", "environ"])
            if process.info["pid"] != os.getpid()
            and any(
                argument.startswith(GISKARD_PROGRAM)
                or f"/{GISKARD_PROGRAM}" in argument
                for argument in process.info["cmdline"] or []
            )
        ]

    @staticmethod
    def owner_in(environment: Mapping[str, str]) -> Optional[int]:
        """
        :param environment: The environment of a Giskard's process.
        :return: The run that started it, or None for a foreign Giskard.
        """
        owner = environment.get(RealRobotVariable.GISKARD_OWNER)
        return None if owner is None else int(owner)

    @property
    def is_left_behind(self) -> bool:
        """
        Whether a run of this platform started it and ended without ending it, as a
        killed run did.
        """
        return self.owner is not None and not psutil.pid_exists(self.owner)

    @classmethod
    def end_left_behind(cls) -> list[int]:
        """
        End every Giskard a run of this platform left behind, as its run would have
        (interrupted, so it commands zero velocities, and killed if it does not end);
        a foreign Giskard is never touched.

        :return: The process identities of the Giskards ended.
        """
        left_behind = [giskard.pid for giskard in cls.all() if giskard.is_left_behind]
        for pid in left_behind:
            ProcessSession(pid).end()
        return left_behind

    def to_text(self) -> str:
        """
        :return: Its process and whose it is, as a person reads it.
        """
        whose = "foreign" if self.owner is None else "started by this platform"
        return f"pid {self.pid}, {whose}"


# %% halting the arms


@dataclass
class ArmHalt:
    """
    Commands every arm's velocity controller zero velocities, as Giskard does when it
    ends; sent once Giskard ended, so the controllers hold still even when Giskard was
    killed before it could.
    """

    command_topics: Sequence[str] = tuple(TracyVelocityCommandTopic)
    """
    The topics the arms' velocity controllers take their commands on.
    """

    joints_per_arm: int = 6
    """
    How many joints each controller commands; it refuses a command of another length.
    """

    acknowledgement_timeout: float = 1.0
    """
    How long, in seconds, each controller may take to acknowledge the command.
    """

    publishers: list[Publisher] = field(default_factory=list, init=False)
    """
    The publishers of the commands while :meth:`ready`.
    """

    @contextmanager
    def ready(self, node: Node) -> Iterator[ArmHalt]:
        """
        Keep a publisher on every command topic while the block runs, so the
        controllers have discovered it once :meth:`command` is called.

        :param node: A spinning node.
        :return: This halt.
        """
        with ExitStack() as ready:
            for topic in self.command_topics:
                publisher = node.create_publisher(Float64MultiArray, topic, 10)
                ready.callback(node.destroy_publisher, publisher)
                self.publishers.append(publisher)
            ready.callback(self.publishers.clear)
            yield self

    def command(self) -> bool:
        """
        Command zero velocities on every topic and wait until they were acknowledged.

        :return: Whether a controller took the command on every topic and acknowledged
            it in time.
        """
        for publisher in self.publishers:
            publisher.publish(Float64MultiArray(data=[0.0] * self.joints_per_arm))
        acknowledged = [
            publisher.wait_for_all_acked(Duration(seconds=self.acknowledgement_timeout))
            and publisher.get_subscription_count() > 0
            for publisher in self.publishers
        ]
        return all(acknowledged)

    def controllers(self) -> dict[str, int]:
        """
        :return: How many controllers take the commands of every topic.
        """
        return {
            publisher.topic_name: publisher.get_subscription_count()
            for publisher in self.publishers
        }

    def discovered(self, timeout: float) -> bool:
        """
        :param timeout: How long, in seconds, to wait.
        :return: Whether a controller subscribed to every topic within `timeout`.
        """
        deadline = time.monotonic() + timeout
        while any(
            publisher.get_subscription_count() == 0 for publisher in self.publishers
        ):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True


@dataclass
class ArmStandstill(ExecutorExtension):
    """
    Holds every arm still while no plan runs on Giskard: commands zero velocities once
    a run ends and every :attr:`hold_period` until the next plan is compiled, so no
    controller keeps a command Giskard left behind.
    """

    halt: ArmHalt
    """
    Commands the arms zero velocities.
    """

    hold_period: float = 0.5
    """
    How often, in seconds, the arms are commanded zero velocities while no plan runs.
    """

    plan_running: bool = field(default=False, init=False)
    """
    Whether a plan was compiled whose run has not ended yet.
    """

    lock: Lock = field(default_factory=Lock, init=False, repr=False)
    """
    Keeps a command of zero velocities from being sent once a plan was compiled.
    """

    @contextmanager
    def holding(self) -> Iterator[ArmStandstill]:
        """
        Hold the arms still while no plan runs, as long as the block runs.

        :return: This standstill.
        """
        with ExitStack() as holding:
            stopped = Event()
            holder = Thread(
                target=self.hold_still, args=(stopped,), name="arm standstill"
            )
            holder.start()
            holding.callback(holder.join)
            holding.callback(stopped.set)
            yield self

    def hold_still(self, stopped: Event) -> None:
        """
        Command zero velocities every :attr:`hold_period` no plan runs, until `stopped`
        is set.

        :param stopped: Set once the holding ends.
        """
        while not stopped.wait(self.hold_period):
            with self.lock:
                if not self.plan_running:
                    self.halt.command()

    def after_compile(self, executor: Executor) -> None:
        with self.lock:
            self.plan_running = True

    def after_run(self, executor: Executor) -> None:
        with self.lock:
            self.halt.command()
            self.plan_running = False


# %% preflight


class OrbbecCameraTopic(StrEnum):
    """
    The topics the Orbbec camera's driver publishes the images RoboKudo reads on, as it
    names them by default.
    """

    COLOR = "/camera/color/image_raw"
    DEPTH = "/camera/depth/image_raw"
    COLOR_CAMERA_INFO = "/camera/color/camera_info"


class ArmWatchdogTopic(StrEnum):
    """
    The topics of the arm watchdog (:mod:`cramera_bridge.arm_watchdog`), which runs
    apart from the viewer.
    """

    HEARTBEAT = "cramera/arm_watchdog/heartbeat"
    """
    Says, every half second, that the watchdog runs.
    """


class RequirementKind(StrEnum):
    """
    What a real run needs from the robot's drivers and the ROS workspace.
    """

    GISKARD_SCRIPT = "Giskard script installed"
    """
    The script the run starts Giskard with.
    """

    NO_OTHER_GISKARD = "no other Giskard running"
    """
    Nothing serves Giskard's command action yet, as the run's own Giskard is to.
    """

    ROBOT_DESCRIPTION = "robot description published"
    """
    The topic Giskard reads the robot's model from.
    """

    JOINT_STATES = "joint states arriving"
    """
    A topic an arm's driver publishes its joint positions on.
    """

    GRIPPER_ACTION_SERVER = "gripper action server present"
    """
    The action a gripper's driver serves; without it a run waits for it forever.
    """

    CAMERA_TOPIC = "camera topic published"
    """
    A topic RoboKudo reads the camera's images from.
    """

    ARM_WATCHDOG = "arm watchdog running"
    """
    The watchdog that halts an arm whose commands stopped, outside the viewer, says
    that it runs; without it, a viewer dying mid-motion leaves the arms moving.
    """


@dataclass(frozen=True)
class PreflightCheck:
    """
    One requirement of a real run, and whether the preflight found it.
    """

    kind: RequirementKind
    """
    What is required.
    """

    name: str
    """
    The topic, action or file required.
    """

    satisfied: bool
    """
    Whether it was found.
    """

    other_giskards: tuple[RunningGiskard, ...] = ()
    """
    The Giskard processes seen while another Giskard served the command action.
    """

    def to_text(self) -> str:
        """
        :return: A line saying whether the requirement was found.
        """
        return f"{'ok' if self.satisfied else 'MISSING'}  {self.kind}: {self.name}"

    def missing_text(self) -> str:
        """
        :return: What is missing, as a person reads it.
        """
        if self.kind is RequirementKind.ARM_WATCHDOG:
            return f"the arm watchdog is not running (no heartbeat on {self.name})"
        if self.kind is not RequirementKind.NO_OTHER_GISKARD:
            return f"{self.kind}: {self.name}"
        processes = "; ".join(giskard.to_text() for giskard in self.other_giskards)
        return (
            f"another Giskard is still running ({processes})"
            if processes
            else f"another Giskard is still running, serving {self.name} from a "
            "process this platform does not see"
        )


@dataclass(frozen=True)
class PreflightReport:
    """
    What the preflight found of a real run's requirements.
    """

    checks: list[PreflightCheck]
    """
    Every requirement checked, in the order checked.
    """

    @property
    def missing(self) -> list[PreflightCheck]:
        """
        The requirements not found.
        """
        return [check for check in self.checks if not check.satisfied]

    @property
    def ready(self) -> bool:
        """
        Whether every requirement was found, so a real run can start.
        """
        return not self.missing

    def verdict(self) -> str:
        """
        :return: Whether a real run can start, with what is missing when it cannot.
        """
        if self.ready:
            return "The real robot is ready."
        missing = "; ".join(check.missing_text() for check in self.missing)
        return f"The real robot is not ready: {missing}."

    def to_text(self) -> str:
        """
        :return: A line per requirement, then whether a real run can start.
        """
        return "\n".join([*(check.to_text() for check in self.checks), self.verdict()])

    def refusal(self) -> str:
        """
        :return: Why a run was refused before anything moved and what to do, as a
            person reads it.
        """
        kinds = {check.kind for check in self.missing}
        remedies = []
        if RequirementKind.ARM_WATCHDOG in kinds:
            remedies.append(
                "Run ./aicor start in the platform's folder to start it again"
            )
        if kinds - {RequirementKind.ARM_WATCHDOG}:
            remedies.append("Start what is missing (docs/real-tracy.md)")
        return (
            f"{self.verdict()} No motion was started. {'; '.join(remedies)}, "
            "then run the plan again."
        )


@dataclass(frozen=True)
class RealRobotPreflight:
    """
    Checks that the drivers a real run needs are up, before it starts Giskard; the
    defaults are those of Tracy.
    """

    giskard_script: GiskardScript = field(
        default_factory=lambda: GiskardScript.from_environment(os.environ)
    )
    """
    The script the run starts Giskard with; one simulating the arms needs no arm driver
    and no camera.
    """

    wait_seconds: float = 15.0
    """
    How long, in seconds, the preflight waits in all for the requirements to be
    discovered; a requirement found earlier ends its own wait at once.
    """

    giskard_command_action: str = "/giskard/command"
    """
    The action Giskard is commanded through.
    """

    robot_description_topic: str = "/robot_description"
    """
    The topic Giskard reads the robot's model from.
    """

    joint_state_topics: Sequence[str] = (
        TracyTopic.LEFT_ARM_JOINT_STATES,
        TracyTopic.RIGHT_ARM_JOINT_STATES,
    )
    """
    The topics the arms' drivers publish their joint positions on.
    """

    gripper_action_servers: Sequence[str] = tuple(TracyParallelGripperCommandTopic)
    """
    The actions the grippers' drivers serve.
    """

    camera_topics: Sequence[str] = tuple(OrbbecCameraTopic)
    """
    The topics RoboKudo reads the camera's images from.
    """

    requires_arm_watchdog: bool = False
    """
    Whether the arm watchdog has to say that it runs, as before a run moving the real
    arms; the check before the platform starts, and with it the watchdog, leaves it out.
    """

    def check(self, node: Node) -> PreflightReport:
        """
        :param node: A spinning node the checks look through.
        :return: Which requirements were found; another Giskard is looked for once the
            drivers were waited for, so it had as long to be discovered.
        """
        deadline = time.monotonic() + self.wait_seconds
        checks = [
            PreflightCheck(
                RequirementKind.GISKARD_SCRIPT,
                self.giskard_script.value,
                GiskardProcess(self.giskard_script).is_installed,
            ),
            *(
                PreflightCheck(
                    RequirementKind.GRIPPER_ACTION_SERVER,
                    server,
                    self.is_gripper_action_served(node, server, deadline),
                )
                for server in self.gripper_action_servers
            ),
        ]
        if self.giskard_script.commands_the_real_robot:
            checks.extend(self.driver_checks(node, deadline))
            if self.requires_arm_watchdog:
                checks.append(
                    PreflightCheck(
                        RequirementKind.ARM_WATCHDOG,
                        ArmWatchdogTopic.HEARTBEAT.value,
                        is_heard_of(node, ArmWatchdogTopic.HEARTBEAT, deadline),
                    )
                )
        checks.append(self.other_giskard_check(node, deadline))
        return PreflightReport(checks)

    def other_giskard_check(self, node: Node, deadline: float) -> PreflightCheck:
        """
        :param node: A spinning node.
        :param deadline: The :func:`time.monotonic` time the wait ends at.
        :return: Whether Giskard's command action was no longer served by the deadline,
            as a Giskard that just ended takes a moment to leave; else the Giskard
            processes seen.
        """
        while self.giskard_command_action in dict(get_action_names_and_types(node)):
            if time.monotonic() >= deadline:
                return PreflightCheck(
                    RequirementKind.NO_OTHER_GISKARD,
                    self.giskard_command_action,
                    False,
                    tuple(RunningGiskard.all()),
                )
            time.sleep(0.1)
        return PreflightCheck(
            RequirementKind.NO_OTHER_GISKARD, self.giskard_command_action, True
        )

    def driver_checks(self, node: Node, deadline: float) -> list[PreflightCheck]:
        """
        :param node: A spinning node.
        :param deadline: The :func:`time.monotonic` time the waits end at.
        :return: Whether the robot's description, the arms' joint states and the
            camera's topics were found.
        """
        received = JointStateMessages(self.joint_state_topics).receive(
            node, seconds_left_until(deadline)
        )
        return [
            PreflightCheck(
                RequirementKind.ROBOT_DESCRIPTION,
                self.robot_description_topic,
                is_published(node, self.robot_description_topic, deadline),
            ),
            *(
                PreflightCheck(RequirementKind.JOINT_STATES, topic, topic in received)
                for topic in self.joint_state_topics
            ),
            *(
                PreflightCheck(
                    RequirementKind.CAMERA_TOPIC,
                    topic,
                    is_published(node, topic, deadline),
                )
                for topic in self.camera_topics
            ),
        ]

    @staticmethod
    def is_gripper_action_served(node: Node, server: str, deadline: float) -> bool:
        """
        :param node: A spinning node.
        :param server: The name of a gripper's parallel gripper command action.
        :param deadline: The :func:`time.monotonic` time the wait ends at.
        :return: Whether a server of it was discovered before the deadline.
        """
        client = ActionClient(node, ParallelGripperCommand, server)
        served = client.wait_for_server(timeout_sec=seconds_left_until(deadline))
        client.destroy()
        return served


def seconds_left_until(deadline: float) -> float:
    """
    :param deadline: A :func:`time.monotonic` time.
    :return: The seconds until it, none once it passed.
    """
    return max(0.0, deadline - time.monotonic())


def is_published(
    node: Node, topic: str, deadline: float, polling_seconds: float = 0.1
) -> bool:
    """
    :param node: A spinning node.
    :param topic: The topic.
    :param deadline: The :func:`time.monotonic` time the wait ends at.
    :param polling_seconds: How often, in seconds, the publishers are counted.
    :return: Whether a publisher of the topic was discovered before the deadline.
    """
    while node.count_publishers(topic) == 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(polling_seconds)
    return True


def is_heard_of(
    node: Node, topic: str, deadline: float, shortest_wait: float = 1.0
) -> bool:
    """
    :param node: A spinning node.
    :param topic: A topic of :class:`std_msgs.msg.Empty` messages.
    :param deadline: The :func:`time.monotonic` time the wait ends at.
    :param shortest_wait: How long, in seconds, the wait lasts at least, past the
        deadline if need be: twice as long as a heartbeat takes to repeat.
    :return: Whether a message arrived on the topic before the deadline, which a
        process that died does not send, though its publisher may still be listed.
    """
    heard = threading.Event()
    subscription = node.create_subscription(
        Empty, topic, lambda message: heard.set(), 1
    )
    heard.wait(max(seconds_left_until(deadline), shortest_wait))
    node.destroy_subscription(subscription)
    return heard.is_set()


# %% joint states


@dataclass(frozen=True)
class JointStateMessages:
    """
    The latest message of each of the topics the real robot's drivers publish its joint
    positions on.
    """

    topics: Sequence[str]
    """
    The topics.
    """

    def receive(self, node: Node, wait_seconds: float) -> dict[str, JointState]:
        """
        :param node: A spinning node.
        :param wait_seconds: How long, in seconds, to wait for every topic's message.
        :return: The first message of each topic that sent one in time, by topic.
        """
        received: dict[str, JointState] = {}
        complete = threading.Event()

        def keep(topic: str, message: JointState) -> None:
            received.setdefault(topic, message)
            if len(received) == len(self.topics):
                complete.set()

        subscriptions = [
            node.create_subscription(
                JointState, topic, lambda message, topic=topic: keep(topic, message), 1
            )
            for topic in self.topics
        ]
        complete.wait(wait_seconds)
        for subscription in subscriptions:
            node.destroy_subscription(subscription)
        return dict(received)


@dataclass(frozen=True)
class ReportedPosture:
    """
    Where the real robot stands, as its drivers report its joint positions.
    """

    joint_state_topics: Sequence[str] = RealRobotPreflight.joint_state_topics
    """
    The topics the drivers publish the joint positions on.
    """

    wait_seconds: float = 5.0
    """
    How long, in seconds, the joint positions are waited for.
    """

    node_name: str = "cramera_robot_posture"
    """
    The name of the node the joint positions are read through.
    """

    def apply(self, world: World) -> None:
        """
        :param world: A world holding the real robot, its joints put where the real
            ones stand.
        :raises JointStatesNotReceived: When a topic sends no joint positions in time.
        :raises WorldEntityNotFoundError: When a joint the drivers name is not in the
            world.
        """
        with SpinningNode(self.node_name).running() as node:
            received = JointStateMessages(self.joint_state_topics).receive(
                node, self.wait_seconds
            )
            silent = [
                topic for topic in self.joint_state_topics if topic not in received
            ]
            if silent:
                raise JointStatesNotReceived(silent)
        for message in received.values():
            for name, position in zip(message.name, message.position):
                world.get_connection_by_name(name).position = position
        world.notify_state_change()


# %% command


def main() -> None:
    """
    ``cramera-preflight``: print what a real run would miss of the robot's drivers,
    ending with status 1 when anything is missing; the script the run would start
    Giskard with is read from the environment.
    """
    with SpinningNode("cramera_preflight").running() as node:
        report = RealRobotPreflight().check(node)
    print(report.to_text(), flush=True)
    sys.exit(0 if report.ready else 1)


if __name__ == "__main__":
    main()
