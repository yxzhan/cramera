"""
What the CRAM workspace offers a plan: its robot annotations, object meshes, surface
annotations and native actions, as cramera-bridge reads them and hands them over in the
protocol's documents.

Whether the workspace is importable is decided by asking the import system before
anything imports it, so an absent workspace is a value the plugin reads rather than an
import error it has to catch; the module reading the workspace is only imported once
every module is found.
"""

from __future__ import annotations

import importlib.util
import shutil
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.machinery import ModuleSpec
from pathlib import Path

from typing_extensions import TYPE_CHECKING, Callable, Dict, List, Optional, Set, Tuple

from cramera.confinement import ConfinedDirectory
from cramera.cram_imports import CramImports
from cramera.exceptions import FileNotInDirectory
from cramera_plan_builder.exceptions import CramUnavailable
from cramera_protocol.bundle.files import MeshFormat
from cramera_protocol.plan_builder import (
    ActionCatalog,
    EnvironmentAnnotations,
    LoadedEnvironment,
    ObjectModel,
    RobotModel,
)

if TYPE_CHECKING:
    from cramera_bridge.plans.workspace import PlanWorkspace

TEXTURE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
"""
The suffixes of the textures a mesh may reference beside it.
"""

DESCRIPTION_SUFFIX = ".urdf"
"""
The suffix of an object library model's description, which names its meshes.
"""

LIBRARY_SEPARATOR = "/"
"""
Separates an object library's name from a file's path in the library.
"""

# %% modules


class CramModule(StrEnum):
    """
    The top-level modules a plan's catalog and its demo import.
    """

    SEMANTIC_DIGITAL_TWIN = "semantic_digital_twin"
    """
    The robot annotations, surfaces and enumerations.
    """

    CORAPLEX = "coraplex"
    """
    The actions and the object meshes.
    """

    KRROOD = "krrood"
    """
    The class introspection robot parts are read with.
    """

    CRAMERA_BRIDGE = "cramera_bridge"
    """
    The robot task demo a generated demo runs as.
    """


ModuleFinder = Callable[[str], Optional[ModuleSpec]]
"""
Finds a top-level module without importing it; :func:`importlib.util.find_spec` outside
of tests.
"""


@dataclass(frozen=True)
class CramModules:
    """
    Finds out which of the workspace's modules are installed.
    """

    modules: Tuple[CramModule, ...] = tuple(CramModule)
    """
    The modules that must be installed.
    """

    finder: ModuleFinder = field(default=importlib.util.find_spec)
    """
    Finds a module without importing it.
    """

    def missing(self) -> List[str]:
        """
        :return: The modules that cannot be found, in the order of :attr:`modules`; a
            directory that only shares a module's name does not count as the module.
        """
        return [
            module.value for module in self.modules if not self.is_installed(module)
        ]

    def is_installed(self, module: CramModule) -> bool:
        """
        :param module: A top-level module.
        :return: Whether a module with code of its own is found under that name.
        """
        specification = self.finder(module)
        return specification is not None and specification.origin is not None


# %% workspace


