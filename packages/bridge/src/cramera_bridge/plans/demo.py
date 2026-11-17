"""
A plan run as a robot task demo: the plan's robots and objects in its environment, the
performing robot's steps as native actions, each step leaving its arm open given the arm
its grounding chose, and the placements measured afterwards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cached_property
from pathlib import Path

from typing_extensions import Callable, Dict, Iterator, List, Optional

from coraplex.perception import WorldPerception
from coraplex.plans.plan_transformation import PlanRewriting
from coraplex.robot_plans.plan_transformations import (
    DetectBeforeGrasp,
    LimitHowLongDetectionsWait,
    LetOnlyTheObjectTouchTheGripper,
)
from cramera_bridge.exceptions import StepNotGrounded, TaskFailed
from cramera_bridge.execution import StartingPosture
from cramera_bridge.geometry import NumericPose
from cramera_bridge.performing import PlanPerformance, TaskContext
from cramera_bridge.real_robot import ReportedPosture
from cramera_bridge.robot_task import NAME_PART_SEPARATOR, RobotTaskDemo
from cramera_bridge.scenes.multi_robot import RobotPlacement, RobotScene
from cramera_bridge.plans.actions import ActionDiscovery, DiscoveredActions
from cramera_bridge.plans.detected_objects import DetectedObjects
from cramera_bridge.plans.grounding import PlanGrounding
from cramera_bridge.plans.run_log import (
    RecordSaving,
    RunLog,
    RunRefused,
    SafetyWatched,
)
from cramera_bridge.plans.safety import RobotSafety
from cramera_bridge.plans.site import RobotSite
from cramera_bridge.plans.perception_reporting import PerceptionReporting
from cramera_bridge.plans.step_run import AttemptPlan, ScheduledStep, StepRun
from cramera_bridge.plans.steps import StepArguments
from cramera_bridge.plans.workspace import RobotAnnotations
from cramera_bridge.plans.world import EmptyFloor, PlannedObjects
from cramera_bridge.scenes.bundle_world import PoseText
from cramera_protocol.bundle.acceptance import AcceptanceMetrics
from cramera_protocol.bundle.index import SceneName
from cramera_protocol.geometry import Pose as PlannedPose
from cramph.executor import ExecutorExtension
from cramph.node import StatechartNode
from cramera_protocol.plan_builder import (
    ActionFieldKind,
    DemoDocument,
    Destination,
    PlacementMode,
    PlanDocument,
    PlannedRobot,
    PlanStep,
)
from cramera_protocol.robot_profile import RuntimeCheck
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

PLACEMENT_TOLERANCE = 0.1
"""
How far, in metres, an object put down at a pose may end up from it.
"""

MAXIMUM_NAME_LENGTH = 64
"""
How long a recording's name may be, as :class:`SceneName` allows.
"""

FIRST_REPEAT = 2
"""
The number the first repeated recording of a name carries.
"""

PLACEMENT_ERROR = "placementError"
"""
The measurement of how far an object ended from where a step put it, followed by the
step's identity.
"""

PLACED_POSITION = "placedPosition"
"""
The measurement of where an object a step put down ended once the plan ran, followed by
the step's identity.
"""

MEASUREMENT_SEPARATOR = "."
"""
Joins a measurement's name and the step it measures.
"""

# %% placements


@dataclass(frozen=True)
class Placement:
    """
    An object a step puts down at a pose, which the run's end is measured against.
    """

    step: PlanStep
    """
    The step.
    """

    object_identifier: str
    """
    The identity of the object put down.
    """

    pose: PlannedPose
    """
    Where the step puts it.
    """


# %% robots


@dataclass(frozen=True)
class PlannedRobots:
    """
    The robots of a plan as the native scene of several robots, each under its own
    namespace.
    """

    plan: PlanDocument
    """
    The plan.
    """

    annotations: RobotAnnotations = field(default_factory=RobotAnnotations)
    """
    The workspace's robot annotations, which the plan names by class name.
    """

    def performing(self) -> PlannedRobot:
        """
        :return: The robot whose steps run: the selected one, else the first.
        """
        return self.plan.performing_robot()

    def placement(self, robot: PlannedRobot) -> RobotPlacement:
        """
        :param robot: A robot of the plan.
        :return: Its native placement under the namespace of its identity.
        """
        return RobotPlacement(
            namespace=robot.identifier,
            label=robot.label,
            robot_type=self.annotations.named(robot.model),
            pose=PoseText(robot.pose).native(None),
            joint_positions=dict(robot.joint_positions),
        )

    def scene(self) -> RobotScene:
        """
        :return: Every robot of the plan, the performing one selected.
        """
        return RobotScene(
            placements=[self.placement(robot) for robot in self.plan.robots],
            selected_namespace=self.performing().identifier,
        )


# %% demo


@dataclass
class PlannedTaskDemo(RobotTaskDemo):
    """
    Runs a plan composed in the plan builder: its robots and objects in its environment,
    the performing robot's steps one after another.
    """

    collision_avoidance: bool = True
    """
    Whether every motion avoids collisions with the environment and the robot itself; a
    plan's run, its validation and its dry-run agree on it.
    """

    document: DemoDocument = field(kw_only=True)
    """
    The plan and where its environment was found.
    """

    robot_scene: RobotScene = field(kw_only=True)
    """
    The plan's robots.
    """

    bodies: Dict[str, Body] = field(default_factory=dict, init=False)
    """
    The plan's objects by identity once the world is built, and once the plan is built
    the objects its detection steps find by the references later steps name them with.
    """

    discovery: ActionDiscovery = field(default_factory=ActionDiscovery, kw_only=True)
    """
    Finds the native actions the steps name.
    """

    performed_steps: List[PlanStep] = field(default_factory=list, init=False)
    """
    The performing robot's steps as the plan was built: each arm left open given and
    each destination only known once grounded resolved to its pose.
    """

    retries: int = field(default=2, kw_only=True)
    """
    How often a failed step is attempted again before the plan is aborted.
    """

    regrounding_distance: float = field(default=0.02, kw_only=True)
    """
    How far, in metres, an object a step expects in place may lie from where the plan
    was grounded with it before the step is grounded anew.
    """

    run_log: RunLog = field(default_factory=RunLog, kw_only=True)
    """
    Where every attempt at a step is written.
    """

    step_run: Optional[StepRun] = field(default=None, init=False)
    """
    Runs the steps with retries, once the plan was built.
    """

    first_attempt: Optional[AttemptPlan] = field(default=None, init=False)
    """
    The plan of the first attempt at every step, once the plan was built.
    """

    @cached_property
    def discovered_actions(self) -> DiscoveredActions:
        """
        The workspace's actions, discovered once per run.
        """
        return self.discovery.discover()

    @classmethod
    def of_document(cls, document: DemoDocument, port: int) -> PlannedTaskDemo:
        """
        :param document: A plan the plan builder checked, and its environment.
        :param port: The port the run is published on.
        :return: The demo; a plan in an environment bundle is recorded as
            ``<bundle>_<robot model>_<plan name>``, otherwise under the plan's name, each
            space of the name as ``_``.
        :raises EnvironmentBundleHoldsRobot: When the environment bundle holds a robot.
        """
        plan = document.plan
        robots = PlannedRobots(plan)
        performing = robots.performing()
        scene = robots.scene()
        fields = dict(
            robot=robots.placement(performing).specification(),
            task=plan.task or plan.name,
            port=port,
            document=document,
            robot_scene=scene,
        )
        if document.environment_directory is None:
            return cls(environment=EmptyFloor(), name=plan.file_name(), **fields)
        return cls.in_environment_bundle(
            Path(document.environment_directory),
            robot_name=performing.model,
            task_identifier=plan.file_name(),
            **fields,
        )

    @property
    def plan(self) -> PlanDocument:
        """
        The plan the demo runs.
        """
        return self.document.plan

    def steps(self) -> List[PlanStep]:
        """
        :return: The performing robot's steps.
        """
        return PlannedRobots(self.plan).performing().steps

    @property
    def site(self) -> RobotSite:
        """
        Where the plan stands the performing robot, and the robot profile's zones.
        """
        return RobotSite.of_demo(self.document)

    def build_world(self) -> World:
        """
        :return: The world the execution reports from the real robot, which stands as
            it is and is named as the plan names it, holding the plan's objects; where
            no real robot is reached, a new world holding the environment, every robot
            at its start pose with its arms parked, as a physics run starts them and
            the real robot stands inside its workspaces, and the plan's objects: a robot
            whose arms stood out in its description's zero posture could start a run
            inside an object, which collision avoidance refuses to move from.
        """
        world = self.execution.reported_world()
        if world is None:
            world = self.robot_scene.build_world(self.environment)
            StartingPosture().apply(world)
        else:
            self.robot_scene.selected_placement().name_reported_robot(world)
        self.bodies = PlannedObjects(self.plan.objects).spawn(world)
        return world

    def performance(self, context: TaskContext) -> PlanPerformance:
        """
        Differs from :meth:`RobotTaskDemo.performance` in having every grasp detect its
        object first, whichever execution runs it, every detection wait for its answer
        as long as the robot profile's perception settings allow, and in holding every
        motion to the safety limits of the robot profile.

        Under a lenient runtime check, a robot whose camera sees the table without
        turning detects no object before the final approach of a grasp: each step
        detected its objects with the same camera just before it moved, see
        :meth:`StepRun.step_node`, and acts where they were found, or where the plan
        was grounded with them when they were not.
        """
        performance = super().performance(context)
        detects_before_grasp = (
            self.document.perception.runtime_check is RuntimeCheck.STRICT
            or context.robot.get_torso_if_specified() is not None
        )
        performance.plan_transformations = [
            *([DetectBeforeGrasp()] if detects_before_grasp else []),
            LimitHowLongDetectionsWait(
                answer_timeout=timedelta(
                    seconds=self.document.perception.answer_timeout
                )
            ),
        ]
        if self.document.safety is None:
            return performance
        performance.safety = RobotSafety(
            limits=self.document.safety,
            site=self.site,
            on_the_real_robot=self.execution.reported_world() is not None,
        ).envelope(context)
        return performance

    def build_context(self, world: World) -> TaskContext:
        """
        Differs from :meth:`RobotTaskDemo.build_context` in performing with the plan's
        selected robot, which in the world a real robot reports is the one robot of its
        model, and in letting a gripper touch only the object its step acts on, while
        every motion and every step tried before the run avoids collisions where the
        demo has :attr:`collision_avoidance`.
        """
        placement = self.robot_scene.selected_placement()
        robot = (
            placement.resolve(world)
            if self.execution.reported_world() is None
            else placement.resolve_by_model(world)
        )
        return TaskContext(
            world=world,
            robot=robot,
            collision_avoidance=self.collision_avoidance,
            extensions=[
                PlanRewriting(transformations=[LetOnlyTheObjectTouchTheGripper()])
            ],
        )

    def build_plan(self, context: TaskContext) -> StatechartNode:
        """
        :param context: The world and the performing robot.
        :return: The performing robot's steps one after another, each its native action
            after the check of the objects it expects in place, and beside the monitor
            of its look-at.
        :raises StepNotGrounded: When a step leaves its arm open and the plan cannot be
            grounded.
        """
        steps = self.grounded_steps(context)
        self.performed_steps = [scheduled.resolved for scheduled in steps]
        self.bodies.update(
            DetectedObjects(
                context, self.bodies, self.discovered_actions, site=self.site
            ).resolve(self.performed_steps)
        )
        self.step_run = StepRun(
            context=context,
            bodies=self.bodies,
            actions=self.discovered_actions,
            site=self.site,
            run_log=self.run_log,
            retries=self.retries,
            regrounding_distance=self.regrounding_distance,
            steps=steps,
            safety=self.document.safety,
            runtime_check=self.document.perception.runtime_check,
        )
        self.first_attempt = self.step_run.attempt_plan(0)
        if self.document.safety is not None:
            self.run_log.write(SafetyWatched())
        self.step_run.announce()
        return self.first_attempt.plan

    def perform_plan(
        self,
        plan: StatechartNode,
        context: TaskContext,
        performance: PlanPerformance,
        extensions: Callable[[], List[ExecutorExtension]],
    ) -> None:
        """
        Differs from :meth:`RobotTaskDemo.perform_plan` in attempting a failed step
        again, in measuring where the steps as they were grounded last put their
        objects, and in reporting what RoboKudo answered every detection.

        :raises StepAbandoned: When a step failed every attempt it had.
        :raises SafetyConstraintViolated: When a motion broke a safety limit, which ends
            the run without another attempt.
        """
        reporting = self.perception_reporting()

        def reported_extensions() -> List[ExecutorExtension]:
            return [*extensions(), *reporting]

        self.step_run.perform(performance, reported_extensions, self.first_attempt)
        self.performed_steps = [scheduled.resolved for scheduled in self.step_run.steps]

    def perception_reporting(self) -> List[PerceptionReporting]:
        """
        :return: The reporting of every detection's answer to the run log, where
            RoboKudo answers the detections; none where the world model does.
        """
        answers = self.execution.perception_answers()
        if answers is None:
            return []
        return [
            PerceptionReporting(
                answers=answers, step_run=self.step_run, run_log=self.run_log
            )
        ]

    def grounded_steps(self, context: TaskContext) -> List[ScheduledStep]:
        """
        :param context: The world and the performing robot.
        :return: The performing robot's steps, each leaving its arm open naming the arm
            that reaches and each destination only known once grounded naming the pose
            it resolves to, as a grounding of the plan in a copy of the world on the
            twin's objects finds them, with the positions it expects its objects at. On
            the real robot the copy stands as its drivers report, since the world
            Giskard serves holds no joint positions before its first motion.
        :raises StepNotGrounded: When a step fails that grounding.
        """
        steps = self.steps()
        if not any(self.is_left_open(context, step) for step in steps):
            return [ScheduledStep(step=step, resolved=step) for step in steps]
        grounding = PlanGrounding(
            context=context,
            bodies=dict(self.bodies),
            actions=self.discovered_actions,
            perception=WorldPerception(reports_unseen_bodies=True),
            site=self.site,
            safety=self.document.safety,
        )
        if self.execution.reported_world() is not None:
            ReportedPosture().apply(grounding.copied_context.world)
        grounded = grounding.ground(steps)
        for grounded_step in grounded:
            if not grounded_step.succeeded:
                validation = grounded_step.to_step_validation()
                raise StepNotGrounded(validation.step, validation.detail)
        return [
            ScheduledStep.from_grounded_step(grounded_step)
            for grounded_step in grounded
        ]

    def is_left_open(self, context: TaskContext, step: PlanStep) -> bool:
        """
        :param context: The world and the performing robot.
        :param step: A step of the performing robot.
        :return: Whether it leaves its arm open, or a destination only known once the
            plan is grounded.
        """
        arguments = StepArguments(
            context=context,
            bodies=self.bodies,
            action=self.discovered_actions.named(step.action),
            step=step,
            site=self.site,
        )
        return arguments.find_open_arm_field() is not None or bool(
            arguments.late_destination_fields()
        )

    def on_record_saving(self) -> None:
        """
        Report in the run log that the run is over and its robot released, so the plan
        builder shows the run's record being saved rather than the run running.
        """
        self.run_log.write(RecordSaving())

    def on_refused(self, refusal: str) -> None:
        """
        Report in the run log that the robot was not ready and nothing moved, so the
        plan builder shows the refusal rather than a failed run.

        :param refusal: What is missing and what to do, as a person reads it.
        """
        self.run_log.write(RunRefused(refusal))

    def measure(self, context: TaskContext) -> AcceptanceMetrics:
        """
        Measure how far each object put down at a pose ended from it, and where it
        ended.

        :param context: The world after the plan ran.
        :return: The distances and the positions by step.
        :raises TaskFailed: When an object ended farther than
            :data:`PLACEMENT_TOLERANCE` from its pose.
        """
        measurements = {}
        for placement in self.placements():
            position = NumericPose.of_entity(
                context.world, self.bodies[placement.object_identifier]
            ).position
            error = math.dist(position, placement.pose.to_json()[:3])
            if error > PLACEMENT_TOLERANCE:
                raise TaskFailed(
                    f"{placement.object_identifier} ended {error:.3f} m from the pose "
                    f"of {placement.step.identifier}"
                )
            measurements[
                MEASUREMENT_SEPARATOR.join((PLACEMENT_ERROR, placement.step.identifier))
            ] = error
            measurements[
                MEASUREMENT_SEPARATOR.join((PLACED_POSITION, placement.step.identifier))
            ] = list(position)
        return AcceptanceMetrics(measurements=measurements)

    def placements(self) -> Iterator[Placement]:
        """
        :return: The objects the performing robot's steps put down at a pose, as the
            plan was built: the first body of a step whose field accepting a surface
            names a pose.
        """
        for step in self.performed_steps or self.steps():
            schema = self.discovered_actions.named(step.action).schema
            objects = [
                step.values[offered.name]
                for offered in schema.fields
                if offered.kind is ActionFieldKind.BODY and offered.name in step.values
            ]
            if not objects:
                continue
            for offered in schema.fields:
                if not offered.accepts_surface or offered.name not in step.values:
                    continue
                destination = Destination.from_json(step.values[offered.name])
                if destination.placement is PlacementMode.POSE:
                    yield Placement(
                        step=step, object_identifier=objects[0], pose=destination.pose
                    )

    def scene_name(self) -> str:
        """
        :return: The recording's name: the demo's name when it is a valid name, else
            the plan's, followed by ``_2``, ``_3`` and so on when the local scenes
            directory already holds it.
        """
        base = self.name if SceneName.is_valid(self.name) else self.plan.file_name()
        name = base
        repeat = FIRST_REPEAT
        while (self.locations.local_scenes / name).exists():
            suffix = f"{NAME_PART_SEPARATOR}{repeat}"
            name = f"{base[: MAXIMUM_NAME_LENGTH - len(suffix)]}{suffix}"
            repeat += 1
        return SceneName(name).text
