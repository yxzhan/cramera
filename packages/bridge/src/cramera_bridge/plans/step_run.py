"""
Running the performing robot's steps with a budget of retries.

Each step first looks for the objects it expects in place where the plan was grounded
with them, and checks that they still lie there, before its arm moves; a step keeping an
object in view runs beside a monitor of the robot's camera that gives the step up once
the object leaves its field of view. An object found elsewhere before the step moved
anything is detected again on the table and the step and the ones after it are grounded
anew where it lies, without counting an attempt. Under a lenient runtime check, an
object found anywhere in the box it is looked for in is acted on where it was found, and
an object found nowhere, or a perception that does not answer, leaves the step acting
where the plan was grounded with the object. A step that fails is attempted again
from where the run stopped, the steps after it grounded anew: with the other arm first
where a motion failed, after detecting its objects again where they were lost track of.
Once a step used up its attempts the plan is aborted. Every step as it is scheduled and
starts, every attempt, every change of what the camera keeps in view, every object a
step perceived, found elsewhere or acted on unseen and a safety stop go to the run log.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum

from typing_extensions import Callable, Dict, Iterator, List, Optional, Sequence

from coraplex.datastructures.enums import DetectionTechnique
from coraplex.exceptions import (
    MotionDidNotFinish,
    NothingPerceived,
    PerceptionSourceDidNotAnswer,
    PerceptionSourceFailed,
    PerceptionSourceUnavailable,
)
from coraplex.perception import PerceptionTask, WorldPerception
from coraplex.plans import executors
from coraplex.plans.expected_positions import BodiesWhereExpected, ExpectedPosition
from coraplex.plans.failures import BodiesMovedSinceGrounding, TargetLeftFieldOfView
from coraplex.plans.validation import FailureCause
from coraplex.robot_plans.actions.core.misc import DetectAction
from cramera_bridge.exceptions import ArmOutsideItsWorkspace, StepAbandoned
from cramera_bridge.names import ENUM_NAMES
from cramera_bridge.performing import PlanPerformance, TaskContext
from cramera_bridge.plans.actions import DiscoveredActions
from cramera_bridge.plans.arms import Arms, RobotArms
from cramera_bridge.plans.grounding import GroundedStep, PlanGrounding
from cramera_bridge.plans.perception_reports import object_name
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.run_log import (
    AttemptOutcome,
    LookAt,
    ObjectPerceived,
    RunLog,
    SafetyStopped,
    StepAttempt,
    StepContinuedUnseen,
    StepRegrounded,
    StepScheduled,
    StepStarted,
    View,
)
from cramera_bridge.plans.site import RobotSite
from cramera_bridge.plans.steps import StepActions, StepArguments
from cramera_bridge.plans.world import PlannedGraspable
from cramera_protocol.detection import DetectedObjectReference, DetectionRegion
from cramera_protocol.live_messages import ConstraintMonitorType, MonitorParameterKey
from cramera_protocol.plan_builder import PlanStep, StepConstraint
from cramera_protocol.robot_profile import RuntimeCheck, SafetyLimits
from cramph.composites import CancelledWhenTrue
from cramph.composites import Sequence as StepSequence
from cramph.data_types import LifeCycleValues, ObservationStateValues
from cramph.exceptions import ExecutionFailure
from cramph.executor import (
    Executor,
    ExecutorExtension,
    StatechartExecutor,
    TickingExecutorExtension,
)
from cramph.node import StatechartNode
from cramph.state_history import StateHistory, StateHistoryObserver
from giskardpy.motion_statechart.exceptions import SafetyConstraintViolated
from giskardpy.motion_statechart.monitors.cartesian_monitors import OutOfFieldOfView
from giskardpy.motion_statechart.monitors.safety_monitors import (
    CancelOnSafetyViolation,
    ToolOutsideWorkspace,
)
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix, Point3
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from semantic_digital_twin.world_description.color_names import ColorName
from semantic_digital_twin.world_description.geometry import VolumetricBoundingBox
from semantic_digital_twin.world_description.world_entity import Body

# %% failures the run log reports

OBJECT_RECOGNITION_NOT_RUNNING = "the object recognition (RoboKudo) is not running"
"""
Why a step's objects were not looked for when no RoboKudo pipeline serves its query
action, as a person reads it.
"""


class ErrorsHeldBack(logging.Filter):
    """
    Holds back the error records of a logger, letting the rest pass.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


@contextmanager
def failures_left_to_the_run_log() -> Iterator[None]:
    """
    Hold back, while the block runs, the error the executor logs for each plan that did
    not finish before it raises: the step run reports every such failure in its run
    log, as a retry, a step grounded anew or the step given up, so the error line only
    read like the run failing, as when a check found an object elsewhere before a step.
    """
    logger = logging.getLogger(executors.__name__)
    held_back = ErrorsHeldBack()
    with ExitStack() as holding:
        logger.addFilter(held_back)
        holding.callback(logger.removeFilter, held_back)
        yield


# %% steps as they run


