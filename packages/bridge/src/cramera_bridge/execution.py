"""
How a plan's motions are executed: kinematically simulated, or against a physics
simulation of the world in MuJoCo.

A generated plan demo runs the same plan whichever way it is executed; only the
execution differs, as the plan builder's run mode and the robot profile choose it by an
:class:`~cramera_protocol.robot_profile.ExecutorKind`. An execution sets the world up
for its kind while the plans run, and gives each plan the executor that runs it.

Executing on the real robot is another kind of execution: it starts Giskard to command
the robot, and the run's world is the one Giskard reports, kept in step with it.
"""

from __future__ import annotations

import os
import signal
from abc import ABC, abstractmethod
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from types import FrameType, MappingProxyType

import mujoco
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from typing_extensions import Iterator, Optional, Sequence, Type

from coraplex.plans.executors import (
    KinematicallySimulatedPlanExecutor,
    PhysicallySimulatedPlanExecutor,
    PlanExecutor,
    RealRobotPlanExecutor,
)
from coraplex.robot_plans.plan_transformations import (
    DetectWithRoboKudo,
    LetContactCarryTheBodies,
    MoveStraightNearTheObject,
)
from cramera_bridge.exceptions import (
    ExecutionNotRunning,
    PerceptionEngineNotNamed,
    RealRobotNotReady,
    RealRunTerminated,
)
from cramera_bridge.real_robot import (
    ArmHalt,
    ArmStandstill,
    GiskardProcess,
    GiskardScript,
    RealRobotPreflight,
    RunningGiskard,
    STOP_SIGNALS,
)
from cramera_bridge.plans.robokudo_answers import RoboKudoAnswers
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.robot_profile import ExecutorKind
from cramph.context import StatechartContext
from cramph.executor import ExecutorExtension
from giskardpy.motion_statechart.monitors.safety_monitors import SafetyEnvelope
from robokudo.defs import PACKAGE_NAME
from robokudo.query_service import QueryService
from robokudo.utils.module_loader import ModuleLoader
from semantic_digital_twin.adapters.multi_sim import MujocoSim
from semantic_digital_twin.adapters.ros.world_fetcher import fetch_world_from_service
from semantic_digital_twin.adapters.ros.world_synchronizer import WorldSynchronizer
from semantic_digital_twin.datastructures.definitions import StaticJointState
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from semantic_digital_twin.semantic_annotations.mixins import HasGraspCandidates
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import Connection6DoF
from semantic_digital_twin.world_description.contact import ContactParameters

PERCEPTION_ENGINE_VARIABLE = "CRAMERA_PERCEPTION_ENGINE"
"""
The environment variable naming the RoboKudo analysis engine a perceiving execution
perceives with.
"""

PERCEIVING_NODE_NAME = "cramera_perception_client"
"""
The name of the node a perceiving execution's plans reach RoboKudo through.
"""

# %% starting posture


@dataclass(frozen=True)
class StartingPosture:
    """
    The posture the arms of every robot of a world start a run in.
    """

    posture: StaticJointState = StaticJointState.PARK
    """
    The posture, applied to every arm that has one.
    """

    def apply(self, world: World) -> None:
        """
        :param world: The world before the run starts, its arms put in :attr:`posture`.
        """
        for robot in world.get_semantic_annotations_by_type(AbstractRobot):
            for arm in robot.all_arms:
                if arm.has_joint_state_of_type(self.posture):
                    arm.get_joint_state_by_type(self.posture).apply_to(world)
        world.notify_state_change()


# %% execution


@dataclass
class PlanExecution(ABC):
    """
    One way of executing the motions of a task's plans.
    """

    @classmethod
    def of_kind(cls, kind: ExecutorKind) -> PlanExecution:
        """
        :param kind: How the plans are to be executed.
        :return: A new execution of that kind with its default settings.
        """
        return EXECUTIONS[kind]()

    @contextmanager
    def attached(self) -> Iterator[None]:
        """
        Reach the robot the plans move while the block runs the whole task, its world
        built inside; a simulated execution reaches nothing.
        """
        yield

    @contextmanager
    def attached_when_ready(self) -> Iterator[Optional[str]]:
        """
        Reach the robot as :meth:`attached` does, unless its check finds it not ready.

        :return: None while the robot is reached; else why the robot is not ready, as a
            person reads it, and the block must not perform anything, as nothing
            reaches the robot.
        """
        with self.attached():
            yield None

    def reported_world(self) -> Optional[World]:
        """
        :return: The world as the robot reports it while :meth:`attached`, which the
            task is performed in; None where the task builds its world itself.
        """
        return None

    def perception_answers(self) -> Optional[RoboKudoAnswers]:
        """
        :return: RoboKudo's answers to the plans' detections while plans are performed,
            where RoboKudo answers them; None where the world model does.
        """
        return None

    @contextmanager
    def running(self, world: World) -> Iterator[None]:
        """
        Set the world up for this execution while the block performs the task's plans.

        :param world: The world the plans are performed in.
        """
        yield

    @abstractmethod
    def executor(
        self,
        context: StatechartContext,
        collision_avoidance: bool,
        extensions: Sequence[ExecutorExtension],
        safety: SafetyEnvelope,
    ) -> PlanExecutor:
        """
        :param context: The context of the plan the executor runs; it serves one
            executor.
        :param collision_avoidance: Whether every motion avoids collisions with the
            environment.
        :param extensions: Added to the executor.
        :param safety: The safety constraints every motion of the plan is held to.
        :return: A new executor running one plan, inside :meth:`running`.
        """


