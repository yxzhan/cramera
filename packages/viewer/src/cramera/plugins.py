"""
Viewer plugins: packages that add routes, browser modules and capabilities.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from typing_extensions import Callable, Iterable, List, Optional

from cramera.exceptions import DuplicatePlugin, InvalidPluginName
from cramera.http.messages import HttpMethod
from cramera.http.routing import (
    ExactRoute,
    PrefixHandler,
    PrefixRoute,
    RequestHandler,
    Route,
)
from cramera.locations import DataLocations
from cramera.scenes import SceneCatalog
from cramera_protocol.live import Capability
from cramera_protocol.viewer import (
    NavigationGroup,
    PluginGroup,
    PluginInfo,
    PluginNavigation,
    ViewerRoute,
)

# %% plugin interface


@dataclass(frozen=True)
class PluginContext:
    """
    What the viewer gives a plugin to build its routes from.
    """

    catalog: SceneCatalog
    """
    The scenes the viewer shows.
    """

    locations: DataLocations
    """
    Where cramera keeps its data.
    """


@dataclass(frozen=True)
class RouteDeclaration(ABC):
    """
    A route a plugin declares below its prefix ``/api/plugins/<plugin>/``.
    """

    method: HttpMethod
    """
    The method the route answers.
    """

    streams_body: bool = field(default=False, kw_only=True)
    """
    Whether the route reads the request body itself, see :attr:`Route.streams_body`.
    """

    remote_changes: bool = field(default=False, kw_only=True)
    """
    Whether other devices may make this change, see :attr:`Route.remote_changes`.
    """

    local_only: bool = field(default=False, kw_only=True)
    """
    Whether only this machine may make the request, see :attr:`Route.local_only`.
    """

    @abstractmethod
    def route(self, plugin_prefix: str) -> Route:
        """
        :param plugin_prefix: The plugin's prefix, ending in ``/``.
        :return: The router's route for this declaration.
        """


@dataclass(frozen=True)
class PluginRoute(RouteDeclaration):
    """
    One route of a plugin, served at ``/api/plugins/<plugin>/<path>``.
    """

    path: str
    """
    The route's path below the plugin's prefix, without a leading ``/``.
    """

    handler: RequestHandler
    """
    Answers the requests.
    """

    def route(self, plugin_prefix: str) -> Route:
        return ExactRoute(
            self.method,
            f"{plugin_prefix}{self.path}",
            self.handler,
            streams_body=self.streams_body,
            remote_changes=self.remote_changes,
            local_only=self.local_only,
        )


@dataclass(frozen=True)
class PluginPrefixRoute(RouteDeclaration):
    """
    One route of a plugin for every path below ``/api/plugins/<plugin>/<prefix>``, such
    as files addressed by their path.
    """

    prefix: str
    """
    The route's prefix below the plugin's prefix, without a leading ``/`` and ending in
    ``/``.
    """

    handler: PrefixHandler
    """
    Answers the requests, given the path after the prefix.
    """

    def route(self, plugin_prefix: str) -> Route:
        return PrefixRoute(
            self.method,
            f"{plugin_prefix}{self.prefix}",
            self.handler,
            streams_body=self.streams_body,
            remote_changes=self.remote_changes,
            local_only=self.local_only,
        )


@dataclass(frozen=True)
class NavigationEntry(ABC):
    """
    A plugin's entry in the viewer's navigation.
    """

    title: str
    """
    The entry's text.
    """

    group: NavigationGroup
    """
    The group the entry is shown in.
    """

    @abstractmethod
    def info(self, plugin_name: str) -> PluginNavigation:
        """
        :param plugin_name: The name of the plugin the entry belongs to.
        :return: The entry as the browser learns about it.
        """


@dataclass(frozen=True)
class PageEntry(NavigationEntry):
    """
    An entry that opens a page of the plugin's own, which needs the whole window.
    """

    page: str
    """
    The page's file, relative to the plugin's web directory.
    """

    def info(self, plugin_name: str) -> PluginNavigation:
        return PluginNavigation(
            title=self.title,
            group=self.group,
            page=f"{ViewerRoute.PLUGIN_FILES}{plugin_name}/{self.page}",
        )


@dataclass(frozen=True)
class PanelEntry(NavigationEntry):
    """
    An entry that shows the plugin's panel on the scene page; the panel's identity is
    the plugin's name.
    """

    def info(self, plugin_name: str) -> PluginNavigation:
        return PluginNavigation(title=self.title, group=self.group)


class ViewerPlugin(ABC):
    """
    A package that extends the viewer.
    """

    @abstractmethod
    def name(self) -> str:
        """
        :return: The plugin's name: lowercase letters, digits and ``-``; it is also
            the plugin's route segment.
        """

    @abstractmethod
    def version(self) -> str:
        """
        :return: The plugin's version.
        """

    def capabilities(self) -> List[Capability]:
        """
        :return: What the plugin adds; nothing by default.
        """
        return []

    def routes(self, context: PluginContext) -> List[RouteDeclaration]:
        """
        :param context: What the viewer offers the plugin.
        :return: The plugin's routes; none by default.
        """
        return []

    def web_directory(self) -> Optional[Path]:
        """
        :return: The directory of the plugin's browser files, or None for a plugin
            without any.
        """
        return None

    def module(self) -> Optional[str]:
        """
        :return: The plugin's browser module, relative to :meth:`web_directory`, or
            None for a plugin without one.
        """
        return None

    def navigation(self) -> Optional[NavigationEntry]:
        """
        :return: The plugin's entry in the viewer's navigation, or None for a plugin
            that only adds to the scene page; none by default.
        """
        return None

    def start(self) -> None:
        """
        Begin what the plugin does in the background; the viewer calls it once every
        plugin's routes are built, before it serves.

        Nothing by default.
        """

    def close(self) -> None:
        """
        Release what the plugin owns, such as a simulation its routes started; the
        viewer calls it once it has stopped serving.

        Nothing by default.
        """

    def info(self) -> PluginInfo:
        """
        :return: The plugin as the browser learns about it.
        """
        module = self.module()
        navigation = self.navigation()
        return PluginInfo(
            name=self.name(),
            version=self.version(),
            module=(
                None
                if module is None
                else f"{ViewerRoute.PLUGIN_FILES}{self.name()}/{module}"
            ),
            capabilities=self.capabilities(),
            navigation=None if navigation is None else navigation.info(self.name()),
        )


PluginFactory = Callable[[], ViewerPlugin]
"""
What a plugin's entry point names: a callable that creates the plugin.
"""


# %% discovery


@dataclass
class PluginLoader:
    """
    Creates the installed plugins from their entry points.
    """

    entry_points: Iterable[EntryPoint] = field(
        default_factory=lambda: entry_points(group=PluginGroup.VIEWER)
    )
    """
    The entry points of the viewer's plugin group.
    """

    def load(self) -> List[ViewerPlugin]:
        """
        :return: The plugins, sorted by name.
        :raises InvalidPluginName: When a plugin's name is not a route segment.
        :raises DuplicatePlugin: When two plugins share a name.
        """
        plugins = [entry_point.load()() for entry_point in self.entry_points]
        names: List[str] = []
        for plugin in plugins:
            name = plugin.name()
            if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) is None:
                raise InvalidPluginName(name)
            if name in names:
                raise DuplicatePlugin(name)
            names.append(name)
        return sorted(plugins, key=lambda plugin: plugin.name())
