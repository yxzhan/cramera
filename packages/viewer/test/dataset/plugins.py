"""
Plugins that stand in for installed viewer plugins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from typing_extensions import List, Optional

from cramera.http.messages import HttpMethod, Request, Response
from cramera.plugins import (
    PluginLoader,
    NavigationEntry,
    PluginContext,
    PluginPrefixRoute,
    PluginRoute,
    RouteDeclaration,
    ViewerPlugin,
)
from cramera_protocol.live import Capability

PLUGIN_WEB = Path(__file__).parent / "plugin_web"
"""
The browser files of :class:`QuestionPlugin`.
"""


@dataclass
class QuestionPlugin(ViewerPlugin):
    """
    A plugin with a route, browser files and a capability.
    """

    plugin_name: str = "questions"
    """
    The name the plugin reports.
    """

    contexts: List[PluginContext] = field(default_factory=list)
    """
    The contexts the viewer gave the plugin.
    """

    closings: int = 0
    """
    How often the viewer closed the plugin.
    """

    entry: Optional[NavigationEntry] = None
    """
    The plugin's entry in the viewer's navigation.
    """

    def name(self) -> str:
        return self.plugin_name

    def version(self) -> str:
        return "1.2.3"

    def navigation(self) -> Optional[NavigationEntry]:
        return self.entry

    def capabilities(self) -> List[Capability]:
        return [Capability.QUESTIONS]

    def routes(self, context: PluginContext) -> List[RouteDeclaration]:
        self.contexts.append(context)
        return [
            PluginRoute(HttpMethod.POST, "ask", self.answer),
            PluginPrefixRoute(HttpMethod.GET, "topics/", self.answer_topic),
        ]

    def answer(self, request: Request) -> Response:
        """
        :param request: A question as JSON.
        :return: The question echoed back.
        """
        return Response.of_json({"echo": request.json()})

    def answer_topic(self, request: Request, topic: str) -> Response:
        """
        :param request: A request for a topic.
        :param topic: The path below the route's prefix.
        :return: The topic echoed back.
        """
        return Response.of_json({"topic": topic})

    def web_directory(self) -> Optional[Path]:
        return PLUGIN_WEB

    def close(self) -> None:
        self.closings += 1

    def module(self) -> Optional[str]:
        return "index.js"


@dataclass
class SilentPlugin(ViewerPlugin):
    """
    A plugin with nothing but a name and a version.
    """

    plugin_name: str = "silent"
    """
    The name the plugin reports.
    """

    def name(self) -> str:
        return self.plugin_name

    def version(self) -> str:
        return "0.1.0"


@dataclass
class RecordingPlugin(ViewerPlugin):
    """
    A plugin writing down when the viewer asks for its routes and when it starts it.
    """

    plugin_name: str
    """
    The name the plugin reports.
    """

    events: List[str]
    """
    What the viewer asked of the plugins, in order, shared between plugins.
    """

    def name(self) -> str:
        return self.plugin_name

    def version(self) -> str:
        return "0.1.0"

    def routes(self, context: PluginContext) -> List[RouteDeclaration]:
        self.events.append(f"{self.plugin_name} routes")
        return []

    def start(self) -> None:
        self.events.append(f"{self.plugin_name} start")


@dataclass(frozen=True)
class FactoryEntryPoint:
    """
    Stands in for an installed entry point that names a plugin factory.
    """

    plugin: ViewerPlugin
    """
    The plugin the factory creates.
    """

    def load(self):
        """
        :return: The plugin's factory.
        """
        return lambda: self.plugin


@dataclass
class RefusingLoader(PluginLoader):
    """
    A plugin loader that must not be asked to load.
    """

    def load(self) -> List[ViewerPlugin]:
        """
        :raises AssertionError: Always.
        """
        raise AssertionError("No plugin may be loaded.")
