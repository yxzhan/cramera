"""
Performing a plan: the world and the robot a task is performed with, and running one
plan node in a statechart of its own.

coraplex runs a plan as the top-level node of a cramph statechart, compiled and executed
by a plan executor whose context gives the nodes the robot and how statements are
grounded. A context serves one executor, so every plan performed gets a context of its
own, made from the :class:`TaskContext` it was built with: the one a plan was built in,
if it asked for one, else a new one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import List, Optional, Sequence

from coraplex.plans.context_extensions import RobotAccess, StatementGrounding
from coraplex.plans.executors import PlanExecutor
from coraplex.plans.plan_transformation import PlanRewriting, PlanTransformation
from cramera_bridge.execution import KinematicExecution, PlanExecution
from cramph.context import ContextExtension, StatechartContext
from cramph.executor import ExecutorExtension
from cramph.node import StatechartNode
from cramph.statechart import Statechart
from giskardpy.motion_statechart.monitors.safety_monitors import SafetyEnvelope
from krrood.entity_query_language.backends import (
    EntityQueryLanguageGenerativeBackend,
    QueryBackend,
)
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from semantic_digital_twin.world import World

# %% context


@dataclass
class TaskContext:
    """
    The world and the robot a task is performed with, and the backend grounding the
    parts its plans leave open.
    """

    world: World
    """
    The world the task is performed in.
    """

    robot: AbstractRobot
    """
    The robot performing the task.
    """

    query_backend: QueryBackend = field(
        default_factory=EntityQueryLanguageGenerativeBackend
    )
    """
    Grounds the statements of the task's plans, as coraplex does by default.
    """

    sampling_seed: Optional[int] = StatementGrounding.sampling_seed
    """
    Seeds the locations the plans' actions sample, so a run repeats; None samples afresh
    each run.
    """

    collision_avoidance: bool = False
    """
    Whether every motion of the task, and every step tried before it runs, avoids
    collisions with the robot's surroundings and with itself.
    """

    extensions: List[ContextExtension] = field(default_factory=list)
    """
    Further context extensions every plan of the task runs with, such as the
    :class:`~coraplex.plans.plan_transformation.PlanRewriting` of its plans.
    """

    next_context: Optional[StatechartContext] = field(default=None, init=False)
    """
    The context the next plan performed runs in, once a plan was built in it.
    """

    def statechart_context(self) -> StatechartContext:
        """
        :return: The context the next plan performed runs in, for a plan whose nodes
            are built in it: the task's robot, how its statements are grounded and
            :attr:`extensions`.
        """
        if self.next_context is None:
            self.next_context = StatechartContext.with_extensions(
                self.world, self.context_extensions()
            )
        return self.next_context

    def context_extensions(self) -> List[ContextExtension]:
        """
        :return: New context extensions of a plan of the task: the task's robot, how
            its statements are grounded and :attr:`extensions`.
        """
        return [
            RobotAccess(self.robot),
            StatementGrounding(
                query_backend=self.query_backend,
                sampling_seed=self.sampling_seed,
            ),
            *self.extensions,
        ]

    def take_statechart_context(self) -> StatechartContext:
        """
        :return: The context the next plan performed runs in, which no later plan
            shares.
        """
        context = self.statechart_context()
        self.next_context = None
        return context


# %% performing


@dataclass
class PlanPerformance:
    """
    How the plans of a task are performed: by which execution, with which settings and
    which further executor extensions.
    """

    execution: PlanExecution = field(default_factory=KinematicExecution)
    """
    Gives each plan the executor that runs it, which decides how its motions are
    executed.
    """

    collision_avoidance: bool = False
    """
    Whether every motion avoids collisions with the environment.
    """

    extensions: Sequence[ExecutorExtension] = ()
    """
    Added to every executor, such as the extension publishing its plan.
    """

    safety: SafetyEnvelope = field(default_factory=SafetyEnvelope)
    """
    The safety constraints every motion of every plan is held to.
    """

    plan_transformations: Sequence[PlanTransformation] = ()
    """
    The transformations rewriting every plan performed, whichever execution runs it,
    after those of the plan's own
    :class:`~coraplex.plans.plan_transformation.PlanRewriting`.
    """

    def executor(
        self,
        context: TaskContext,
        extensions: Sequence[ExecutorExtension] = (),
    ) -> PlanExecutor:
        """
        :param context: The world and the robot the plan is performed with.
        :param extensions: Added to the executor after :attr:`extensions`.
        :return: A new executor with a context of its own, which rewrites the plan with
            :attr:`plan_transformations` too.
        """
        statechart_context = context.take_statechart_context()
        if self.plan_transformations:
            rewriting = (
                statechart_context.get_extension(PlanRewriting) or PlanRewriting()
            )
            statechart_context.extensions[PlanRewriting] = PlanRewriting(
                transformations=[
                    *rewriting.transformations,
                    *self.plan_transformations,
                ]
            )
        return self.execution.executor(
            statechart_context,
            collision_avoidance=self.collision_avoidance,
            extensions=[*self.extensions, *extensions],
            safety=self.safety,
        )

    def perform(
        self,
        plan: StatechartNode,
        context: TaskContext,
        extensions: Sequence[ExecutorExtension] = (),
    ) -> PlanExecutor:
        """
        Perform a plan in a statechart of its own until it ended.

        :param plan: The plan's top-level node.
        :param context: The world and the robot the plan is performed with.
        :param extensions: Added to the executor after :attr:`extensions`.
        :return: The executor that ran the plan, holding its statechart.
        :raises MotionDidNotFinish: If the plan did not succeed, or another
            :class:`~coraplex.plans.failures.PlanFailure`.
        :raises SafetyConstraintViolated: If a motion broke one of :attr:`safety`.
        """
        executor = self.executor(context, extensions)
        statechart = Statechart(context=executor.context)
        statechart.add_node(plan)
        try:
            executor.compile(statechart)
            executor.execute()
        finally:
            executor.close()
        return executor


def perform(
    plan: StatechartNode,
    context: TaskContext,
    performance: Optional[PlanPerformance] = None,
) -> PlanExecutor:
    """
    Perform a plan kinematically simulated, or as a performance says.

    :param plan: The plan's top-level node.
    :param context: The world and the robot the plan is performed with.
    :param performance: How the plan is performed; kinematically simulated without
        collision avoidance when None.
    :return: The executor that ran the plan.
    """
    return (performance or PlanPerformance()).perform(plan, context)
