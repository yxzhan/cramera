"""
The classes of the CRAM workspace a query may name, found by reading the packages'
sources without importing them; a class is imported when a query first names it.
"""

from __future__ import annotations

import ast
import importlib.util
from functools import cache
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cramera.cram_imports import CramImports

PYTHON_SUFFIX = ".py"
"""
The suffix of a Python source file.
"""

PACKAGE_MODULE = "__init__"
"""
The module name of a package's own source file.
"""

GENERATED_CLASS_SUFFIX = "DAO"
"""
The suffix of the ORM classes generated from the workspace's dataclasses.
"""

GENERATED_ASSOCIATION_MARKER = "_association"
"""
The marker in the name of a generated ORM association class.
"""

GENERATED_MODULE = "ormatic_interface"
"""
The module holding the generated ORM classes, which is never read.
"""

# %% packages


class WorkspacePackage(StrEnum):
    """
    The packages a query may name classes from, in the order a name several of them
    define is awarded.
    """

    SEMANTIC_DIGITAL_TWIN = "semantic_digital_twin"
    """
    Worlds, bodies and their annotations.
    """

    CORAPLEX = "coraplex"
    """
    Plans, actions and motions.
    """

    KRROOD = "krrood"
    """
    The entity query language.
    """

    SEGMIND = "segmind"
    """
    Event detection.
    """

    GISKARDPY = "giskardpy"
    """
    Motion statecharts.
    """

    PHYSICS_SIMULATORS = "physics_simulators"
    """
    Physics simulation.
    """

    PROBABILISTIC_MODEL = "probabilistic_model"
    """
    Probabilistic models.
    """

    RANDOM_EVENTS = "random_events"
    """
    Random events.
    """

    ROBOKUDO = "robokudo"
    """
    Perception.
    """

    def rank(self) -> int:
        """
        :return: The package's position; the lower position wins a shared name.
        """
        return list(type(self)).index(self)


# %% classes


@dataclass(frozen=True)
class ClassLocation:
    """
    One class a query may name and where it is defined.
    """

    name: str
    """
    The class's name.
    """

    module: str
    """
    The importable module defining the class.
    """

    package: WorkspacePackage
    """
    The workspace package the class belongs to.
    """

    docstring_summary: str = ""
    """
    The first line of the class's docstring.
    """

    def load(self) -> Any:
        """
        :return: The class, importing its module.
        """
        return vars(CramImports().module(self.module))[self.name]

    @staticmethod
    def is_generated(name: str) -> bool:
        """
        :param name: A class name.
        :return: Whether it names a generated ORM class.
        """
        return name.endswith(GENERATED_CLASS_SUFFIX) or (
            GENERATED_ASSOCIATION_MARKER in name
        )


@dataclass(frozen=True)
class SourceTreeScanner:
    """
    Reads the top-level classes of every module of one package's source tree.
    """

    package_directory: Path
    """
    The package's directory, holding its ``__init__.py``.
    """

    package: WorkspacePackage
    """
    The package the directory holds.
    """

    def classes(self) -> List[ClassLocation]:
        """
        :return: Every hand-written top-level class, in file order.
        """
        found: List[ClassLocation] = []
        for source in sorted(self.package_directory.rglob(f"*{PYTHON_SUFFIX}")):
            if source.stem == GENERATED_MODULE:
                continue
            found.extend(self.classes_of(source))
        return found

    def classes_of(self, source: Path) -> List[ClassLocation]:
        """
        :param source: One module of the package.
        :return: The module's hand-written top-level classes.
        """
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        module = self.module_of(source)
        return [
            ClassLocation(
                name=statement.name,
                module=module,
                package=self.package,
                docstring_summary=self.summary_of(statement),
            )
            for statement in tree.body
            if isinstance(statement, ast.ClassDef)
            and not ClassLocation.is_generated(statement.name)
        ]

    def module_of(self, source: Path) -> str:
        """
        :param source: One module of the package.
        :return: Its importable dotted name.
        """
        relative = source.relative_to(self.package_directory.parent).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == PACKAGE_MODULE:
            parts.pop()
        return ".".join(parts)

    @staticmethod
    def summary_of(statement: ast.ClassDef) -> str:
        """
        :param statement: A class definition.
        :return: The first line of its docstring, or an empty text.
        """
        docstring = (ast.get_docstring(statement) or "").strip()
        return docstring.splitlines()[0] if docstring else ""


