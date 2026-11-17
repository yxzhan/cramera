"""
Robot task demos: running, measuring, saving and publishing a task.
"""

from __future__ import annotations

import shutil
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing_extensions import Iterator, List, Optional

from cramera.locations import DataLocations
from cramera_bridge.database.native_plans import NativePlans
from cramera_bridge.database.runs import (
    PlanDatabase,
    RecordedPlan,
    RunDocument,
    SavedRun,
)
from cramera_bridge.demos.navigation import BarrierEnvironment
from cramera_bridge.documents import DocumentFile
from cramera_bridge.execution import KinematicExecution
from cramera_bridge.exceptions import (
    EnvironmentBundleHoldsRobot,
    RecordingNotSaved,
    SceneNameTaken,
    TaskFailed,
)
from cramera_protocol.bundle.acceptance import AcceptanceMetrics
from cramera_protocol.bundle.files import BundleFile, ScenesDirectoryFile
from cramera_protocol.bundle.index import InvalidSceneName, SceneIndex
from cramera_protocol.bundle.scene import Camera, SceneDescription
from cramera_protocol.geometry import Vector3
from cramera_protocol.viewer import EnvironmentVariable
from semantic_digital_twin.specifications.robots import RobotSpecification
from semantic_digital_twin.datastructures.definitions import TorsoState
from semantic_digital_twin.robots.pr2 import PR2

from cramera_bridge.robot_task import NAME_PART_SEPARATOR, TaskArgument, TaskCommand

from ..dataset.bundles import ANNOTATED_BUNDLE
from ..dataset.demos import RaisedThenMovedTorsoDemo, TorsoDemo, TorsoMeasurement

SCENE_NAME = "barrier_pr2_torso"
"""
The name the test demos save their recording under.
"""

TASK = "PR2: raise the torso"
"""
The task label of the test demos.
"""

BARRIER_DISTANCE = 2.0
"""
How far the barrier's target lies, in metres.
"""

# %% fixtures


@pytest.fixture
def locations(tmp_path) -> DataLocations:
    """
    :return: Data locations below the test's directory.
    """
    return DataLocations(data=tmp_path / "data", shared_scenes=tmp_path / "shared")


def torso_demo(
    locations: DataLocations, demo_type: type = TorsoDemo, **changes
) -> TorsoDemo:
    """
    :param locations: Where the recording is saved.
    :param demo_type: The demo class.
    :param changes: Fields that differ from the test task.
    :return: A PR2 moving its torso.
    """
    fields = dict(
        environment=BarrierEnvironment(distance=BARRIER_DISTANCE),
        robot=RobotSpecification(semantic_annotation_type=PR2),
        name=SCENE_NAME,
        task=TASK,
        environment_name="Barrier floor",
        camera=Camera(position=Vector3(3.0, 0.0, 2.0), target=Vector3(0.0, 0.0, 0.5)),
        locations=locations,
        port=0,
    )
    fields.update(changes)
    return demo_type(**fields)


@pytest.fixture(scope="module")
def saved_run(tmp_path_factory):
    """
    :return: The demo and the saved scene of one finished task.
    """
    root = tmp_path_factory.mktemp("torso")
    locations = DataLocations(data=root / "data", shared_scenes=root / "shared")
    demo = torso_demo(locations)
    return demo, demo.run()


# %% running


def test_a_finished_task_is_saved_with_its_measurements(saved_run):
    demo, saved = saved_run
    bundle = demo.locations.local_scenes / saved.scene
    scene = DocumentFile(bundle / BundleFile.SCENE).read(SceneDescription)
    metrics = DocumentFile(bundle / BundleFile.ACCEPTANCE_METRICS).read(
        AcceptanceMetrics
    )
    assert saved.scene == SCENE_NAME
    assert scene.task == TASK
    assert scene.environment_name == demo.environment_name
    assert scene.camera == demo.camera
    assert TorsoMeasurement.TORSO_ERROR.value in metrics.measurements