@dataclass(frozen=True)
class ScheduledStep:
    """
    A step of the performing robot as it runs next.
    """

    step: PlanStep
    """
    The step as the plan names it.
    """

    resolved: PlanStep
    """
    The step as it was grounded: naming the arm and the poses its grounding chose.
    """

    expected_positions: Dict[str, List[float]] = field(default_factory=dict)
    """
    Where the objects the step expects in place lay when it was grounded, by identity.
    """

    @classmethod
    def from_grounded_step(cls, grounded: GroundedStep) -> ScheduledStep:
        """
        :param grounded: A step that succeeded its grounding.
        :return: The step as it runs.
        """
        return cls(
            step=grounded.step,
            resolved=grounded.resolved_step(),
            expected_positions=dict(grounded.expected_positions),
        )

    def look_at(self) -> Optional[StepConstraint]:
        """
        :return: The step's first constraint keeping an object in the camera's view, or
            None.
        """
        for constraint in self.step.constraints:
            if (
                constraint.monitor is not None
                and constraint.monitor.monitor_type
                is ConstraintMonitorType.IN_FIELD_OF_VIEW
            ):
                return constraint
        return None


# %% why an attempt failed

StepFailure = ExecutionFailure | PerceptionSourceUnavailable
"""
What ends the run of an attempt at a step: a failure of its plan, or a perception that
is not running when the step looks for its objects.
"""


class Alternative(StrEnum):
    """
    How the next attempt at a failed step differs from the one before.
    """

    OTHER_ARM = "takes the other arm"
    """
    The step's arm, left to its grounding, is the robot's other one.
    """

    REDETECTION = "detects its objects again and grounds the plan anew"
    """
    The step's objects are detected again on the table and the rest of the plan is
    grounded where they were found.
    """


@dataclass(frozen=True)
class FailedAttempt:
    """
    Why an attempt at a step failed.
    """

    reason: str
    """
    The failure, as a person reads it.
    """

    lost_track: bool
    """
    Whether the step's objects were not where the plan held them, so detecting them
    again comes before anything else.
    """

    may_be_attempted_again: bool = True
    """
    Whether another attempt could fare better; a perception that did not answer would
    not answer it either.
    """

    unseen: Optional[str] = None
    """
    What the robot's perception gave nothing for, as a person reads it, when it was not
    running, did not answer or did not find the step's objects on the table again; None
    otherwise.
    """

    @classmethod
    def from_failure(cls, failure: StepFailure, node: StatechartNode) -> FailedAttempt:
        """
        :param failure: What ended the run of the attempt.
        :param node: The node the attempt ran as.
        :return: Why it failed; a plan that did not finish because a detection below
            the node saw nothing failed as not perceived. A perception that is not
            running, did not answer or gave up leaves its objects unseen, and is not
            attempted again unless it gave up.
        """
        if isinstance(failure, MotionDidNotFinish):
            failure = next(
                (
                    NothingPerceived(task.query.semantic_annotation)
                    for task in [node, *node.descendants]
                    if isinstance(task, PerceptionTask)
                    and task.life_cycle_state is LifeCycleValues.FAILED
                ),
                failure,
            )
        reason = (
            OBJECT_RECOGNITION_NOT_RUNNING
            if isinstance(failure, PerceptionSourceUnavailable)
            else failure.error_message()
        )
        return cls(
            reason=reason,
            lost_track=isinstance(
                failure,
                (BodiesMovedSinceGrounding, NothingPerceived, TargetLeftFieldOfView),
            ),
            may_be_attempted_again=not isinstance(
                failure, (PerceptionSourceDidNotAnswer, PerceptionSourceUnavailable)
            ),
            unseen=(
                reason
                if isinstance(
                    failure,
                    (
                        PerceptionSourceDidNotAnswer,
                        PerceptionSourceFailed,
                        PerceptionSourceUnavailable,
                    ),
                )
                else None
            ),
        )

    @classmethod
    def from_grounded_step(cls, grounded: GroundedStep) -> FailedAttempt:
        """
        :param grounded: A step that failed its grounding.
        :return: Why it failed.
        """
        validation = grounded.to_step_validation()
        return cls(
            reason=(
                f"{validation.step} cannot be grounded ({validation.reason}): "
                f"{validation.detail}"
            ),
            lost_track=grounded.check.cause is FailureCause.NOT_PERCEIVED,
        )


# %% following an attempt


@dataclass(frozen=True)
class AttemptedStep:
    """
    A step's node in the plan of an attempt.
    """

    node: StatechartNode
    """
    The node the step runs as.
    """

    step: str
    """
    The identity of the step.
    """

    number: int
    """
    Which attempt at the step the node is.
    """

    action: Optional[StatechartNode] = None
    """
    The node of the step's action, after the checks of the objects it expects in place;
    None when it is :attr:`node` itself.
    """

    arm: Optional[str] = None
    """
    The arm the attempt uses, by the protocol's identifier; None when its grounding
    chose none.
    """

    detected: Optional[DetectedObject] = None
    """
    The object the step perceives: the one a detection step finds for later steps, else
    the one the step acts on, such as a pick's; written as perceived once the step
    succeeded after a perception below it did.
    """

    def failed_before_moving(self) -> bool:
        """
        :return: Whether the step failed before its action started, in the checks of
            the objects it expects in place, so nothing of the robot moved for it.
        """
        return (
            self.action is not None
            and self.action.life_cycle_state is LifeCycleValues.NOT_STARTED
        )

    def has_perceived(self) -> bool:
        """
        :return: Whether a perception below the step's node succeeded.
        """
        return any(
            isinstance(node, PerceptionTask)
            and node.life_cycle_state is LifeCycleValues.SUCCEEDED
            for node in self.node.descendants
        )


