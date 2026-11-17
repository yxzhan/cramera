"""
Finding out whether the CRAM workspace is importable, and what a workspace serves.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest
from typing_extensions import Dict

from cramera.cram_imports import CramImports
from cramera.exceptions import FileNotInDirectory, PathOutsideDirectory
from cramera_plan_builder.exceptions import CramUnavailable
from cramera_plan_builder.workspace import (
    CramModule,
    CramModules,
    MissingWorkspace,
    RobotModelFiles,
    WorkspaceLoader,
)
from cramera_protocol.plan_builder import ActionCatalog

from .dataset.given_workspace import DATASET, ROBOT_MESH, GivenWorkspace

LIBRARY = DATASET / "object_library"
"""
The test object library.
"""


def finder_without(*absent: CramModule):
    """
    :param absent: Modules the finder does not find.
    :return: A finder that finds every other module as code.
    """
    return lambda name: (
        None if name in absent else ModuleSpec(name, loader=None, origin=f"{name}.py")
    )


def another_thread_may_import_cram() -> bool:
    """
    :return: Whether a thread other than the caller could import CRAM right now.
    """
    answers = []

    def probe() -> None:
        acquired = CramImports().lock.acquire(blocking=False)
        if acquired:
            CramImports().lock.release()
        answers.append(acquired)

    probing = threading.Thread(target=probe)
    probing.start()
    probing.join()
    return answers[0]


# %% modules


def test_modules_the_finder_does_not_find_are_missing():
    modules = CramModules(finder=finder_without(CramModule.CORAPLEX))
    assert modules.missing() == [CramModule.CORAPLEX.value]


def test_a_directory_sharing_a_modules_name_is_not_the_module():
    modules = CramModules(
        finder=lambda name: ModuleSpec(name, loader=None, origin=None)
    )
    assert modules.missing() == [module.value for module in CramModule]


def test_without_a_module_the_loader_answers_the_missing_workspace():
    loader = WorkspaceLoader(
        modules=CramModules(finder=finder_without(CramModule.KRROOD))
    )
    workspace = loader.workspace()
    assert workspace == MissingWorkspace([CramModule.KRROOD.value])
    assert loader.workspace() is workspace


def test_a_preloaded_workspace_is_read_beside_the_caller_and_kept():
    loader = WorkspaceLoader(
        modules=CramModules(finder=finder_without(CramModule.KRROOD))
    )
    loader.preload().join()
    assert loader.loaded == MissingWorkspace([CramModule.KRROOD.value])
    assert loader.workspace() is loader.loaded


def test_the_preload_keeps_every_other_cram_import_waiting_while_it_reads():
    free_while_reading = []

    def finder(name: str) -> None:
        free_while_reading.append(another_thread_may_import_cram())

    WorkspaceLoader(modules=CramModules(finder=finder)).preload().join()
    assert free_while_reading and not any(free_while_reading)


# %% the missing workspace


def test_the_missing_workspace_offers_nothing_and_names_what_is_missing():
    workspace = MissingWorkspace([CramModule.CORAPLEX.value])
    assert workspace.robots() == [] and workspace.objects() == []
    assert workspace.surface_types() == []
    assert workspace.action_catalog() == ActionCatalog()
    assert workspace.missing() == [CramModule.CORAPLEX.value]
    with pytest.raises(CramUnavailable) as refusal:
        workspace.object_file("milk.stl")
    assert refusal.value.missing == workspace.missing()
    with pytest.raises(CramUnavailable):
        workspace.write_robot_description("pr2", Path("unused"))


# %% object files


@pytest.mark.parametrize("name", ["milk.stl", "cup.obj", "cup.mtl"])
def test_meshes_and_their_material_libraries_are_served(given_workspace, name):
    assert given_workspace.object_file(name) == (DATASET / "objects" / name).resolve()


def test_a_file_of_another_kind_is_not_served(given_workspace):
    with pytest.raises(FileNotInDirectory):
        given_workspace.object_file("notes.txt")


def test_a_file_outside_the_object_directory_is_not_served(given_workspace):
    with pytest.raises(PathOutsideDirectory):
        given_workspace.object_file("../workspace.json")


def test_an_installed_workspace_names_nothing_missing(given_workspace):
    assert given_workspace.missing() == []


# %% object libraries


@dataclass(frozen=True)
class LibraryWorkspace(GivenWorkspace):
    """
    The test workspace with the test object library beside its meshes.
    """

    def object_roots(self) -> Dict[str, Path]:
        return {LIBRARY.name: LIBRARY}


@pytest.mark.parametrize(
    "path",
    [
        "objects/cup/model.urdf",
        "objects/cup/assets/cup.obj",
        "objects/cup/assets/cup.mtl",
    ],
)
def test_a_library_serves_its_descriptions_and_meshes(path):
    requested = f"{LIBRARY.name}/{path}"
    assert LibraryWorkspace().object_file(requested) == (LIBRARY / path).resolve()


def test_a_library_serves_nothing_but_descriptions_meshes_and_textures():
    with pytest.raises(FileNotInDirectory):
        LibraryWorkspace().object_file(f"{LIBRARY.name}/objects/cup/notes.md")


def test_a_path_leaving_the_library_is_refused():
    with pytest.raises(PathOutsideDirectory):
        LibraryWorkspace().object_file(f"{LIBRARY.name}/../workspace.json")


def test_a_path_naming_no_library_is_read_from_the_mesh_directory():
    assert (
        LibraryWorkspace().object_file("milk.stl")
        == (DATASET / "objects" / "milk.stl").resolve()
    )
    with pytest.raises(FileNotInDirectory):
        LibraryWorkspace().object_file("elsewhere/objects/cup/model.urdf")


# %% robot models


@dataclass(frozen=True)
class CountingWorkspace(GivenWorkspace):
    """
    The test workspace, recording each robot whose description it writes.
    """

    descriptions: list = None

    def write_robot_description(self, name: str, directory: Path) -> Path:
        self.descriptions.append(name)
        return super().write_robot_description(name, directory)


@pytest.fixture
def robot_files(tmp_path) -> RobotModelFiles:
    """
    :return: The robot model files of the counting test workspace.
    """
    loader = WorkspaceLoader()
    loader.loaded = CountingWorkspace(descriptions=[])
    return RobotModelFiles(loader, tmp_path / "robots")


def test_a_robots_description_and_its_meshes_are_served(robot_files):
    urdf = robot_files.file("pr2/pr2.urdf")
    assert urdf == robot_files.directory / "pr2" / "pr2.urdf"
    assert f"meshes/{ROBOT_MESH}" in urdf.read_text(encoding="utf-8")
    mesh = robot_files.file(f"pr2/meshes/{ROBOT_MESH}")
    assert mesh.read_bytes() == (DATASET / "objects" / ROBOT_MESH).read_bytes()


def test_a_robots_description_is_written_once(robot_files):
    robot_files.file("pr2/pr2.urdf")
    robot_files.file(f"pr2/meshes/{ROBOT_MESH}")
    robot_files.file("tracy/tracy.urdf")
    assert robot_files.workspace.workspace().descriptions == ["pr2", "tracy"]


def test_a_robot_the_workspace_does_not_offer_is_not_served(robot_files):
    with pytest.raises(FileNotInDirectory):
        robot_files.file("../robots/pr2.urdf")
    assert robot_files.workspace.workspace().descriptions == []


def test_a_path_leaving_the_robots_directory_is_refused(robot_files):
    with pytest.raises(PathOutsideDirectory):
        robot_files.file("pr2/../tracy/tracy.urdf")


def test_a_robots_directory_serves_nothing_but_descriptions_meshes_and_textures(
    robot_files,
):
    robot_files.file("pr2/pr2.urdf")
    (robot_files.directory / "pr2" / "notes.txt").write_text("", encoding="utf-8")
    with pytest.raises(FileNotInDirectory):
        robot_files.file("pr2/notes.txt")