def test_the_plans_of_a_task_run_in_order_and_are_all_recorded(locations):
    demo = torso_demo(locations, RaisedThenMovedTorsoDemo)
    saved = demo.run()
    bundle = locations.local_scenes / saved.scene
    scene = DocumentFile(bundle / BundleFile.SCENE).read(SceneDescription)
    plans = demo.build_plans(demo.build_context(demo.build_world()))
    assert len(scene.plan_trees) == len(plans)
    first, second = scene.plan_trees
    assert first.start is not None and second.start is not None
    assert first.end < second.start


def test_the_saved_scene_is_listed(saved_run):
    demo, saved = saved_run
    index = DocumentFile(demo.locations.local_scenes / ScenesDirectoryFile.INDEX).read(
        SceneIndex
    )
    assert saved.scene in [entry.name for entry in index.scenes]


def test_a_saved_name_is_not_run_again(saved_run):
    demo, _ = saved_run
    with pytest.raises(SceneNameTaken):
        demo.run()


def saved_run_count(locations: DataLocations) -> int:
    """
    :return: How many runs the saved bundle's plan database keeps.
    """
    database = PlanDatabase(locations.local_scenes / SCENE_NAME)
    with Session(database.engine()) as session:
        return len(session.scalars(select(SavedRun)).all())


def test_a_task_run_keeps_its_measurements_in_the_plan_database(saved_run):
    demo, saved = saved_run
    bundle = demo.locations.local_scenes / saved.scene
    database = PlanDatabase(bundle)

    with Session(database.engine()) as session:
        kept = session.scalar(
            select(RunDocument.content).where(
                RunDocument.file == BundleFile.ACCEPTANCE_METRICS.value
            )
        )

    assert kept == (bundle / BundleFile.ACCEPTANCE_METRICS).read_text()


@pytest.mark.skipif(
    not NativePlans.mapping_is_built(),
    reason="CRAM's database mapping is not generated in this workspace",
)
def test_a_task_run_keeps_the_plans_it_performed(saved_run):
    demo, saved = saved_run
    database = PlanDatabase(demo.locations.local_scenes / saved.scene)
    performed = demo.build_plans(demo.build_context(demo.build_world()))

    with Session(database.engine()) as session:
        numbers = session.scalars(
            select(RecordedPlan.number).order_by(RecordedPlan.number)
        ).all()

    assert numbers == list(range(1, len(performed) + 1))


@dataclass
class WatchedExecution(KinematicExecution):
    """
    A kinematic execution that knows whether the task is attached to it.
    """

    is_attached: bool = False
    """
    Whether the task's block of :meth:`attached` runs.
    """

    @contextmanager
    def attached(self) -> Iterator[None]:
        with ExitStack() as attached:
            self.is_attached = True
            attached.callback(setattr, self, "is_attached", False)
            yield


@dataclass
class SaveWatchingTorsoDemo(TorsoDemo):
    """
    The torso demo noting what holds once its record starts being saved.
    """

    execution: WatchedExecution = field(default_factory=WatchedExecution)

    seen_at_saving: List[tuple[bool, int]] = field(default_factory=list)
    """
    For each time the record started being saved: whether the execution was still
    attached, and how many of the run's plans the plan database held.
    """

    def on_record_saving(self) -> None:
        self.seen_at_saving.append(
            (self.execution.is_attached, stored_plan_count(self.locations))
        )


def stored_plan_count(locations: DataLocations) -> int:
    """
    :return: How many plans the saved bundle's plan database keeps.
    """
    database = PlanDatabase(locations.local_scenes / SCENE_NAME)
    with Session(database.engine()) as session:
        return len(session.scalars(select(RecordedPlan)).all())


@pytest.mark.skipif(
    not NativePlans.mapping_is_built(),
    reason="CRAM's database mapping is not generated in this workspace",
)
def test_a_run_stores_its_plans_only_once_the_robot_is_released(locations):
    demo = torso_demo(locations, SaveWatchingTorsoDemo)

    demo.run()

    assert demo.seen_at_saving == [(False, 0)]
    assert stored_plan_count(locations) == 1


