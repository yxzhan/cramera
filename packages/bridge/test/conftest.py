"""
Shared fixtures of the cramera-bridge tests.

Most tests need the CRAM workspace. Where it is missing, only the tests listed in
:data:`TESTS_WITHOUT_CRAM` are collected, so the suite skips the rest cleanly and the
pure logic still runs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cramera.locations import DataLocations
from cramera_protocol.viewer import EnvironmentVariable

CRAM_PACKAGES = ("semantic_digital_twin", "coraplex", "giskardpy", "krrood", "segmind")
"""
The CRAM packages the tests import.
"""

TESTS_WITHOUT_CRAM = (
    "test_documents.py",
    "test_cram_boundary.py",
    "test_demo_script.py",
    "database/test_runs.py",
    "laboratory/test_command.py",
    "export/test_scene_index.py",
    "plans/test_objects.py",
    "questions/test_presets_and_matching.py",
    "questions/test_recorded_bundle.py",
    "questions/test_urdf_structure.py",
    "recording/test_activity.py",
    "recording/test_detected_events.py",
    "recording/test_marker_topics.py",
    "recording/test_recording.py",
    "recording/test_segments.py",
    "scenes/test_presentation.py",
    "stepping/test_held_queries.py",
)
"""
The test modules that import nothing of the CRAM workspace, relative to this directory.
"""

TEST_DIRECTORY = Path(__file__).parent
"""
The directory of this package's tests.
"""


def tests_needing_cram() -> list:
    """
    :return: Every test module and test directory that needs CRAM, relative to this
        directory; a directory without a test that runs without CRAM is left out whole,
        together with its fixtures.
    """
    kept_directories = {(TEST_DIRECTORY / test).parent for test in TESTS_WITHOUT_CRAM}
    directories = [
        path
        for path in TEST_DIRECTORY.iterdir()
        if path.is_dir()
        and (path / "__init__.py").is_file()
        and path not in kept_directories
    ]
    modules = [
        path
        for directory in kept_directories
        for path in directory.glob("test_*.py")
        if path.relative_to(TEST_DIRECTORY).as_posix() not in TESTS_WITHOUT_CRAM
    ]
    return [str(path.relative_to(TEST_DIRECTORY)) for path in directories + modules]


@pytest.fixture
def locations(tmp_path, monkeypatch) -> DataLocations:
    """
    :return: Data locations below the test's directory, which demos read from the
        environment.
    """
    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "data"))
    return DataLocations(data=tmp_path / "data", shared_scenes=None)


if any(importlib.util.find_spec(package) is None for package in CRAM_PACKAGES):
    collect_ignore = tests_needing_cram()
else:
    from .dataset.worlds import KitchenWorld, RobotWorld, TexturedWorld

    @pytest.fixture
    def kitchen() -> KitchenWorld:
        """
        :return: A new kitchen world.
        """
        return KitchenWorld.build()

    @pytest.fixture
    def textured() -> TexturedWorld:
        """
        :return: A new world of textured surfaces.
        """
        return TexturedWorld.build()

    @pytest.fixture(scope="session")
    def robot_world() -> RobotWorld:
        """
        :return: A world holding a PR2, shared by the tests that only read it.
        """
        return RobotWorld.build()
