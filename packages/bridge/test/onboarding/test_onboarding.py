"""
Onboarding a coraplex demo into a scene bundle, and its command.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing_extensions import List, Optional

from cramera_bridge.command_line import OnboardArgument, OnboardCommand, onboard
from cramera_bridge.database.runs import PlanDatabase, RunDocument, SavedRun
from cramera_bridge.demo_script import DemoScript
from cramera_bridge.documents import DocumentFile
from cramera_bridge.exceptions import (
    DemoStartedNoWorld,
    SceneNameTaken,
    SharedScenesUnavailable,
)
from cramera_bridge.onboarding import DemoOnboarding
from cramera.locations import DataLocations
from cramera_protocol.bundle.files import BundleFile, ScenesDirectoryFile
from cramera_protocol.bundle.index import (
    InvalidSceneName,
    ReservedSceneName,
    SceneIndex,
)
from cramera_protocol.bundle.scene import DetectedEventKind, SceneDescription
from cramera_protocol.bundle.trajectory import Trajectory
from cramera_protocol.live_messages import SceneDestination
from cramera_protocol.viewer import EnvironmentVariable
from segmind.datastructures.events import SupportEvent

from ..dataset.onboarding.sliding_box_demo import (
    BOX_HEIGHTS,
    FAILURE_ARGUMENT,
    FAILURE_STATUS,
)
from .conftest import ONBOARDING_DATASET, SLIDING_BOX_DEMO

SCENE_NAME = "sliding_box"
"""
The name the onboarded runs are saved under.
"""

TASK = "Lower a box onto a table"
"""
The task label of the onboarded runs.
"""

DEMO_OUTPUT_VARIABLE = "CRAMERA_TEST_DEMO_OUTPUT"
"""
Where the demo without a visualization writes what it was given.
"""

# %% onboarding


def onboarding(locations: DataLocations, *arguments: str, **changes) -> DemoOnboarding:
    """
    :param locations: Where the bundle is saved.
    :param arguments: The arguments handed to the demo.
    :param changes: Fields that differ from the test onboarding.
    :return: An onboarding of the sliding box demo.
    """
    fields = dict(
        demo=DemoScript(SLIDING_BOX_DEMO, list(arguments)),
        name=SCENE_NAME,
        locations=locations,
        task=TASK,
    )
    fields.update(changes)
    return DemoOnboarding(**fields)


def saved_scene(directory, name: str = SCENE_NAME) -> SceneDescription:
    """
    :param directory: A scenes directory.
    :param name: The bundle's name.
    :return: The scene description of the saved bundle.
    """
    return DocumentFile(directory / name / BundleFile.SCENE).read(SceneDescription)


def test_a_demo_run_is_saved_as_a_named_bundle(locations):
    saved = onboarding(locations).run()
    scene = saved_scene(locations.local_scenes)
    trajectory = DocumentFile(
        locations.local_scenes / SCENE_NAME / scene.trajectory
    ).read(Trajectory)
    index = DocumentFile(locations.local_scenes / ScenesDirectoryFile.INDEX).read(
        SceneIndex
    )
    assert saved.scene == SCENE_NAME
    assert scene.name == SCENE_NAME
    assert scene.task == TASK
    heights = [next(iter(poses.values())).z for poses in trajectory.objects]
    assert (heights[0], heights[-1]) == (BOX_HEIGHTS[0], BOX_HEIGHTS[-1])
    assert SCENE_NAME in [entry.name for entry in index.scenes]
    assert not (locations.local_scenes / ReservedSceneName.RECORDING).exists()


def test_a_run_without_detection_lists_no_events(locations):
    onboarding(locations).run()
    assert saved_scene(locations.local_scenes).detected_events == []


def test_detected_events_are_listed_in_the_bundle(locations):
    onboarding(locations, detect_events=True).run()
    event_types = {
        event.event_type
        for event in saved_scene(locations.local_scenes).detected_events
    }
    assert SupportEvent.__name__ in event_types


def test_listed_events_are_placed_on_the_bundles_frames(locations):
    onboarding(locations, detect_events=True).run()
    scene = saved_scene(locations.local_scenes)
    frame_count = (
        DocumentFile(locations.local_scenes / SCENE_NAME / scene.trajectory)
        .read(Trajectory)
        .frame_count()
    )
    [support, *_] = [
        event
        for event in scene.detected_events
        if event.event_type == SupportEvent.__name__
    ]
    assert support.kind is DetectedEventKind.SUPPORT
    assert 0 <= support.frame < frame_count
    assert support.keys


def test_a_run_is_saved_into_the_shared_directory(locations):
    onboarding(locations, destination=SceneDestination.SHARED).run()
    assert saved_scene(locations.shared_scenes).name == SCENE_NAME
    assert not (locations.local_scenes / SCENE_NAME).exists()


def test_a_failing_demo_releases_its_visualizations(locations):
    failing = onboarding(locations, FAILURE_ARGUMENT, detect_events=True)
    with pytest.raises(SystemExit) as failure:
        failing.run()
    assert failure.value.code == FAILURE_STATUS
    assert [visualization.server for visualization in failing.visualizations] == [None]
    assert [
        visualization.detections.is_watching for visualization in failing.visualizations
    ] == [False]
    assert not (locations.local_scenes / SCENE_NAME).exists()


# %% runs kept in the plan database


def saved_run_tasks(directory, name: str = SCENE_NAME) -> list:
    """
    :param directory: A scenes directory.
    :param name: The bundle's name.
    :return: The task of every run the bundle's plan database keeps, oldest first.
    """
    database = PlanDatabase(directory / name)
    with Session(database.engine()) as session:
        return list(
            session.scalars(select(SavedRun.task).order_by(SavedRun.identifier)).all()
        )


def test_an_onboarded_run_is_kept_in_the_plan_database(locations):
    onboarding(locations).run()

    assert saved_run_tasks(locations.local_scenes) == [TASK]


def test_onboarding_again_adds_a_run_to_the_bundle(locations):
    onboarding(locations).run()

    onboarding(locations, task="again", adds_a_run=True).run()

    assert saved_scene(locations.local_scenes).task == "again"
    assert saved_run_tasks(locations.local_scenes) == [TASK, "again"]


def test_a_run_keeps_the_events_detected_after_it_was_saved(locations):
    onboarding(locations, detect_events=True).run()

    database = PlanDatabase(locations.local_scenes / SCENE_NAME)
    with Session(database.engine()) as session:
        kept = session.scalar(
            select(RunDocument.content).where(
                RunDocument.file == BundleFile.SCENE.value
            )
        )
    assert kept == (locations.local_scenes / SCENE_NAME / BundleFile.SCENE).read_text()


@dataclass
class PlansSeen:
    """
    What a publisher reports of the plans it saw performed.
    """

    plans: List[str]
    """
    Stand-ins for the performed plans.
    """

    def performed_plans(self) -> List[str]:
        """
        :return: The stand-ins.
        """
        return self.plans


@dataclass
class RunningPublisher:
    """
    A publisher with the plans it saw performed.
    """

    plan: PlansSeen
    """
    The plans it saw performed.
    """


@dataclass
class PublishingVisualization:
    """
    A visualization that publishes plans until it is stopped.
    """

    plans: List[str]
    """
    Stand-ins for the plans its publisher saw performed.
    """

    publisher: Optional[RunningPublisher] = None
    """
    Its publisher while it runs.
    """

    def __post_init__(self) -> None:
        self.publisher = RunningPublisher(PlansSeen(self.plans))

    def stop(self) -> None:
        """
        Stop publishing.
        """
        self.publisher = None


def test_only_the_plans_of_the_saved_recording_are_kept(locations):
    demo = onboarding(locations)
    demo.visualizations.extend(
        [PublishingVisualization(["first"]), PublishingVisualization(["second"])]
    )

    demo.stop_watching()
    demo.stop_watching()

    assert demo.performed_plans == ["second"]


# %% refusals before the demo runs


def test_a_taken_name_is_refused_before_the_demo_runs(locations):
    (locations.shared_scenes / SCENE_NAME).mkdir(parents=True)
    refused = onboarding(locations)
    with pytest.raises(SceneNameTaken):
        refused.run()
    assert refused.visualizations == []


def test_an_invalid_name_is_refused(locations):
    with pytest.raises(InvalidSceneName):
        onboarding(locations, name="not a name").run()


def test_an_unconfigured_shared_directory_is_refused(tmp_path):
    local_only = DataLocations(data=tmp_path / "data", shared_scenes=None)
    with pytest.raises(SharedScenesUnavailable):
        onboarding(local_only, destination=SceneDestination.SHARED).run()


def test_a_demo_without_a_visualization_is_refused(locations, monkeypatch, tmp_path):
    monkeypatch.setenv(DEMO_OUTPUT_VARIABLE, str(tmp_path / "output.txt"))
    without_world = onboarding(
        locations,
        demo=DemoScript(ONBOARDING_DATASET.parent / "live" / "demo_script.py"),
    )
    with pytest.raises(DemoStartedNoWorld):
        without_world.run()


# %% command


def test_the_command_describes_the_onboarding(locations):
    command = OnboardCommand.parse(
        [
            str(SLIDING_BOX_DEMO),
            OnboardArgument.NAME,
            SCENE_NAME,
            OnboardArgument.SHARED,
            OnboardArgument.TASK,
            TASK,
            OnboardArgument.DETECT_EVENTS,
            OnboardArgument.ADD_RUN,
            "--",
            FAILURE_ARGUMENT,
        ],
        locations,
    )
    assert command.onboarding == DemoOnboarding(
        demo=DemoScript(SLIDING_BOX_DEMO.resolve(), [FAILURE_ARGUMENT]),
        name=SCENE_NAME,
        locations=locations,
        destination=SceneDestination.SHARED,
        task=TASK,
        detect_events=True,
        adds_a_run=True,
    )


def test_the_command_saves_into_the_configured_data_directory(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv(EnvironmentVariable.DATA, str(data))
    monkeypatch.delenv(EnvironmentVariable.SCENES, raising=False)
    onboard([str(SLIDING_BOX_DEMO), OnboardArgument.NAME, SCENE_NAME])
    local_scenes = DataLocations(data=data, shared_scenes=None).local_scenes
    assert saved_scene(local_scenes).name == SCENE_NAME
