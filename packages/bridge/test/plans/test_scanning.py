"""
Scanning the scene for a plan's objects: Tracy's simulated camera, rendered from a scene
holding three cubes, fills a plan whose twin holds none with a copy of the cube model of
each color seen, where it was seen.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from importlib.util import find_spec

import numpy as np
import pytest

from coraplex.exceptions import PerceptionSourceDidNotAnswer
from coraplex.perception import Detection, PerceptionQuery
from cramera_bridge.execution import PERCEPTION_ENGINE_VARIABLE
from cramera_bridge.plans.objects import LIBRARY_VARIABLE, ObjectLibrary
from cramera_bridge.plans.scanning import SceneScanner
from cramera_bridge.plans.world import PlannedGraspable
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.detection import DetectionGrounding, SceneScan, SceneScanOrder
from cramera_protocol.plan_builder import DemoDocument, PlanDocument, PlannedObject
from cramera_protocol.robot_profile import PerceptionSettings
from krrood_spatial_math.spatial_types import Pose
from rclpy.executors import MultiThreadedExecutor
from semantic_digital_twin.robots.tracy import Tracy
from semantic_digital_twin.world_description.color_names import ColorName
from semantic_digital_twin.world_description.geometry import Scale

from ..dataset.bundles import DATASET

pytestmark = pytest.mark.skipif(
    not Tracy.is_description_installed(),
    reason="iai_tracy_description is not installed",
)

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube, each named by its color.
"""

TRACY_CUBES = DATASET / "plans" / "tracy_cubes.json"
"""
Tracy with a red, a yellow and a blue cube on its table.
"""

SCANNED_POSITION_TOLERANCE = 0.01
"""
How far, in metres, a cube found may lie from where it stands in the simulated scene.
"""

# %% fixtures


@pytest.fixture
def cubes() -> PlanDocument:
    """
    :return: The plan of Tracy and the three cubes on its table.
    """
    return PlanDocument.from_json(json.loads(TRACY_CUBES.read_text("utf-8")))


@pytest.fixture
def empty_twin(cubes: PlanDocument) -> PlanDocument:
    """
    :return: The plan of Tracy without objects or steps.
    """
    [robot] = cubes.robots
    return replace(cubes, objects=[], robots=[replace(robot, steps=[])])


@dataclass(frozen=True)
class ReportedColors:
    """
    A robot perception that sees one cube of each color it is given, side by side on
    Tracy's table.
    """

    colors: tuple[ColorName, ...]
    """
    The colors of the cubes seen, in the order they are reported.
    """

    def look_for(self, query: PerceptionQuery) -> list[Detection]:
        return [
            Detection(
                query.semantic_annotation,
                Pose.from_xyz_rpy(
                    0.55, 0.1 * number, 0.906, reference_frame=query.world.root
                ),
                color=color,
                size=Scale(0.05, 0.05, 0.05),
            )
            for number, color in enumerate(self.colors)
        ]

    @contextmanager
    def perceiving(self):
        yield self


def scanned(order: SceneScanOrder, colors: tuple[ColorName, ...]) -> SceneScan:
    """
    :return: The scan of the order while the robot sees one cube of each color.
    """
    scanner = SceneScanner(order)
    scanner.perception = ReportedColors(colors)
    return scanner.scan()


def model_of(scanned: PlannedObject, objects: list[PlannedObject]) -> PlannedObject:
    """
    :return: The object of ``objects`` that is a copy of the scanned object's model.
    """
    [matching] = [item for item in objects if item.model == scanned.model]
    return matching


# %% matching models


def test_a_cube_found_is_a_copy_of_the_model_of_its_color(monkeypatch, cubes):
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    scanner = SceneScanner(SceneScanOrder(demo=DemoDocument(plan=cubes)))

    for standing in cubes.objects:
        color = ColorName(standing.identifier.removesuffix("_cube"))
        found = Detection(PlannedGraspable, Pose(), color=color)
        assert scanner.matching_model(found) == standing.model