# %% index


@dataclass
class WorkspaceClassIndex:
    """
    The classes a query may name, by name, the class a query gets first leading.
    """

    locations: Dict[str, Tuple[ClassLocation, ...]] = field(default_factory=dict)
    """
    Where each nameable class is defined, best candidate first.
    """

    @classmethod
    def of_classes(cls, classes: Iterable[ClassLocation]) -> WorkspaceClassIndex:
        """
        :param classes: The classes to index.
        :return: An index awarding a shared name by :meth:`WorkspacePackage.rank`.
        """
        grouped: Dict[str, List[ClassLocation]] = {}
        for location in classes:
            grouped.setdefault(location.name, []).append(location)
        return cls(
            locations={
                name: tuple(sorted(found, key=lambda one: one.package.rank()))
                for name, found in grouped.items()
            }
        )

    @classmethod
    def of_package_directories(
        cls, directories: Dict[WorkspacePackage, Path]
    ) -> WorkspaceClassIndex:
        """
        :param directories: The source directory of each package to read.
        :return: An index of their classes.
        """
        return cls.of_classes(
            location
            for package, directory in directories.items()
            for location in SourceTreeScanner(directory, package).classes()
        )

    @classmethod
    def of_installed_packages(
        cls, packages: Sequence[WorkspacePackage] = tuple(WorkspacePackage)
    ) -> WorkspaceClassIndex:
        """
        :param packages: The packages to read; those that are not installed are left
            out.
        :return: An index of the installed packages' classes, read from their sources
            without importing them.
        """
        directories: Dict[WorkspacePackage, Path] = {}
        for package in packages:
            specification = importlib.util.find_spec(package.value)
            if specification is None or not specification.submodule_search_locations:
                continue
            directories[package] = Path(
                list(specification.submodule_search_locations)[0]
            )
        return cls.of_package_directories(directories)

    @classmethod
    @cache
    def installed(cls) -> WorkspaceClassIndex:
        """
        :return: The index of every installed package's classes, read once per process,
            as the installed sources do not change while it runs.
        """
        return cls.of_installed_packages()

    def candidates(self, name: str) -> Tuple[ClassLocation, ...]:
        """
        :param name: A class name.
        :return: Every class of that name, the one a query gets first.
        """
        return self.locations.get(name, ())

    def names(self) -> List[str]:
        """
        :return: Every class name a query may use, sorted.
        """
        return sorted(self.locations)

    def resolve(self, name: str) -> Optional[Any]:
        """
        :param name: A class name.
        :return: The class a query means by it, or None when no class has it.
        """
        candidates = self.candidates(name)
        return candidates[0].load() if candidates else None


@dataclass(eq=False)
class WorkspaceClassNamespace(Dict[str, Any]):
    """
    The namespace a query is evaluated in: names placed in it answer as themselves, any
    other name is looked up in the class index the first time a query uses it.
    """

    index: WorkspaceClassIndex = field(default_factory=WorkspaceClassIndex)
    """
    Where a name the namespace was not given is looked up.
    """

    def __missing__(self, name: str) -> Any:
        """
        :param name: A name the query used.
        :return: The class of that name.
        :raises KeyError: When no class has it, which the interpreter reports as the
            ``NameError`` of an unknown name.
        """
        resolved = self.index.resolve(name)
        if resolved is None:
            raise KeyError(name)
        self[name] = resolved
        return resolved