@dataclass(frozen=True)
class DetectedObject:
    """
    The object a step perceives.
    """

    step: str
    """
    The identity of the step.
    """

    target: str
    """
    The identity of the object.
    """

    body: Body
    """
    The object's body, whose color perception updates.
    """

    def perceived(self) -> ObjectPerceived:
        """
        :return: The object as perceived now, with the one color it shows, if any.
        """
        colors = self.body.visual.color_names()
        return ObjectPerceived(
            step=self.step,
            target=self.target,
            color=(
                ENUM_NAMES.member_identifier(ColorName, next(iter(colors)))
                if len(colors) == 1
                else None
            ),
        )


@dataclass(frozen=True)
class WatchedTarget:
    """
    The monitor of a step's look-at.
    """

    monitor: OutOfFieldOfView
    """
    The monitor observing whether the target left the camera's view.
    """

    step: str
    """
    The identity of the step.
    """

    target: str
    """
    The identity of the object kept in view.
    """


@dataclass
class AttemptProgress(TickingExecutorExtension, StateHistoryObserver):
    """
    Writes to the run log every step of an attempt's plan that started and succeeded,
    every change of what a step's look-at observes, and a safety stop that cancelled the
    plan.

    It follows the plan after every tick of its executor and every state the plan's
    history records, which on the real robot is the only sign of progress: the motion
    server ticks the plan there, and the executor takes over the states it reports.
    """

    run_log: RunLog
    """
    The run log.
    """

    attempts: int
    """
    How many attempts a step has at most.
    """

    steps: List[AttemptedStep] = field(default_factory=list)
    """
    The steps of the plan.
    """

    watched: List[WatchedTarget] = field(default_factory=list)
    """
    The look-ats of the plan.
    """

    succeeded: set[str] = field(default_factory=set, init=False)
    """
    The identities of the steps whose success was written.
    """

    started: set[str] = field(default_factory=set, init=False)
    """
    The identities of the steps whose start was written.
    """

    views: Dict[str, View] = field(default_factory=dict, init=False)
    """
    What each step's look-at observed last, by the step's identity.
    """

    def after_compile(self, executor: Executor) -> None:
        """
        Follow every state the compiled plan's history records.
        """
        executor.statechart.state_history.add_observer(self)

    def on_state_change(self, history: StateHistory) -> None:
        self.follow()

    def after_tick(self, executor: StatechartExecutor) -> None:
        self.follow()

    def follow(self) -> None:
        """
        Write the steps that started or succeeded and the changed views since the plan
        was followed last.
        """
        for attempted in self.steps:
            self.write_success(attempted)
        # the steps run one after another, so the first that did not succeed runs
        current = next(
            (
                attempted
                for attempted in self.steps
                if attempted.node.life_cycle_state is not LifeCycleValues.SUCCEEDED
            ),
            None,
        )
        if current is not None:
            self.write_start(current)
        for watched in self.watched:
            self.write_view(watched)

    def write_start(self, attempted: AttemptedStep) -> None:
        """
        :param attempted: A step of the plan that started, written once.
        """
        if attempted.step in self.started:
            return
        self.started.add(attempted.step)
        self.run_log.write(
            StepStarted(
                step=attempted.step,
                number=attempted.number,
                attempts=self.attempts,
                arm=attempted.arm,
            )
        )

    def after_run(self, executor: StatechartExecutor) -> None:
        executor.statechart.state_history.remove_observer(self)
        for attempted in self.steps:
            self.write_success(attempted)
        self.write_safety_stop(executor)

    def write_safety_stop(self, executor: StatechartExecutor) -> None:
        """
        :param executor: The executor whose run stopped; when a motion broke a safety
            limit, which cancelled the run, the step whose motion it was and the stop
            are written.
        """
        stop = next(
            (
                node
                for node in executor.statechart.get_nodes_by_type(
                    CancelOnSafetyViolation
                )
                if node.life_cycle_state is not LifeCycleValues.NOT_STARTED
            ),
            None,
        )
        if stop is None:
            return
        # the violation the node kept as it cancelled the motion, or, where it could
        # not keep one, as the monitors observing it describe it now
        constraint = self.stop_description(
            stop, stop.violation or stop.create_exception()
        )
        # the steps run one after another, so the first that did not succeed was stopped
        attempted = next(
            attempted
            for attempted in self.steps
            if attempted.node.life_cycle_state is not LifeCycleValues.SUCCEEDED
        )
        self.run_log.write(
            StepAttempt(
                step=attempted.step,
                number=attempted.number,
                attempts=self.attempts,
                outcome=AttemptOutcome.STOPPED,
                reason=constraint,
                arm=attempted.arm,
            )
        )
        self.run_log.write(SafetyStopped(constraint=constraint, step=attempted.step))

    @staticmethod
    def stop_description(
        stop: CancelOnSafetyViolation, violation: SafetyConstraintViolated
    ) -> str:
        """
        :param stop: The node that cancelled the run.
        :param violation: The violation it cancelled the run with.
        :return: The broken limit and how it was broken, as a person reads it; for a
            tool that left its workspace box, followed by how to bring its arm back,
            naming the arm.
        """
        description = f"{violation.constraint}: {violation.violation}"
        left_its_box = next(
            (
                monitor
                for monitor in stop.monitors
                if isinstance(monitor, ToolOutsideWorkspace)
                and monitor.constraint == violation.constraint
            ),
            None,
        )
        if left_its_box is None:
            return description
        arm = AttemptProgress.arm_of(left_its_box.tool)
        return f"{description}. {ArmOutsideItsWorkspace.recovery(arm)}"

    @staticmethod
    def arm_of(tool: Body) -> str:
        """
        :param tool: The tool of one of the robot's arms.
        :return: Its arm as a sentence names it: ``the left arm``, or ``the arm`` for
            the only arm of a robot or a tool no arm carries.
        """
        for robot in tool._world.get_semantic_annotations_by_type(AbstractRobot):
            arms = RobotArms(robot)
            for arm in robot.all_arms:
                side = arms.find_side(arm)
                if arm.end_effector.tool_frame is tool and side is not Arms.BOTH:
                    return f"the {ENUM_NAMES.member_identifier(Arms, side)} arm"
        return "the arm"

    def write_success(self, attempted: AttemptedStep) -> None:
        """
        :param attempted: A step of the plan, written once it succeeded.
        """
        if attempted.step in self.succeeded:
            return
        if attempted.node.life_cycle_state is not LifeCycleValues.SUCCEEDED:
            return
        self.write_start(attempted)
        self.succeeded.add(attempted.step)
        if attempted.detected is not None and attempted.has_perceived():
            self.run_log.write(attempted.detected.perceived())
        self.run_log.write(
            StepAttempt(
                step=attempted.step,
                number=attempted.number,
                attempts=self.attempts,
                outcome=AttemptOutcome.SUCCEEDED,
                arm=attempted.arm,
            )
        )

    def write_view(self, watched: WatchedTarget) -> None:
        """
        :param watched: A look-at of the plan, written when what it observes changed
            while it runs.
        """
        if watched.monitor.life_cycle_state is not LifeCycleValues.RUNNING:
            return
        observation = watched.monitor.observation_state
        if observation == ObservationStateValues.UNKNOWN:
            return
        view = (
            View.OUT_OF_VIEW
            if observation == ObservationStateValues.TRUE
            else View.IN_VIEW
        )
        if self.views.get(watched.step) is view:
            return
        self.views[watched.step] = view
        self.run_log.write(LookAt(step=watched.step, target=watched.target, view=view))