REFUSAL = "The real robot is not ready: the arm watchdog is not running."
"""
Why the stand-in of a real robot refuses a run.
"""


@dataclass
class RefusingExecution(KinematicExecution):
    """
    An execution whose robot's check refuses the run before anything is attached.
    """

    @contextmanager
    def attached_when_ready(self) -> Iterator[Optional[str]]:
        yield REFUSAL


@dataclass
class RefusalNotingTorsoDemo(TorsoDemo):
    """
    The torso demo noting the refusals it is told of and whether it recorded anything.
    """

    execution: RefusingExecution = field(default_factory=RefusingExecution)

    refusals: List[str] = field(default_factory=list)
    """
    The refusals the demo was told of, in order.
    """

    recorded: bool = False
    """
    Whether the demo began to build its world and perform its plans.
    """

    def on_refused(self, refusal: str) -> None:
        self.refusals.append(refusal)

    def record(self, name: str):
        self.recorded = True
        return super().record(name)


def test_a_run_its_robot_refused_performs_nothing_saves_nothing_and_says_why(
    locations,
):
    demo = torso_demo(locations, RefusalNotingTorsoDemo)

    assert demo.run() is None
    assert demo.refusals == [REFUSAL]
    assert not demo.recorded
    assert not (locations.local_scenes / SCENE_NAME).exists()


def test_an_execution_that_attaches_says_it_is_ready():
    execution = WatchedExecution()

    with execution.attached_when_ready() as refusal:
        assert refusal is None
        assert execution.is_attached


def test_a_saved_task_runs_again_as_a_further_run(locations):
    torso_demo(locations).run()

    torso_demo(locations, adds_a_run=True).run()

    assert saved_run_count(locations) == 2


def test_a_saved_run_is_published_whole(saved_run, tmp_path):
    demo, saved = saved_run
    shared = tmp_path / "shared"
    published = demo.publish(shared)
    assert published == saved
    assert (shared / saved.scene / BundleFile.SCENE).is_file()
    assert (shared / saved.scene / BundleFile.ACCEPTANCE_METRICS).is_file()
    assert sorted(path.name for path in shared.iterdir()) == sorted(
        [saved.scene, ScenesDirectoryFile.INDEX.value]
    )


def test_publishing_twice_replaces_the_shared_copy(saved_run, tmp_path):
    demo, saved = saved_run
    shared = tmp_path / "shared"
    demo.publish(shared)
    demo.publish(shared)
    assert sorted(path.name for path in shared.iterdir()) == sorted(
        [saved.scene, ScenesDirectoryFile.INDEX.value]
    )


def test_a_demo_saves_where_the_environment_configures(monkeypatch, tmp_path):
    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "configured"))
    fields = dict(
        environment=BarrierEnvironment(distance=BARRIER_DISTANCE),
        robot=RobotSpecification(semantic_annotation_type=PR2),
        name=SCENE_NAME,
        task=TASK,
    )
    assert TorsoDemo(**fields).locations.data == tmp_path / "configured"


# %% environment bundles

ROBOT_NAME = "pr2"
"""
The robot's part of a bundle demo's recording name.
"""

TASK_IDENTIFIER = "torso"
"""
The task's part of a bundle demo's recording name.
"""


@pytest.fixture
def environment_bundle(tmp_path):
    """
    :return: A copy of the annotated bundle without its robot model and without its
        free objects, whose mesh files the dataset does not hold.
    """
    bundle = shutil.copytree(ANNOTATED_BUNDLE, tmp_path / "lab")
    scene_file = DocumentFile(bundle / BundleFile.SCENE)
    scene = scene_file.read(SceneDescription)
    scene_file.write(
        replace(
            scene,
            models=[model for model in scene.models if not model.robot],
            robot=None,
            objects=[],
        )
    )
    return bundle