@dataclass
class KinematicExecution(PlanExecution):
    """
    Moves the world kinematically, as fast as the controller computes the motions.
    """

    def executor(
        self,
        context: StatechartContext,
        collision_avoidance: bool,
        extensions: Sequence[ExecutorExtension],
        safety: SafetyEnvelope,
    ) -> PlanExecutor:
        return KinematicallySimulatedPlanExecutor(
            context,
            collision_avoidance=collision_avoidance,
            extensions=list(extensions),
            safety=safety,
        )


@dataclass
class PhysicalExecution(PlanExecution):
    """
    Runs the motions against MuJoCo physics, stepped from the control loop, as the
    MuJoCo runner of coraplex's Tracy demos does: the servos take every command as their
    set point, and an object is held by friction alone.

    The world is published to the viewer rather than shown in a MuJoCo window.
    """

    starting_posture: StaticJointState = StaticJointState.PARK
    """
    The posture every arm starts the run in.

    A robot's description leaves its arms where the servos cannot carry them out of,
    such as stretched through its own table.
    """

    step_size: float = 1e-3
    """
    How long one physics step lasts, in seconds.
    """

    friction_cone: mujoco.mjtCone = mujoco.mjtCone.mjCONE_ELLIPTIC
    """
    The friction cone of the contacts: the exact elliptic one, where the default pyramid
    lets a squeezed object creep between the pads.
    """

    impedance_ratio: float = 10.0
    """
    How much harder sliding is than pressing in, as MuJoCo advises for grasping with an
    elliptic cone.
    """

    no_slip_iterations: int = 10
    """
    Passes spent removing the slip soft contacts leave, so a held object stays where the
    pads hold it.
    """

    settling_duration: timedelta = field(default_factory=lambda: timedelta(seconds=0.5))
    """
    How long the objects are left to come to rest before the first plan runs.
    """

    headless: bool = True
    """
    Whether the physics runs without a MuJoCo window of its own.
    """

    simulation: Optional[MujocoSim] = field(default=None, init=False)
    """
    The running physics while plans are performed, else None.
    """

    @contextmanager
    def running(self, world: World) -> Iterator[None]:
        """
        Put the arms in their starting posture, let the free objects be held by
        friction, start the physics and let the objects settle; stop it when the block
        ends.

        :param world: The world the plans are performed in.
        """
        self.prepare(world)
        simulation = MujocoSim(
            world=world,
            headless=self.headless,
            step_size=self.step_size,
            cone=self.friction_cone,
            impratio=self.impedance_ratio,
            noslip_iterations=self.no_slip_iterations,
        )
        simulation.start_stepped_simulation()
        self.simulation = simulation
        try:
            simulation.step_simulation(self.settling_duration)
            yield
        finally:
            self.simulation = None
            simulation.stop_simulation()

    def prepare(self, world: World) -> None:
        """
        Put every arm of every robot in :attr:`starting_posture`, where it has one, and
        give every free graspable object the contact a gripper holds by friction, unless
        its geometry declares its own.

        :param world: The world before the physics starts.
        """
        StartingPosture(self.starting_posture).apply(world)
        ContactParameters.create_for_grasped_object().apply_to(
            graspable.root
            for graspable in world.get_semantic_annotations_by_type(HasGraspCandidates)
            if isinstance(graspable.root.parent_connection, Connection6DoF)
        )

    def executor(
        self,
        context: StatechartContext,
        collision_avoidance: bool,
        extensions: Sequence[ExecutorExtension],
        safety: SafetyEnvelope,
    ) -> PlanExecutor:
        """
        :raises ExecutionNotRunning: Outside :meth:`running`, where no physics runs.
        """
        if self.simulation is None:
            raise ExecutionNotRunning(ExecutorKind.PHYSICS)
        return PhysicallySimulatedPlanExecutor(
            context,
            simulation=self.simulation,
            collision_avoidance=collision_avoidance,
            extensions=list(extensions),
            safety=safety,
        )


