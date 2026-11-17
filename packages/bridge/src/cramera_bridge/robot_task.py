"""
Robot task demos: a CRAM robot performs a task in a scene environment while the run is
published to the viewer, measured and saved as a scene bundle.

A demo states its environment, its robot, its plan and how its outcome is measured; the
run itself, the recording and saving it, and publishing it later are the same for every
demo.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from argparse import ArgumentParser
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import (
    Any,
    Callable,
    Iterator,
    List,
    Optional,
    Self,
    Sequence,
    Tuple,
)

from cramera.locations import DataLocations
from cramera.scenes import SceneSource
from cramera_bridge.database.native_plans import PendingPlans
from cramera_bridge.database.runs import StoredRun
from cramera_bridge.documents import DocumentFile
from cramera_bridge.exceptions import (
    EnvironmentBundleHoldsRobot,
    RecordingNotSaved,
    SceneNameTaken,
)
from cramera_bridge.execution import KinematicExecution, PlanExecution
from cramera_bridge.export.scene_index import SceneIndexFile
from cramera_bridge.live.query_steps import HeldQueries
from cramera_bridge.live.server import LIVE_PORT
from cramera_bridge.performing import PlanPerformance, TaskContext
from cramera_bridge.query_stepping import QueryStepper
from cramera_bridge.scenes.bundle_world import BundleEnvironment
from cramera_bridge.scenes.environment import SceneEnvironment
from cramera_bridge.scenes.presentation import ScenePresentation
from cramera_bridge.visualization import LiveVisualization
from cramera_protocol.bundle.acceptance import AcceptanceMetrics
from cramera_protocol.bundle.files import BundleFile
from cramera_protocol.bundle.index import SceneIndexEntry, SceneName
from cramera_protocol.bundle.scene import Camera, SceneDescription
from cramera_protocol.live_messages import (
    RecordingSaveRequest,
    SavedScene,
    SceneDestination,
)
from cramera_protocol.query_steps import StepPhase
from cramph.executor import ExecutorExtension
from cramph.node import StatechartNode
from semantic_digital_twin.specifications.robots import RobotSpecification
from semantic_digital_twin.world import World

PUBLISHING_PREFIX = ".publish_"
"""
The prefix of the folder a bundle is staged in while it is published.
"""

REPLACED_SUFFIX = ".replaced"
"""
The suffix of a shared bundle's old version while the new one takes its place.
"""

NAME_PART_SEPARATOR = "_"
"""
What separates the environment, the robot and the task in a recording's name.
"""

# %% demo


@dataclass
class RobotTaskDemo(ABC):
    """
    One robot task in one scene environment, recorded into a bundle named after the
    scene, the robot and the task.

    Subclasses build the plan, or several plans run one after another, and measure the
    outcome from the world; :meth:`run` does the rest: it builds the world, publishes it
    while the plans run, records the run, checks the outcome and saves the recording
    into the local scenes directory.
    """

    environment: SceneEnvironment
    """
    Builds the world the task is performed in.
    """

    robot: RobotSpecification
    """
    The CRAM robot and where it starts.
    """

    name: str
    """
    The name the recording is saved under.
    """

    task: str
    """
    What the task does, as the scene picker labels it.
    """

    environment_name: Optional[str] = None
    """
    The environment's display name; recordings in the same environment share it.
    """

    camera: Optional[Camera] = None
    """
    The first view of the recording, or None to keep the environment's view.
    """

    presentation: Optional[ScenePresentation] = None
    """
    The appearance of the environment bundle the recording takes over, if any.
    """

    locations: DataLocations = field(
        default_factory=lambda: DataLocations.from_environment(
            dict(os.environ), Path.home()
        )
    )
    """
    Where the recording is saved: its local scenes directory, as the process's
    environment configures it.
    """

    port: int = LIVE_PORT
    """
    The port the run is published on while it runs.
    """

    execution: PlanExecution = field(default_factory=KinematicExecution)
    """
    How each plan's motions are executed, which sets the world up while the plans run
    and gives each plan its executor.
    """

    collision_avoidance: bool = False
    """
    Whether every motion avoids collisions with the environment.
    """

    step_queries: bool = False
    """
    Whether the run publishes each query of its own as a query step, from the world's
    setup on, and can be held at it until the viewer moves it on.
    """

    holds_at_queries: bool = True
    """
    Whether a run stepping through its queries starts held at each; the viewer can
    change it while the run goes on.
    """

    stepper: Optional[QueryStepper] = field(init=False, default=None)
    """
    Steps through the current run's queries, once a run stepping through them started.
    """

    adds_a_run: bool = False
    """
    Whether the run joins the runs of the local bundle that already holds the name,
    replacing its files, instead of the name being refused.
    """

    @abstractmethod
    def build_plan(self, context: TaskContext) -> StatechartNode:
        """
        :param context: The world and the robot the task is performed with.
        :return: The task's plan: the top-level node of the statechart it runs as.
        """

    def build_plans(self, context: TaskContext) -> List[StatechartNode]:
        """
        :param context: The world and the robot the task is performed with.
        :return: The plans performed one after another and recorded together: the
            task's plan alone, unless the task needs several, such as one per robot.
        """
        return [self.build_plan(context)]

    @abstractmethod
    def measure(self, context: TaskContext) -> AcceptanceMetrics:
        """
        Measure the outcome from the world after the plan ran.

        :param context: The world and the robot the task was performed with.
        :return: The measurements.
        :raises TaskFailed: When the outcome is not the task's.
        """

    @classmethod
    def in_environment_bundle(
        cls,
        directory: Path,
        robot: RobotSpecification,
        robot_name: str,
        task_identifier: str,
        task: str,
        **fields: Any,
    ) -> Self:
        """
        A task in the environment of a scene bundle, recorded as ``<bundle>_<robot
        name>_<task identifier>`` with the bundle's environment name and appearance, so
        the viewer offers the recording beside its environment.

        :param directory: An environment bundle.
        :param robot: The CRAM robot and where it starts.
        :param robot_name: The robot's part of the recording's name.
        :param task_identifier: The task's part of the recording's name.
        :param task: What the task does, as the scene picker labels it.
        :param fields: The other fields of the demo.
        :return: The demo.
        :raises BundleNotFound: When the directory holds no bundle.
        :raises EnvironmentBundleHoldsRobot: When the bundle holds a robot model.
        """
        environment = BundleEnvironment(directory)
        scene = environment.contents.scene
        if any(model.robot for model in scene.models):
            raise EnvironmentBundleHoldsRobot(environment.directory)
        return cls(
            environment=environment,
            robot=robot,
            name=NAME_PART_SEPARATOR.join(
                (environment.directory.name, robot_name, task_identifier)
            ),
            task=task,
            environment_name=scene.environment_name or scene.name,
            presentation=ScenePresentation.of_bundle(environment.directory),
            **fields,
        )

    # %% running

    def scene_name(self) -> str:
        """
        :return: The validated name the recording is saved under.
        :raises InvalidSceneName: When the name is no bundle name.
        """
        return SceneName(self.name).text

    def name_to_record_under(self) -> str:
        """
        :return: The validated name the run is saved under, checked before it runs.
        :raises InvalidSceneName: When the name is no bundle name.
        :raises SceneNameTaken: When the local scenes directory already holds the name
            and the run does not :attr:`adds_a_run`.
        """
        name = self.scene_name()
        if (self.locations.local_scenes / name).exists() and not self.adds_a_run:
            raise SceneNameTaken(name)
        return name

    def build_world(self) -> World:
        """
        :return: A new world holding the environment and the robot.
        """
        return self.environment.create_world(robots=[self.robot])

    def build_context(self, world: World) -> TaskContext:
        """
        :param world: The world built for the task.
        :return: The context naming the robot of the task.
        """
        robot = world.get_semantic_annotations_by_type(
            self.robot.semantic_annotation_type
        )[0]
        return TaskContext(world=world, robot=robot)

    def performance(self, context: TaskContext) -> PlanPerformance:
        """
        :param context: The world and the robot the task is performed with.
        :return: How the task's plans are performed.
        """
        return PlanPerformance(
            execution=self.execution,
            collision_avoidance=self.collision_avoidance,
        )

    def on_live_started(self, visualization: LiveVisualization) -> None:
        """
        Called after the live server starts and before any plan performs a motion.

        :param visualization: The started visualization publishing this run.
        """

    def on_record_saving(self) -> None:
        """
        Called once the run is over and its robot released, as :attr:`execution`
        detached, and before the plans it performed are stored, which takes long.
        """

    def on_refused(self, refusal: str) -> None:
        """
        Called when :attr:`execution` found its robot not ready, before it reached the
        robot; the run ends without building its world or performing anything.

        :param refusal: What is missing and what to do, as a person reads it.
        """

    def run(self) -> Optional[SavedScene]:
        """
        Perform the task while it is published and recorded, measure the outcome and
        save the recording with its measurements.

        A run stepping through its queries is published from before its setup, so it can
        be held at the first query it asks. The world is built while :attr:`execution`
        reaches the robot. The plans it performed are stored once the robot is released.

        :return: The saved bundle; None when the robot was not ready, so nothing was
            performed or saved.
        :raises InvalidSceneName: When the name is no bundle name.
        :raises SceneNameTaken: When the local scenes directory already holds the name
            and the run does not :attr:`adds_a_run`.
        :raises TaskFailed: When the measured outcome is not the task's; nothing is
            saved then.
        """
        name = self.name_to_record_under()
        self.stepper = None
        with self.execution.attached_when_ready() as refusal:
            if refusal is not None:
                self.on_refused(refusal)
                return None
            pending, metrics = self.record(name)
        self.on_record_saving()
        pending.store()
        self.complete(pending.run, metrics)
        return SavedScene(scene=pending.run.scene)

    def record(self, name: str) -> tuple[PendingPlans, AcceptanceMetrics]:
        """
        Perform the task while it is published and recorded, measure the outcome and
        save the recording, while :attr:`execution` reaches the robot.

        :param name: The validated name the recording is saved under.
        :return: The saved run with the plans it performed, still to be stored, and its
            measurements.
        :raises TaskFailed: When the measured outcome is not the task's.
        """
        world = self.build_world()
        prepared = None if self.step_queries else self.prepare(world)
        visualization = LiveVisualization(
            world=world, port=self.port, locations=self.locations
        ).start()
        try:
            self.on_live_started(visualization)
            with self.stepping(visualization):
                if prepared is None:
                    prepared = self.prepare(world)
                context, plans = prepared
                self.enter_phase(StepPhase.EXECUTION)
                performance = self.performance(context)
                with self.execution.running(world):
                    for plan in plans:
                        self.perform_plan(
                            plan,
                            context,
                            performance,
                            lambda: self.executor_extensions(visualization),
                        )
            metrics = self.measure(context)
            visualization.publisher.finish_recording()
            pending = visualization.publisher.save_recording_without_plans(
                RecordingSaveRequest(
                    name=name,
                    destination=SceneDestination.LOCAL,
                    environment=self.environment_name,
                    task=self.task,
                    adds_a_run=self.adds_a_run,
                )
            )
        finally:
            visualization.stop()
        return pending, metrics

    def executor_extensions(
        self, visualization: LiveVisualization
    ) -> List[ExecutorExtension]:
        """
        :param visualization: The started visualization publishing this run.
        :return: New extensions of the executor of a plan: publishing its progress, and
            stepping through its queries when the run does.
        """
        extensions: List[ExecutorExtension] = [visualization.progress()]
        if self.stepper is not None:
            extensions.append(self.stepper.progress())
        return extensions

    def perform_plan(
        self,
        plan: StatechartNode,
        context: TaskContext,
        performance: PlanPerformance,
        extensions: Callable[[], List[ExecutorExtension]],
    ) -> None:
        """
        Perform one of the task's plans until it ended.

        :param plan: The plan.
        :param context: The world and the robot the task is performed with.
        :param performance: How the task's plans are performed.
        :param extensions: Gives new extensions of the executor of a plan.
        """
        performance.perform(plan, context, extensions())

    def prepare(self, world: World) -> Tuple[TaskContext, List[StatechartNode]]:
        """
        Set the task up in its world and build its plans; while the run steps through
        its queries, the plans' open parts are grounded step by step.

        :param world: The world built for the task.
        :return: The context and the plans to perform.
        """
        self.enter_phase(StepPhase.SETUP)
        context = self.build_context(world)
        if self.stepper is not None:
            context.query_backend = self.stepper.resolving(context.query_backend)
        self.enter_phase(StepPhase.EXPANSION)
        return context, self.build_plans(context)

    @contextmanager
    def stepping(self, visualization: LiveVisualization) -> Iterator[None]:
        """
        Step through the queries the run asks until the block ends, when it steps
        through them. The recording's clock stands still while the run is held, and the
        queries it is held at are let go when the visualization stops.

        :param visualization: The started visualization publishing this run.
        """
        if not self.step_queries:
            yield
            return
        self.stepper = QueryStepper(
            publisher=visualization.publisher,
            task=self.task,
            control=HeldQueries(stepping=self.holds_at_queries),
        )
        visualization.publisher.recording.clock = self.stepper.control.running_time
        visualization.when_stopped(self.stepper.control.release)
        with self.stepper.watching():
            yield

    def enter_phase(self, phase: StepPhase) -> None:
        """
        :param phase: What the run does from now on, said of the queries it asks.
        """
        if self.stepper is not None:
            self.stepper.phase = phase

    def complete(self, run: StoredRun, metrics: AcceptanceMetrics) -> None:
        """
        Give the saved bundle the demo's view and the environment's appearance, store
        the measurements beside it, and keep the documents as they end up with the run
        in the bundle's plan database.

        :param run: The saved run.
        :param metrics: The measured outcome.
        """
        bundle = run.database.bundle
        scene_file = DocumentFile(bundle / BundleFile.SCENE)
        scene = scene_file.read(SceneDescription)
        if self.presentation is not None:
            self.presentation.apply(scene, bundle)
        if self.camera is not None:
            scene.camera = self.camera
        scene_file.write(scene)
        DocumentFile(bundle / BundleFile.ACCEPTANCE_METRICS).write(metrics)
        run.database.refresh_documents(run.identifier)

    # %% publishing

    def publish(self, shared: Path) -> SavedScene:
        """
        Copy the recording a previous run saved into a shared scenes directory, without
        running the task again; the copy is staged beside its destination and moved in
        whole, so the shared directory never holds half a bundle.

        :param shared: The shared scenes directory.
        :return: The published bundle.
        :raises RecordingNotSaved: When no run saved the recording yet.
        """
        name = self.scene_name()
        saved = self.locations.local_scenes / name
        scene_file = DocumentFile(saved / BundleFile.SCENE)
        if not scene_file.path.is_file():
            raise RecordingNotSaved(name)
        shared.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f"{PUBLISHING_PREFIX}{name}.", dir=shared)
        )
        shutil.copytree(saved, staging / name)
        target = shared / name
        if target.exists():
            target.rename(staging / f"{name}{REPLACED_SUFFIX}")
        (staging / name).rename(target)
        shutil.rmtree(staging)
        scene = scene_file.read(SceneDescription)
        SceneIndexFile(shared, SceneSource.SHARED).register(
            SceneIndexEntry(
                name=name,
                robot=scene.robot_name
                or ("" if scene.robot is None else scene.robot.name),
                environment=scene.environment_name,
                task=scene.task,
            )
        )
        return SavedScene(scene=name)


# %% command line


class TaskArgument(StrEnum):
    """
    The arguments of a task demo's command.
    """

    PUBLISH = "--publish"
    """
    Copy the saved recording into this shared scenes directory instead of running.
    """

    PORT = "--port"
    """
    The port the run is published on.
    """

    NAME = "--name"
    """
    The name the recording is saved under, instead of the demo's own.
    """

    STEP_QUERIES = "--step-queries"
    """
    Publish each query the run asks of its own as a query step, held at it until the
    viewer moves it on.
    """

    NO_HOLD = "--no-hold"
    """
    With query steps, start the run without holding it at its queries.
    """

    ADD_RUN = "--add-run"
    """
    Record the task again into the saved bundle of the same name, adding the run to its
    plan database.
    """


@dataclass(frozen=True)
class TaskCommand:
    """
    The command line of one task demo: run it, or publish its saved recording.
    """

    demo: RobotTaskDemo
    """
    The demo.
    """

    def main(self, arguments: Sequence[str]) -> Optional[SavedScene]:
        """
        :param arguments: The command's arguments.
        :return: The saved or published bundle; None for a run its robot refused.
        """
        parser = ArgumentParser(description=self.demo.task)
        parser.add_argument(
            TaskArgument.PUBLISH,
            type=Path,
            default=None,
            help="copy the saved recording into this shared scenes directory",
        )
        parser.add_argument(
            TaskArgument.PORT,
            type=int,
            default=self.demo.port,
            help="the port the run is published on",
        )
        parser.add_argument(
            TaskArgument.NAME,
            default=self.demo.name,
            help="the name the recording is saved under",
        )
        parser.add_argument(
            TaskArgument.STEP_QUERIES,
            action="store_true",
            default=self.demo.step_queries,
            help="publish each query the run asks as a step, held until moved on",
        )
        parser.add_argument(
            TaskArgument.NO_HOLD,
            action="store_false",
            dest="holds_at_queries",
            default=self.demo.holds_at_queries,
            help="with query steps, start without holding at the queries",
        )
        parser.add_argument(
            TaskArgument.ADD_RUN,
            action="store_true",
            help="record a further run into the saved bundle of the same name",
        )
        options = parser.parse_args(list(arguments))
        self.demo.name = options.name
        if options.publish is not None:
            return self.demo.publish(options.publish)
        self.demo.port = options.port
        self.demo.step_queries = options.step_queries
        self.demo.holds_at_queries = options.holds_at_queries
        self.demo.adds_a_run = options.add_run
        return self.demo.run()