@dataclass(frozen=True)
class AttemptPlan:
    """
    The plan of one attempt: the steps from the one attempted on, one node each.
    """

    first: int
    """
    The position of the first step among all steps.
    """

    plan: StepSequence
    """
    The plan.
    """

    progress: AttemptProgress
    """
    Writes the plan's progress to the run log.
    """

    def failed_position(self) -> int:
        """
        :return: The position, among all steps, of the step the plan stopped at.
        """
        offset = next(
            index
            for index, node in enumerate(self.plan.nodes)
            if node.life_cycle_state is not LifeCycleValues.SUCCEEDED
        )
        return self.first + offset


# %% the run


@dataclass
class StepRun:
    """
    Runs the performing robot's steps, attempting a failed step again until it used up
    its attempts.
    """

    context: TaskContext
    """
    The world and the robot that performs the steps.
    """

    bodies: Dict[str, Body]
    """
    The plan's objects by identity, and the objects detection steps find by the
    references later steps name them with.
    """

    actions: DiscoveredActions
    """
    The workspace's actions, which steps name.
    """

    site: RobotSite = field(default_factory=RobotSite)
    """
    Where the plan stands the robot, and the robot profile's zones.
    """

    run_log: RunLog = field(default_factory=RunLog)
    """
    Where every attempt is written.
    """

    retries: int = 2
    """
    How often a failed step is attempted again before the plan is aborted.
    """

    regrounding_distance: float = 0.02
    """
    How far, in metres, an object a step expects in place may lie from where the plan
    was grounded with it before the step is grounded anew.
    """

    steps: List[ScheduledStep] = field(default_factory=list)
    """
    The steps as they run, each as it was grounded last.
    """

    safety: Optional[SafetyLimits] = None
    """
    The safety limits of the robot profile, whose workspace boxes a step grounded anew
    is held inside, as its validation held it; None for none.
    """

    attempt_numbers: Dict[str, int] = field(default_factory=dict)
    """
    Which attempt each step is at, by the step's identity; the first when it is absent.
    """

    search_distance: float = 0.05
    """
    How far, in metres, beyond its own bounding box an object a step expects in place is
    looked for around where the plan was grounded with it, before the step moves.
    """

    regrounded: set[str] = field(default_factory=set)
    """
    The identities of the steps grounded anew because an object they expect in place was
    found elsewhere before they moved, which happens once per step.
    """

    runtime_check: RuntimeCheck = RuntimeCheck.STRICT
    """
    How the run treats what it perceives of a step's objects before the step moves.
    """

    continued_unseen: set[str] = field(default_factory=set)
    """
    The identities of the steps acting where the plan was grounded with their objects,
    without looking for them, because the robot's perception gave nothing for them
    under a lenient runtime check.
    """

    @property
    def attempts(self) -> int:
        """
        How many attempts a step has at most.
        """
        return self.retries + 1

    # %% grounding

    def grounding(self) -> PlanGrounding:
        """
        :return: A grounding of steps in a copy of the world as it is now, on the
            twin's objects.
        """
        return PlanGrounding(
            context=self.context,
            bodies=dict(self.bodies),
            actions=self.actions,
            perception=WorldPerception(reports_unseen_bodies=True),
            site=self.site,
            safety=self.safety,
        )

    # %% building an attempt

    def attempt_plan(self, first: int) -> AttemptPlan:
        """
        :param first: The position of the step attempted next.
        :return: The plan of that step and the steps after it.
        """
        progress = AttemptProgress(run_log=self.run_log, attempts=self.attempts)
        nodes = []
        for scheduled in self.steps[first:]:
            action = self.action_node(scheduled, progress)
            node = self.step_node(scheduled, action)
            identifier = scheduled.step.identifier
            progress.steps.append(
                AttemptedStep(
                    node=node,
                    step=identifier,
                    number=self.attempt_numbers.get(identifier, 1),
                    action=action,
                    arm=self.arm_of(scheduled),
                    detected=self.detected_by(scheduled),
                )
            )
            nodes.append(node)
        return AttemptPlan(
            first=first, plan=StepSequence(nodes=nodes), progress=progress
        )

    def action_node(
        self, scheduled: ScheduledStep, progress: AttemptProgress
    ) -> StatechartNode:
        """
        :param scheduled: A step.
        :param progress: Learns of the step's look-at.
        :return: The step's action, beside the monitor of its look-at.
        """
        node = StepActions(
            self.context, self.bodies, self.actions, site=self.site
        ).action(scheduled.resolved)
        look_at = scheduled.look_at()
        if look_at is not None:
            target = look_at.monitor.parameters[MonitorParameterKey.TARGET]
            camera = self.context.robot.get_default_camera()
            monitor = OutOfFieldOfView(
                camera=camera,
                target=self.bodies[target],
                margin=look_at.monitor.parameters.get(MonitorParameterKey.MARGIN, 0.0),
            )
            progress.watched.append(
                WatchedTarget(
                    monitor=monitor, step=scheduled.step.identifier, target=target
                )
            )
            node = CancelledWhenTrue(
                monitor=monitor,
                monitored_node=node,
                exception=TargetLeftFieldOfView(
                    camera=camera, target=self.bodies[target]
                ),
            )
        return node

    def step_node(
        self, scheduled: ScheduledStep, action: StatechartNode
    ) -> StatechartNode:
        """
        :param scheduled: A step.
        :param action: The node of its action, see :meth:`action_node`.
        :return: The action after the detection of each object the step expects in
            place, around where the plan was grounded with it, by a robot whose camera
            sees the table without turning to it, and the check that it still lies
            there, within :attr:`regrounding_distance` or, under a lenient runtime
            check, anywhere it may be found; the action alone when the step expects
            none or acts without looking for its objects.
        """
        expected = [
            ExpectedPosition(
                body=self.bodies[identifier],
                position=Point3(*position, reference_frame=self.context.world.root),
            )
            for identifier, position in scheduled.expected_positions.items()
        ]
        if not expected or scheduled.step.identifier in self.continued_unseen:
            return action
        detections = (
            [
                self.detection(
                    self.bodies[identifier],
                    self.grounded_region(self.bodies[identifier], position),
                )
                for identifier, position in scheduled.expected_positions.items()
            ]
            if self.context.robot.get_torso_if_specified() is None
            else []
        )
        return StepSequence(
            nodes=[
                *detections,
                BodiesWhereExpected(
                    expected=expected, tolerance=self.tolerance(scheduled)
                ),
                action,
            ]
        )

    def tolerance(self, scheduled: ScheduledStep) -> float:
        """
        :param scheduled: A step expecting objects in place.
        :return: How far, in metres, its objects may lie from where the plan was
            grounded with them before the step is grounded anew: the
            :attr:`regrounding_distance`, or, under a lenient runtime check, as far as
            the nearest side of the box the closest-searched object is looked for in.
        """
        if self.runtime_check is RuntimeCheck.STRICT:
            return self.regrounding_distance
        return min(
            self.search_reach(self.bodies[identifier])
            for identifier in scheduled.expected_positions
        )

    def search_reach(self, body: Body) -> float:
        """
        :param body: An object a step expects in place.
        :return: How far, in metres, along each axis it is looked for from where the
            plan was grounded with it: :attr:`search_distance` beyond its own bounding
            box.
        """
        extent = body.collision.as_bounding_box_collection_in_frame(body).bounding_box()
        return (
            max(
                extent.max_x - extent.min_x,
                extent.max_y - extent.min_y,
                extent.max_z - extent.min_z,
            )
            / 2
            + self.search_distance
        )

    def grounded_region(
        self, body: Body, position: List[float]
    ) -> VolumetricBoundingBox:
        """
        :param body: An object a step expects in place.
        :param position: Where the plan was grounded with it, in the world's
            coordinates.
        :return: The box around that position an object of its size is looked for in,
            :attr:`search_distance` beyond its own bounding box.
        """
        reach = self.search_reach(body)
        x, y, z = position
        return VolumetricBoundingBox(
            min_x=x - reach,
            min_y=y - reach,
            min_z=z - reach,
            max_x=x + reach,
            max_y=y + reach,
            max_z=z + reach,
            origin=HomogeneousTransformationMatrix(
                reference_frame=self.context.world.root
            ),
        )

    @staticmethod
    def detection(body: Body, region: VolumetricBoundingBox) -> DetectAction:
        """
        :param body: An object of the plan.
        :param region: Where it is looked for.
        :return: The detection of an object of its kind showing its color in the
            region, which moves the body to where it is found.
        """
        colors = body.visual.color_names()
        return DetectAction(
            technique=DetectionTechnique.TYPES,
            object_sem_annotation=type(PlannedGraspable.of(body)),
            detection_box=region,
            color=next(iter(colors)) if len(colors) == 1 else None,
            accept_first_if_multiple=True,
        )

    # %% reporting

    def announce(self) -> None:
        """
        Write every step as scheduled, with the arm its grounding chose.
        """
        for scheduled in self.steps:
            self.run_log.write(
                StepScheduled(
                    step=scheduled.step.identifier, arm=self.arm_of(scheduled)
                )
            )

    def arm_of(self, scheduled: ScheduledStep) -> Optional[str]:
        """
        :param scheduled: A step.
        :return: The arm its grounding chose for it, by the protocol's identifier, when
            the step leaves its arm to be chosen; else None.
        """
        arguments = StepArguments(
            context=self.context,
            bodies=self.bodies,
            action=self.actions.named(scheduled.step.action),
            step=scheduled.step,
            site=self.site,
        )
        open_field = arguments.find_open_arm_field()
        if open_field is None:
            return None
        return scheduled.resolved.values.get(open_field)

    def detected_by(self, scheduled: ScheduledStep) -> Optional[DetectedObject]:
        """
        :param scheduled: A step.
        :return: The object the step perceives if it perceives one: the one it detects
            for later steps, when it is such a detection, else the one it acts on; None
            when it names no object.
        """
        reference = DetectedObjectReference(scheduled.step.identifier).text
        acted_on = StepArguments(
            context=self.context,
            bodies=self.bodies,
            action=self.actions.named(scheduled.step.action),
            step=scheduled.resolved,
            site=self.site,
        ).object_identifier()
        named = reference if reference in self.bodies else acted_on
        if named not in self.bodies:
            return None
        return DetectedObject(
            step=scheduled.step.identifier,
            target=self.target_of(named),
            body=self.bodies[named],
        )

    # %% running

    def perform(
        self,
        performance: PlanPerformance,
        extensions: Callable[[], Sequence[ExecutorExtension]],
        attempt: AttemptPlan,
    ) -> None:
        """
        Run every step, attempting a failed one again from where the run stopped.

        :param performance: How the plans are performed.
        :param extensions: Gives the executor extensions of each plan performed.
        :param attempt: The plan of the first attempt, from the first step on.
        :raises StepAbandoned: When a step failed every attempt it had.
        :raises SafetyConstraintViolated: When a motion broke a safety limit, which is
            no failure of the step and never attempted again; the attempt's progress
            wrote the stop to the run log.
        """
        while True:
            failure = self.perform_plan(
                attempt.plan, performance, [*extensions(), attempt.progress]
            )
            if failure is None:
                return
            position = attempt.failed_position()
            failed = FailedAttempt.from_failure(
                failure, attempt.plan.nodes[position - attempt.first]
            )
            attempted = attempt.progress.steps[position - attempt.first]
            if failed.unseen is not None and self.may_continue_unseen(attempted):
                self.continue_unseen(position, failed.unseen)
                failed = None
            elif self.was_found_elsewhere(attempted, failed):
                failed = self.reground_where_found(position, performance, extensions)
            if failed is not None:
                self.retry(position, failed, performance, extensions)
            attempt = self.attempt_plan(position)

    def was_found_elsewhere(
        self, attempted: AttemptedStep, failed: FailedAttempt
    ) -> bool:
        """
        :param attempted: The step an attempt stopped at.
        :param failed: Why the attempt failed.
        :return: Whether an object the step expects in place was not where the plan was
            grounded with it before the step moved anything, the first time for the
            step; this is no failed attempt but the plan grounded on where the object
            was believed to be.
        """
        return (
            failed.lost_track
            and attempted.failed_before_moving()
            and attempted.step not in self.regrounded
        )

    def reground_where_found(
        self,
        position: int,
        performance: PlanPerformance,
        extensions: Callable[[], Sequence[ExecutorExtension]],
    ) -> Optional[FailedAttempt]:
        """
        Detect the objects of a step that were found elsewhere again on the table and
        ground the step and the ones after it anew where they lie, as a retry does,
        without counting an attempt.

        :param position: The position of the step.
        :param performance: How the plans are performed.
        :param extensions: Gives the executor extensions of each plan performed.
        :return: Why grounding the step anew failed, which fails its attempt, or None
            when it is ready, or, under a lenient runtime check, acts where the plan was
            grounded with its objects because they were not seen on the table again or
            the robot's perception did not answer.
        """
        scheduled = self.steps[position]
        identifier = scheduled.step.identifier
        self.regrounded.add(identifier)
        objects = [self.target_of(name) for name in scheduled.expected_positions]
        failed = self.prepare(
            position, Alternative.REDETECTION, performance, extensions
        )
        if failed is None:
            self.run_log.write(StepRegrounded(step=identifier, targets=objects))
        elif failed.unseen is not None and self.runtime_check is RuntimeCheck.LENIENT:
            self.continue_unseen(position, failed.unseen)
            return None
        return failed

    def may_continue_unseen(self, attempted: AttemptedStep) -> bool:
        """
        :param attempted: The step an attempt stopped at, its perception having given
            nothing for its objects.
        :return: Whether the step may act where the plan was grounded with its objects
            instead: under a lenient runtime check, when it failed before it moved
            anything.
        """
        return (
            self.runtime_check is RuntimeCheck.LENIENT
            and attempted.failed_before_moving()
            and attempted.step not in self.continued_unseen
        )

    def continue_unseen(self, position: int, unseen: str) -> None:
        """
        Let a step act where the plan was grounded with the objects it expects in place,
        without looking for them, putting them back there in the world the motions are
        planned in, where a detection may have moved them, and say so in the run log.

        :param position: The position of the step.
        :param unseen: What the robot's perception gave nothing for.
        """
        scheduled = self.steps[position]
        identifier = scheduled.step.identifier
        for name, expected in scheduled.expected_positions.items():
            self.put_back(self.bodies[name], expected)
        self.continued_unseen.add(identifier)
        self.run_log.write(
            StepContinuedUnseen(
                step=identifier,
                targets=[self.target_of(name) for name in scheduled.expected_positions],
                reason=unseen,
            )
        )

    def put_back(self, body: Body, position: List[float]) -> None:
        """
        :param body: An object of the plan.
        :param position: Where its origin goes, in the world's coordinates; it keeps
            its orientation.
        """
        world = self.context.world
        parent = body.parent_connection.parent
        parent_P_object = world.transform(
            Point3(*position, reference_frame=world.root), parent
        )
        body.parent_connection.origin = (
            HomogeneousTransformationMatrix.from_point_rotation_matrix(
                point=parent_P_object,
                rotation_matrix=body.parent_connection.origin.rotation_matrix,
                reference_frame=parent,
            )
        )

    def target_of(self, identifier: str) -> str:
        """
        :param identifier: The identity of one of the plan's objects, or a reference to
            the object a detection step found.
        :return: The identity of the plan's object it names.
        """
        body = self.bodies[identifier]
        return next(
            (
                name
                for name, named in self.bodies.items()
                if named is body and DetectedObjectReference.from_value(name) is None
            ),
            body.name.name,
        )

    def perform_plan(
        self,
        plan: StatechartNode,
        performance: PlanPerformance,
        extensions: Sequence[ExecutorExtension],
    ) -> Optional[StepFailure]:
        """
        :param plan: A plan of the run.
        :param performance: How it is performed.
        :param extensions: The executor extensions it runs with.
        :return: The failure that ended it, or None when it succeeded.
        """
        # A failed attempt is the run's to retry, as an action trial's is the
        # chooser's: the failure is caught where the next attempt is decided. A
        # perception that is not running is no plan failure, but leaves the step's
        # objects unseen as one that does not answer does.
        with failures_left_to_the_run_log():
            try:
                performance.perform(plan, self.context, extensions)
            except (ExecutionFailure, PerceptionSourceUnavailable) as failure:
                return failure
        return None

    def retry(
        self,
        position: int,
        failed: FailedAttempt,
        performance: PlanPerformance,
        extensions: Callable[[], Sequence[ExecutorExtension]],
    ) -> None:
        """
        Prepare the next attempt at a failed step, as many times as preparing fails.

        :param position: The position of the failed step.
        :param failed: Why its attempt failed.
        :param performance: How the plans are performed.
        :param extensions: Gives the executor extensions of each plan performed.
        :raises StepAbandoned: When the step used up its attempts, or its failure may
            not be attempted again.
        """
        identifier = self.steps[position].step.identifier
        tried: List[Alternative] = []
        while True:
            number = self.attempt_numbers.get(identifier, 1)
            arm = self.arm_of(self.steps[position])
            if number >= self.attempts or not failed.may_be_attempted_again:
                self.run_log.write(
                    StepAttempt(
                        step=identifier,
                        number=number,
                        attempts=self.attempts,
                        outcome=AttemptOutcome.ABORTED,
                        reason=failed.reason,
                        arm=arm,
                    )
                )
                raise StepAbandoned(identifier, number, failed.reason)
            alternative = self.alternative(position, failed, tried)
            tried.append(alternative)
            self.run_log.write(
                StepAttempt(
                    step=identifier,
                    number=number,
                    attempts=self.attempts,
                    outcome=AttemptOutcome.RETRYING,
                    reason=failed.reason,
                    next_attempt=alternative.value,
                    arm=arm,
                )
            )
            self.attempt_numbers[identifier] = number + 1
            failed = self.prepare(position, alternative, performance, extensions)
            if failed is None:
                return

    def alternative(
        self, position: int, failed: FailedAttempt, tried: List[Alternative]
    ) -> Alternative:
        """
        :param position: The position of the failed step.
        :param failed: Why its attempt failed.
        :param tried: The alternatives tried for it since it failed in the run.
        :return: The other arm, unless the step lost track of its objects, leaves no
            arm to choose or took the other arm already; else detecting its objects
            again.
        """
        if (
            failed.lost_track
            or Alternative.OTHER_ARM in tried
            or self.other_arm_step(self.steps[position]) is None
        ):
            return Alternative.REDETECTION
        return Alternative.OTHER_ARM

    def prepare(
        self,
        position: int,
        alternative: Alternative,
        performance: PlanPerformance,
        extensions: Callable[[], Sequence[ExecutorExtension]],
    ) -> Optional[FailedAttempt]:
        """
        Ground the failed step and the steps after it anew as the alternative says.

        :param position: The position of the failed step.
        :param alternative: How the next attempt differs.
        :param performance: How the plans are performed.
        :param extensions: Gives the executor extensions of each plan performed.
        :return: Why preparing the attempt failed, or None when it is ready.
        """
        steps = [scheduled.step for scheduled in self.steps[position:]]
        if (
            alternative is Alternative.REDETECTION
            and self.steps[position].expected_positions
        ):
            detection = self.redetection(self.steps[position])
            failure = self.perform_plan(detection, performance, extensions())
            if failure is not None:
                redetected = FailedAttempt.from_failure(failure, detection)
                lost = ", ".join(
                    f"the {object_name(self.target_of(name))}"
                    for name in self.steps[position].expected_positions
                )
                return FailedAttempt(
                    reason=f"{lost} was not detected on the table again: "
                    f"{redetected.reason}",
                    lost_track=True,
                    unseen=redetected.unseen or f"{lost} was not seen",
                )
        if alternative is Alternative.OTHER_ARM:
            steps[0] = self.other_arm_step(self.steps[position])
        grounded = self.grounding().ground(steps)
        for grounded_step in grounded:
            if not grounded_step.succeeded:
                return FailedAttempt.from_grounded_step(grounded_step)
        self.steps[position:] = [
            ScheduledStep.from_grounded_step(grounded_step)
            for grounded_step in grounded
        ]
        return None

    def other_arm_step(self, scheduled: ScheduledStep) -> Optional[PlanStep]:
        """
        :param scheduled: A step.
        :return: The step naming the arm of the robot other than the one its grounding
            chose, when it leaves its arm to be chosen between a left and a right one;
            else None.
        """
        arguments = StepArguments(
            context=self.context,
            bodies=self.bodies,
            action=self.actions.named(scheduled.step.action),
            step=scheduled.step,
            site=self.site,
        )
        open_field = arguments.find_open_arm_field()
        if open_field is None:
            return None
        chosen = ENUM_NAMES.member(Arms, scheduled.resolved.values[open_field])
        others = {Arms.LEFT: Arms.RIGHT, Arms.RIGHT: Arms.LEFT}
        if chosen not in others:
            return None
        return replace(
            scheduled.step,
            values={
                **scheduled.step.values,
                open_field: ENUM_NAMES.member_identifier(Arms, others[chosen]),
            },
        )

    def redetection(self, scheduled: ScheduledStep) -> StatechartNode:
        """
        :param scheduled: A step.
        :return: The detection, on the robot's table, of each object the step expects in
            place, by its kind and the color it shows.
        """
        table = RegionBoxes(self.context.robot, site=self.site).box(DetectionRegion())
        return StepSequence(
            nodes=[
                self.detection(self.bodies[identifier], table)
                for identifier in scheduled.expected_positions
            ]
        )