def test_a_found_object_of_a_color_no_model_shows_matches_none(monkeypatch, cubes):
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    scanner = SceneScanner(SceneScanOrder(demo=DemoDocument(plan=cubes)))

    found = Detection(PlannedGraspable, Pose(), color=ColorName.GREEN)

    assert scanner.matching_model(found) is None


def test_an_identity_taken_gets_the_next_number(cubes):
    assert SceneScanner.free_identifier("red_cube", cubes.objects) == "red_cube_2"
    assert SceneScanner.free_identifier("green_cube", cubes.objects) == "green_cube"


# %% scanning what the robot's perception reports


def test_a_scan_adds_a_copy_of_the_matching_model_and_counts_the_unmatched(
    monkeypatch, empty_twin
):
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    order = SceneScanOrder(demo=DemoDocument(plan=empty_twin))

    scan = scanned(
        order, (ColorName.RED, ColorName.WHITE, ColorName.RED, ColorName.GREEN)
    )

    assert scan.grounding is DetectionGrounding.PERCEIVED
    assert [(item.identifier, item.model) for item in scan.objects] == [
        ("red_cube", "cube_library/objects/cubes/red_cube/model.urdf"),
        ("red_cube_2", "cube_library/objects/cubes/red_cube/model.urdf"),
    ]
    assert scan.unrecognized == 2


def test_a_scan_reads_the_libraries_as_often_however_many_objects_it_finds(
    monkeypatch, empty_twin
):
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    order = SceneScanOrder(demo=DemoDocument(plan=empty_twin))
    reads = []
    collection_entries = ObjectLibrary.collection_entries
    monkeypatch.setattr(
        ObjectLibrary,
        "collection_entries",
        lambda library: reads.append(library) or collection_entries(library),
    )

    scanned(order, (ColorName.WHITE,))
    reads_for_one = len(reads)
    reads.clear()
    scanned(order, (ColorName.WHITE,) * 5)

    assert len(reads) == reads_for_one


# %% scanning through the simulated camera


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_a_scan_of_the_simulated_scene_fills_an_empty_twin_with_the_three_cubes(
    tmp_path, cubes, empty_twin
):
    order_file = tmp_path / "order.json"
    scan_file = tmp_path / "scan.json"
    order = SceneScanOrder(
        demo=DemoDocument(plan=empty_twin), simulated_objects=cubes.objects
    )
    order_file.write_text(json.dumps(order.to_json()), encoding="utf-8")

    finished = subprocess.run(
        [
            sys.executable,
            "-m",
            "cramera_bridge.plans.scanning",
            str(order_file),
            str(scan_file),
        ],
        env={
            **os.environ,
            LIBRARY_VARIABLE: str(CUBE_LIBRARY),
            PERCEPTION_ENGINE_VARIABLE: "tracy_cubes",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=600,
    )

    assert finished.returncode == 0, finished.stderr.decode()[-4000:]
    scan = SceneScan.from_json(json.loads(scan_file.read_text("utf-8")))
    assert scan.grounding is DetectionGrounding.PERCEIVED
    assert sorted(item.model for item in scan.objects) == sorted(
        item.model for item in cubes.objects
    )
    for scanned in scan.objects:
        standing = model_of(scanned, cubes.objects)
        np.testing.assert_allclose(
            scanned.pose.to_json()[:2],
            standing.pose.to_json()[:2],
            atol=SCANNED_POSITION_TOLERANCE,
        )


# %% scanning through a perception that does not answer


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_a_scan_robokudo_does_not_answer_fails_after_the_profiles_answer_timeout(
    monkeypatch, cubes
):
    from ..dataset.real_robot import RoboKudoThatNeverAnswers

    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    perception = PerceptionSettings(answer_timeout=0.5)
    order = SceneScanOrder(demo=DemoDocument(plan=cubes, perception=perception))

    with SpinningNode("robokudo_stand_in", MultiThreadedExecutor).running() as node:
        with RoboKudoThatNeverAnswers().serving(node):
            with pytest.raises(PerceptionSourceDidNotAnswer) as raised:
                SceneScanner(order).scan()

    assert raised.value.answer_timeout == timedelta(seconds=perception.answer_timeout)