@dataclass
class PerceivingPhysicalExecution(PhysicalExecution):
    """
    Runs the motions in physics as :class:`PhysicalExecution` does, and answers the
    plans' detections the way the real robot does: a RoboKudo analysis engine looks
    through the robot's camera, rendered from the simulated world, over RoboKudo's query
    action.
    """

    analysis_engine: str = field(
        default_factory=lambda: os.environ.get(PERCEPTION_ENGINE_VARIABLE, "")
    )
    """
    The module of RoboKudo's analysis engines that perceives, such as ``tracy_cubes``.
    """

    ros_node: Optional[Node] = field(default=None, init=False)
    """
    The node the plans reach RoboKudo through while plans are performed, else None.
    """

    answers: Optional[RoboKudoAnswers] = field(default=None, init=False)
    """
    RoboKudo's answers to the plans' detections while plans are performed, else None.
    """

    @contextmanager
    def running(self, world: World) -> Iterator[None]:
        """
        Differs from :meth:`PhysicalExecution.running` in also answering RoboKudo's
        query action from the analysis engine while the block runs.

        :raises PerceptionEngineNotNamed: When no analysis engine is named.
        """
        if not self.analysis_engine:
            raise PerceptionEngineNotNamed(PERCEPTION_ENGINE_VARIABLE)
        with super().running(world), ExitStack() as perceiving:
            node = perceiving.enter_context(
                SpinningNode(PERCEIVING_NODE_NAME).running()
            )
            service = QueryService(
                analysis_engine=ModuleLoader().load_ae(
                    ros_pkg_name=PACKAGE_NAME, module_name=self.analysis_engine
                ),
                world=world,
            )
            service.start()
            perceiving.callback(service.stop)
            self.answers = RoboKudoAnswers(node).start()
            perceiving.callback(self.answers.stop)
            perceiving.callback(setattr, self, "answers", None)
            self.ros_node = node
            perceiving.callback(setattr, self, "ros_node", None)
            yield

    def perception_answers(self) -> Optional[RoboKudoAnswers]:
        return self.answers

    def executor(
        self,
        context: StatechartContext,
        collision_avoidance: bool,
        extensions: Sequence[ExecutorExtension],
        safety: SafetyEnvelope,
    ) -> PlanExecutor:
        """
        Differs from :meth:`PhysicalExecution.executor` in answering every detection of
        the plan with RoboKudo.
        """
        if self.simulation is None or self.ros_node is None:
            raise ExecutionNotRunning(ExecutorKind.PERCEIVED_PHYSICS)
        return PhysicallySimulatedPlanExecutor(
            context,
            simulation=self.simulation,
            collision_avoidance=collision_avoidance,
            extensions=list(extensions),
            safety=safety,
            ros_node=self.ros_node,
            plan_adaptations=[
                MoveStraightNearTheObject(),
                LetContactCarryTheBodies(),
                DetectWithRoboKudo(),
            ],
        )