class CramWorkspace(ABC):
    """
    What the CRAM workspace offers a plan.
    """

    @abstractmethod
    def robots(self) -> List[RobotModel]:
        """
        :return: The robot annotations with what their parts allow, sorted by name.
        """

    @abstractmethod
    def write_robot_description(self, name: str, directory: Path) -> Path:
        """
        :param name: A robot's identifier.
        :param directory: The directory the description and its meshes are written to.
        :return: The written URDF, which references its meshes relative to itself.
        :raises CramUnavailable: Without the workspace.
        """

    def environment_annotations(self, directory: Path) -> EnvironmentAnnotations:
        """
        :param directory: An environment bundle.
        :return: Its semantic annotations as a plan run finds them; none by default.
        """
        return EnvironmentAnnotations()

    @abstractmethod
    def write_environment(
        self, source: Path, scenes: Path, taken: Set[str]
    ) -> LoadedEnvironment:
        """
        :param source: A URDF or xacro file of this machine.
        :param scenes: The scenes directory the environment bundle is written to.
        :param taken: The bundle names already in use, in any scenes directory.
        :return: The written bundle's index entry and the meshes that were not found.
        :raises CramUnavailable: Without the workspace.
        """

    @abstractmethod
    def objects(self) -> List[ObjectModel]:
        """
        :return: The object meshes, sorted by identifier.
        """

    @abstractmethod
    def object_directory(self) -> Path:
        """
        :return: The directory holding the object meshes.
        :raises CramUnavailable: Without the workspace.
        """

    @abstractmethod
    def surface_types(self) -> List[str]:
        """
        :return: The identifiers of the annotations objects can be put on or in, sorted.
        """

    def saved_surface_types(self) -> Dict[str, str]:
        """
        :return: The identifier of each surface type by the name plans saved it under
            before the protocol named it; none by default.
        """
        return {}

    @abstractmethod
    def action_catalog(self) -> ActionCatalog:
        """
        :return: The schemas of the native actions and the action modules that failed
            to import.
        """

    def missing(self) -> List[str]:
        """
        :return: The modules that cannot be found; none for an installed workspace.
        """
        return []

    def object_roots(self) -> Dict[str, Path]:
        """
        :return: The root of each object library by the name its files' paths start
            with; none without libraries.
        """
        return {}

    def object_file(self, requested: str) -> Path:
        """
        :param requested: A mesh of the object directory, or a material library or
            texture beside one; or ``<library>/<path>`` of a library model's
            description, mesh or texture.
        :return: The file.
        :raises CramUnavailable: Without the workspace.
        :raises PathOutsideDirectory: For a path leaving the directory or the library.
        :raises FileNotInDirectory: For a missing file or one that is no mesh, material
            library, texture or description.
        """
        library, _, relative = requested.partition(LIBRARY_SEPARATOR)
        roots = self.object_roots()
        if relative and library in roots:
            directory, requested = roots[library], relative
        else:
            directory = self.object_directory()
        file = ConfinedDirectory(directory).file(requested)
        if file.suffix.lower() not in self.served_suffixes():
            raise FileNotInDirectory(directory, requested)
        return file

    @staticmethod
    def served_suffixes() -> frozenset[str]:
        """
        :return: The suffixes of the object directory's files that are served.
        """
        return (
            frozenset(member.value for member in MeshFormat)
            | TEXTURE_SUFFIXES
            | {DESCRIPTION_SUFFIX}
        )


@dataclass(frozen=True)
class MissingWorkspace(CramWorkspace):
    """
    The workspace of an interpreter that cannot import CRAM: it offers nothing and names
    what is missing.
    """

    missing_modules: List[str]
    """
    The modules that cannot be found.
    """

    def robots(self) -> List[RobotModel]:
        return []

    def write_robot_description(self, name: str, directory: Path) -> Path:
        raise CramUnavailable(self.missing_modules)

    def write_environment(
        self, source: Path, scenes: Path, taken: Set[str]
    ) -> LoadedEnvironment:
        raise CramUnavailable(self.missing_modules)

    def objects(self) -> List[ObjectModel]:
        return []

    def object_directory(self) -> Path:
        raise CramUnavailable(self.missing_modules)

    def surface_types(self) -> List[str]:
        return []

    def action_catalog(self) -> ActionCatalog:
        return ActionCatalog()

    def missing(self) -> List[str]:
        return list(self.missing_modules)


@dataclass(frozen=True)
class InstalledWorkspace(CramWorkspace):
    """
    The workspace of an interpreter that imports CRAM, as cramera-bridge reads it.
    """

    plans: PlanWorkspace
    """
    The workspace as cramera-bridge reads it.
    """

    def robots(self) -> List[RobotModel]:
        return self.plans.robots()

    def write_robot_description(self, name: str, directory: Path) -> Path:
        return self.plans.write_robot_description(name, directory)

    def write_environment(
        self, source: Path, scenes: Path, taken: Set[str]
    ) -> LoadedEnvironment:
        return self.plans.write_environment(source, scenes, taken)

    def environment_annotations(self, directory: Path) -> EnvironmentAnnotations:
        return self.plans.environment_annotations(directory)

    def objects(self) -> List[ObjectModel]:
        return self.plans.objects()

    def object_directory(self) -> Path:
        return self.plans.object_directory()

    def object_roots(self) -> Dict[str, Path]:
        return self.plans.object_roots()

    def surface_types(self) -> List[str]:
        return self.plans.surface_types()

    def saved_surface_types(self) -> Dict[str, str]:
        return self.plans.saved_surface_types()

    def action_catalog(self) -> ActionCatalog:
        return self.plans.action_catalog()


