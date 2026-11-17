"""
The viewer plugin answering questions about recorded scenes.

The plugin itself imports nothing of krrood or cramera-bridge, so a viewer without the
CRAM workspace still loads it and serves its browser module; its routes are built from
:mod:`cramera_questions.routes`, which hands the questions to cramera-bridge's question
engine, only where both are installed, and otherwise answer what is missing.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.machinery import ModuleSpec
from importlib.metadata import distributions
from pathlib import Path

from typing_extensions import TYPE_CHECKING, Callable, Dict, List, Optional

from cramera.cram_imports import CramImports
from cramera.http.messages import HttpMethod, Request, Response
from cramera.plugins import PluginContext, PluginRoute, ViewerPlugin
from cramera_protocol.live import Capability
from cramera_protocol.plugins import PluginName
from cramera_protocol.questions import QuestionsRoute
from cramera_questions.exceptions import QueryLanguageMissing

if TYPE_CHECKING:
    from cramera_bridge.questions.scenes import SceneQuestionCache
    from cramera_bridge.questions.workspace_classes import WorkspaceClassIndex

WEB_DIRECTORY = Path(__file__).parent / "web"
"""
The plugin's browser files.
"""

BROWSER_MODULE = "index.js"
"""
The plugin's browser module, relative to :data:`WEB_DIRECTORY`.
"""

DISTRIBUTION_NAME = "cramera-questions"
"""
The distribution the plugin is installed from.
"""

SOURCE_VERSION = "1.0.0"
"""
The version of this source tree, as its ``pyproject.toml`` declares it; reported when
the plugin runs from the sources without being installed.
"""

ModuleFinder = Callable[[str], Optional[ModuleSpec]]
"""
Finds a top-level module without importing it; :func:`importlib.util.find_spec` outside
of tests.
"""


class QuestionsModule(StrEnum):
    """
    The plugin's modules that import cramera-bridge's question engine, imported only
    where it and krrood are installed.
    """

    ROUTES = "cramera_questions.routes"
    """
    :class:`cramera_questions.routes.QuestionRoutes`.
    """


# %% installation


class QuestionsDependency(StrEnum):
    """
    The top-level modules answering questions needs.
    """

    KRROOD = "krrood"
    """
    Evaluates the entity query language.
    """

    CRAMERA_BRIDGE = "cramera_bridge"
    """
    The question engine.
    """


@dataclass(frozen=True)
class QueryLanguageInstallation:
    """
    Whether krrood, which evaluates the entity query language, and cramera-bridge, whose
    question engine uses it, are installed.
    """

    finder: ModuleFinder = field(default=importlib.util.find_spec)
    """
    Finds a module without importing it.
    """

    def missing(self) -> List[str]:
        """
        :return: The modules that cannot be found, in the order of
            :class:`QuestionsDependency`; a directory that only shares a module's name
            does not count as the module.
        """
        return [
            dependency.value
            for dependency in QuestionsDependency
            if not self.has_code(dependency)
        ]

    def is_installed(self) -> bool:
        """
        :return: Whether every module answering questions needs is found.
        """
        return not self.missing()

    def has_code(self, dependency: QuestionsDependency) -> bool:
        """
        :param dependency: A top-level module.
        :return: Whether a module with code of its own is found under that name.
        """
        specification = self.finder(dependency.value)
        return specification is not None and specification.origin is not None


@dataclass(frozen=True)
class UnavailableQuestionRoutes:
    """
    The plugin's routes in an environment without krrood or cramera-bridge: each answers
    what is missing.
    """

    missing: List[str]
    """
    The modules that cannot be found.
    """

    def routes(self) -> List[PluginRoute]:
        """
        :return: One route per :class:`QuestionsRoute` and method.
        """
        return [
            PluginRoute(method, route, self.refuse)
            for route in QuestionsRoute
            for method in (HttpMethod.GET, HttpMethod.POST)
        ]

    def refuse(self, request: Request) -> Response:
        """
        :param request: Any request of the plugin.
        :raises QueryLanguageMissing: Always.
        """
        raise QueryLanguageMissing(self.missing)


# %% plugin


@dataclass
class QuestionsPlugin(ViewerPlugin):
    """
    Answers queries, plain-language questions and graph views about recorded scenes.
    """

    class_index: Optional[WorkspaceClassIndex] = None
    """
    The workspace classes a query may name; None reads those of the installed workspace
    packages once the routes are built.
    """

    installation: QueryLanguageInstallation = field(
        default_factory=QueryLanguageInstallation
    )
    """
    Finds out whether krrood and cramera-bridge are installed.
    """

    caches: Dict[int, SceneQuestionCache] = field(default_factory=dict)
    """
    The scene questions of each catalog the plugin was given, by the catalog's identity.
    """

    def name(self) -> str:
        return PluginName.QUESTIONS.value

    def version(self) -> str:
        """
        :return: The installed distribution's version, or the version of this source
            tree when it runs without being installed.
        """
        return next(
            (
                distribution.version
                for distribution in distributions(name=DISTRIBUTION_NAME)
            ),
            SOURCE_VERSION,
        )

    def capabilities(self) -> List[Capability]:
        return [Capability.QUESTIONS]

    def web_directory(self) -> Optional[Path]:
        return WEB_DIRECTORY

    def module(self) -> Optional[str]:
        return BROWSER_MODULE

    def routes(self, context: PluginContext) -> List[PluginRoute]:
        missing = self.installation.missing()
        if missing:
            return UnavailableQuestionRoutes(missing).routes()
        routes = CramImports().module(QuestionsModule.ROUTES).QuestionRoutes
        answering = routes.of_catalog(context.catalog, self.class_index)
        self.caches[id(context.catalog)] = answering.questions.cache
        return answering.routes()