@dataclass
class RealExecution(PlanExecution):
    """
    Moves the real robot: Giskard, started for the task, commands its controllers and
    its grippers' drivers, the task's world is the one Giskard reports and follows the
    robot's joint positions, and RoboKudo answers the plans' detections.

    The robot starts where it stands; nothing parks it before the first plan.
    """

    giskard_script: GiskardScript = field(
        default_factory=lambda: GiskardScript.from_environment(os.environ)
    )
    """
    The script Giskard is started with.
    """

    world_fetch_timeout: float = 60.0
    """
    How long, in seconds, Giskard may take to serve its world once started; it served
    it within ten seconds in the rehearsals, and a request left unanswered is sent
    again.
    """

    node_name: str = "cramera_real_robot"
    """
    The name of the node the plans reach Giskard and RoboKudo through.
    """

    world: Optional[World] = field(default=None, init=False)
    """
    The world Giskard reports while :meth:`attached`, else None.
    """

    ros_node: Optional[Node] = field(default=None, init=False)
    """
    The node the plans reach Giskard through while :meth:`attached`, else None.
    """

    standstill: ArmStandstill | None = field(default=None, init=False)
    """
    Holds the arms still between the plans while :meth:`attached`, else None.
    """

    answers: Optional[RoboKudoAnswers] = field(default=None, init=False)
    """
    RoboKudo's answers to the plans' detections while :meth:`attached`, else None.
    """

    terminated: bool = field(default=False, init=False)
    """
    Whether SIGTERM asked the run to end; no plan is performed any more once it did,
    even when something the run called caught the :class:`RealRunTerminated`.
    """

    @contextmanager
    def attached(self) -> Iterator[None]:
        """
        Reach the robot as :meth:`attached_when_ready` does.

        :raises RealRobotNotReady: When the preflight misses a driver the run needs.
        :raises RealRunTerminated: When SIGTERM ended the block.
        """
        with self.attached_when_ready() as refusal:
            if refusal is not None:
                raise RealRobotNotReady(refusal)
            yield

    @contextmanager
    def attached_when_ready(self) -> Iterator[Optional[str]]:
        """
        End every Giskard an earlier run of this platform left behind, check the robot's
        drivers, start Giskard and keep the world it serves in step with it while the
        block runs, holding the arms still while no plan runs. When the block ends, or
        SIGTERM ends it, Giskard is interrupted, so it commands zero velocities, and
        ended; then every arm is commanded zero velocities once more, in case Giskard had
        to be killed.

        A preflight that misses a driver the run needs refuses the run before Giskard
        starts and before anything commands the arms, zero velocities included.

        :return: None while the robot is reached; the preflight's refusal otherwise.
        :raises RealRunTerminated: When SIGTERM ended the block.
        """
        with ExitStack() as attached:
            for pid in RunningGiskard.end_left_behind():
                print(f"Ended a Giskard an earlier run left behind (pid {pid}).")
            node = attached.enter_context(
                SpinningNode(self.node_name, MultiThreadedExecutor).running()
            )
            report = RealRobotPreflight(
                self.giskard_script, requires_arm_watchdog=True
            ).check(node)
            if not report.ready:
                print(report.to_text(), flush=True)
                yield report.refusal()
                return
            attached.enter_context(self.ended_by_termination())
            halt = attached.enter_context(ArmHalt().ready(node))
            attached.callback(halt.command)
            standstill = attached.enter_context(ArmStandstill(halt).holding())
            attached.enter_context(GiskardProcess(self.giskard_script).running())
            world = fetch_world_from_service(
                node=node, timeout_seconds=self.world_fetch_timeout
            )
            attached.callback(WorldSynchronizer(_world=world, node=node).close)
            self.answers = RoboKudoAnswers(node).start()
            attached.callback(self.answers.stop)
            self.world = world
            self.ros_node = node
            self.standstill = standstill
            attached.callback(self.detach)
            yield None

    @contextmanager
    def ended_by_termination(self) -> Iterator[None]:
        """
        Turn SIGTERM into :class:`RealRunTerminated` while the block runs, also in a
        process started with the stop signals blocked, which it would never receive
        otherwise; the earlier handler is restored afterwards, the signals stay
        unblocked.
        """
        with ExitStack() as ended:
            ended.callback(
                signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM)
            )
            signal.signal(signal.SIGTERM, self.terminate)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, STOP_SIGNALS)
            yield

    def terminate(self, signal_number: int, frame: Optional[FrameType]) -> None:
        """
        End a run asked to end, ignoring further requests while it halts the robot.

        :param signal_number: The signal received.
        :param frame: The frame the signal interrupted.
        :raises RealRunTerminated: Always, so the run unwinds through :meth:`attached`.
        """
        self.terminated = True
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise RealRunTerminated()

    def detach(self) -> None:
        """
        Forget the world, the node, the standstill and RoboKudo's answers once the robot
        is no longer reached.
        """
        self.world = None
        self.ros_node = None
        self.standstill = None
        self.answers = None

    def reported_world(self) -> Optional[World]:
        return self.world

    def perception_answers(self) -> Optional[RoboKudoAnswers]:
        return self.answers

    def executor(
        self,
        context: StatechartContext,
        collision_avoidance: bool,
        extensions: Sequence[ExecutorExtension],
        safety: SafetyEnvelope,
    ) -> PlanExecutor:
        """
        :raises RealRunTerminated: Once SIGTERM asked the run to end, so no further
            plan reaches Giskard, not even a retry after the ending was caught.
        :raises ExecutionNotRunning: Outside :meth:`attached`, where Giskard does not
            run.
        """
        if self.terminated:
            raise RealRunTerminated()
        if self.ros_node is None:
            raise ExecutionNotRunning(ExecutorKind.REAL)
        return RealRobotPlanExecutor(
            context,
            ros_node=self.ros_node,
            collision_avoidance=collision_avoidance,
            extensions=[*extensions, self.standstill],
            safety=safety,
        )


EXECUTIONS: MappingProxyType[ExecutorKind, Type[PlanExecution]] = MappingProxyType(
    {
        ExecutorKind.KINEMATIC: KinematicExecution,
        ExecutorKind.PHYSICS: PhysicalExecution,
        ExecutorKind.PERCEIVED_PHYSICS: PerceivingPhysicalExecution,
        ExecutorKind.REAL: RealExecution,
    }
)
"""
The execution of each executor kind.
"""