# %% choice


class WorkspaceModule(StrEnum):
    """
    The module of cramera-bridge reading the installed workspace; it imports CRAM, so it
    is only imported once every module is found.
    """

    INSTALLED = "cramera_bridge.plans.workspace"
    """
    :class:`cramera_bridge.plans.workspace.PlanWorkspace`.
    """


@dataclass
class WorkspaceLoader:
    """
    Reads the workspace once, when it is first asked for or preloaded: importing CRAM
    takes seconds, which the viewer's start does not wait for.
    """

    modules: CramModules = field(default_factory=CramModules)
    """
    Finds out which modules are installed.
    """

    loaded: Optional[CramWorkspace] = field(default=None, init=False)
    """
    The workspace once it was read.
    """

    lock: threading.RLock = field(default_factory=lambda: CramImports.lock, init=False)
    """
    The gate of the process's CRAM imports: one thread reads the workspace while
    others wait for it, and no other thread imports CRAM meanwhile.
    """

    def workspace(self) -> CramWorkspace:
        """
        :return: The installed workspace, or a :class:`MissingWorkspace` naming the
            modules that cannot be found.
        """
        with self.lock:
            if self.loaded is None:
                self.loaded = self.read()
            return self.loaded

    def preload(self) -> threading.Thread:
        """
        Read the workspace in a thread of its own, so the first request asking for it
        does not wait for CRAM's import as long; a request asking meanwhile waits for
        it, and one asking after a failed read reads it again.

        :return: The started thread reading it.
        """
        reading = threading.Thread(
            target=self.warm, name="workspace preload", daemon=True
        )
        reading.start()
        return reading

    def warm(self) -> None:
        """
        Read the workspace and list its robots once, which imports the robots' modules
        the catalog's first answer would otherwise wait for; no other thread imports
        CRAM meanwhile.
        """
        with self.lock:
            self.workspace().robots()

    def read(self) -> CramWorkspace:
        """
        :return: The workspace the installed modules allow.
        """
        missing = self.modules.missing()
        if missing:
            return MissingWorkspace(missing)
        module = CramImports().module(WorkspaceModule.INSTALLED)
        return InstalledWorkspace(module.PlanWorkspace())


# %% robot models


@dataclass
class RobotModelFiles:
    """
    The robots' model descriptions and their meshes, written into a directory the first
    time a robot is asked for in this process, so a changed description shows after a
    restart.
    """

    workspace: WorkspaceLoader
    """
    Reads what the CRAM workspace offers.
    """

    directory: Path
    """
    The directory holding one directory per robot.
    """

    written: Set[str] = field(default_factory=set, init=False)
    """
    The robots whose description was written in this process.
    """

    lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    """
    Lets one request write a description while others wait for it.
    """

    def file(self, requested: str) -> Path:
        """
        :param requested: ``<robot>/<robot>.urdf`` of a robot's description, or
            ``<robot>/<path>`` of a mesh or texture it references.
        :return: The file.
        :raises CramUnavailable: Without the workspace.
        :raises FileNotInDirectory: For a robot the workspace does not offer, a missing
            file or one that is no mesh, texture or description.
        :raises PathOutsideDirectory: For a path leaving the robot's directory.
        """
        robot, _, relative = requested.partition(LIBRARY_SEPARATOR)
        workspace = self.workspace.workspace()
        if robot not in {model.name for model in workspace.robots()}:
            raise FileNotInDirectory(self.directory, requested)
        directory = self.directory / robot
        with self.lock:
            if robot not in self.written:
                shutil.rmtree(directory, ignore_errors=True)
                workspace.write_robot_description(robot, directory)
                self.written.add(robot)
        file = ConfinedDirectory(directory).file(relative)
        if file.suffix.lower() not in CramWorkspace.served_suffixes():
            raise FileNotInDirectory(directory, relative)
        return file