def bundle_demo(directory, locations) -> TorsoDemo:
    """
    :return: The torso task in the environment of a bundle.
    """
    return TorsoDemo.in_environment_bundle(
        directory,
        RobotSpecification(semantic_annotation_type=PR2),
        ROBOT_NAME,
        TASK_IDENTIFIER,
        TASK,
        locations=locations,
        port=0,
    )


def test_a_bundle_demo_is_named_and_labelled_after_its_environment(
    environment_bundle, locations
):
    demo = bundle_demo(environment_bundle, locations)
    environment = DocumentFile(environment_bundle / BundleFile.SCENE).read(
        SceneDescription
    )
    assert demo.name == NAME_PART_SEPARATOR.join(
        (environment_bundle.name, ROBOT_NAME, TASK_IDENTIFIER)
    )
    assert demo.environment_name == environment.environment_name


def test_a_bundle_demo_records_with_its_environments_appearance(
    environment_bundle, locations
):
    demo = bundle_demo(environment_bundle, locations)
    saved = demo.run()
    scene = DocumentFile(locations.local_scenes / saved.scene / BundleFile.SCENE).read(
        SceneDescription
    )
    environment = DocumentFile(environment_bundle / BundleFile.SCENE).read(
        SceneDescription
    )
    assert scene.environment_name == environment.environment_name
    assert scene.rendering == environment.rendering
    assert scene.camera == environment.camera
    assert (
        locations.local_scenes / saved.scene / scene.rendering.environment
    ).is_file()


def test_a_bundle_holding_a_robot_is_no_environment(locations):
    with pytest.raises(EnvironmentBundleHoldsRobot):
        bundle_demo(ANNOTATED_BUNDLE, locations)


# %% refusals


def test_an_unsaved_run_cannot_be_published(locations, tmp_path):
    with pytest.raises(RecordingNotSaved):
        torso_demo(locations).publish(tmp_path / "shared")


def test_an_invalid_name_is_refused_before_the_run(locations):
    with pytest.raises(InvalidSceneName):
        torso_demo(locations, name="not a name").run()


def test_a_missed_target_fails_and_saves_nothing(locations, monkeypatch):
    demo = torso_demo(locations)
    unreachable = TorsoDemo.measure
    monkeypatch.setattr(
        demo,
        "measure",
        lambda context: unreachable(
            torso_demo(locations, torso_state=TorsoState.LOW), context
        ),
    )
    with pytest.raises(TaskFailed):
        demo.run()
    assert not (locations.local_scenes / SCENE_NAME).exists()


# %% command line


def test_the_command_publishes_a_saved_run(saved_run, tmp_path):
    demo, saved = saved_run
    shared = tmp_path / "shared"
    published = TaskCommand(demo).main([TaskArgument.PUBLISH, str(shared)])
    assert published == saved


def test_the_command_runs_on_the_given_port(locations):
    demo = torso_demo(locations)
    saved = TaskCommand(demo).main([TaskArgument.PORT, "0"])
    assert saved.scene == SCENE_NAME


def test_the_command_saves_under_the_given_name(locations):
    demo = torso_demo(locations)
    saved = TaskCommand(demo).main(
        [TaskArgument.PORT, "0", TaskArgument.NAME, "barrier_pr2_named"]
    )
    assert saved.scene == "barrier_pr2_named"
    assert (locations.local_scenes / "barrier_pr2_named").is_dir()


def test_the_command_steps_through_the_queries_without_holding(locations):
    demo = torso_demo(locations)
    TaskCommand(demo).main(
        [
            TaskArgument.PORT,
            "0",
            TaskArgument.STEP_QUERIES,
            TaskArgument.NO_HOLD,
        ]
    )
    assert demo.step_queries
    assert not demo.holds_at_queries
    assert demo.stepper is not None


def test_the_command_adds_a_further_run(locations):
    torso_demo(locations).run()

    TaskCommand(torso_demo(locations)).main(
        [TaskArgument.PORT, "0", TaskArgument.ADD_RUN]
    )

    assert saved_run_count(locations) == 2
