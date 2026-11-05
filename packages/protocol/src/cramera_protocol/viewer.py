"""
The viewer's own routes, environment variables and plugin entry points.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import List, Optional

from cramera_protocol.document import JsonDocument, document_field
from cramera_protocol.live import Capability
from cramera_protocol.version import CURRENT_VERSION, ProtocolVersion

# %% routes


class ViewerRoute(StrEnum):
    """
    The routes the viewer itself serves; plugins add routes below :attr:`PLUGIN_API`.
    """

    INFO = "/api/info"
    """
    The viewer's version, its plugins and their capabilities.
    """

    SCENES = "/api/scenes"
    """
    The merged index of every scenes directory.
    """

    SCENE_FILES = "/scenes/"
    """
    The prefix of bundle files: ``/scenes/<bundle>/<file>``.
    """

    PROTOCOL_MODULE = "/protocol.js"
    """
    The protocol's names as an ES module for the browser.
    """

    PLUGIN_FILES = "/plugins/"
    """
    The prefix of plugin browser files: ``/plugins/<plugin>/<file>``.
    """

    PLUGIN_API = "/api/plugins/"
    """
    The prefix of plugin routes: ``/api/plugins/<plugin>/<route>``.
    """

    LOGO = "/api/logo"
    """
    The logo the top bar of every page shows: the AICOR logo unless the viewer was given
    another.
    """

    LIVE_RELAY = "/api/live/"
    """
    The prefix of what the page reads from and changes at the live publisher on this
    machine, ``/api/live/<route>``, relayed so that the viewer's port is the only one a
    page has to reach.
    """


# %% answers


class ErrorKey(StrEnum):
    """
    The keys of an error answer of the viewer or a live publisher.
    """

    ERROR = "error"
    """
    The reason the request failed.
    """


# %% environment


class EnvironmentVariable(StrEnum):
    """
    The environment variables cramera reads.
    """

    SCENES = "CRAMERA_SCENES"
    """
    The shared scenes directory.
    """

    DATA = "CRAMERA_DATA"
    """
    The directory of cramera's own data; local recordings go to its ``scenes``.
    """

    LOGO = "CRAMERA_LOGO"
    """
    An image file the viewer shows as its logo instead of the AICOR logo.
    """


# %% plugins


class PluginGroup(StrEnum):
    """
    The entry-point groups cramera discovers.
    """

    VIEWER = "cramera.plugins"
    """
    Plugins of the viewer; each entry point names a factory of a viewer plugin.
    """


class PluginKey(StrEnum):
    """
    The keys of one plugin in the viewer's information.
    """

    NAME = "name"
    """
    The plugin's name.
    """

    VERSION = "version"
    """
    The plugin's version.
    """

    MODULE = "module"
    """
    The URL of the plugin's browser module.
    """

    CAPABILITIES = "capabilities"
    """
    What the plugin adds.
    """

    NAVIGATION = "navigation"
    """
    The plugin's entry in the viewer's navigation.
    """


class NavigationGroup(StrEnum):
    """
    The groups of the viewer's navigation, in the order the header shows them; within a
    group, entries are ordered by title.
    """

    ANALYSIS = "analysis"
    """
    Pages that ask questions of data, such as a probabilistic model.
    """

    BUILDER = "builder"
    """
    Pages that compose plans and scenes.
    """

    LABORATORY = "laboratory"
    """
    Simulated workplaces a person operates.
    """


class NavigationKey(StrEnum):
    """
    The keys of a plugin's navigation entry.
    """

    TITLE = "title"
    """
    The entry's text.
    """

    GROUP = "group"
    """
    The :class:`NavigationGroup` the entry belongs to.
    """

    PAGE = "page"
    """
    The route of the plugin's own page below :attr:`ViewerRoute.PLUGIN_FILES`.
    """


@dataclass(kw_only=True)
class PluginNavigation(JsonDocument):
    """
    A plugin's entry in the viewer's navigation: either a page of its own or its panel
    on the scene page, whose identity is the plugin's name.
    """

    title: str = document_field(NavigationKey.TITLE)
    """
    The entry's text.
    """

    group: NavigationGroup = document_field(NavigationKey.GROUP)
    """
    The group the entry is shown in.
    """

    page: Optional[str] = document_field(NavigationKey.PAGE, default=None)
    """
    The URL of the plugin's own page relative to the viewer's root, or None for an entry
    that shows the plugin's panel on the scene page.
    """


@dataclass(kw_only=True)
class PluginInfo(JsonDocument):
    """
    One loaded plugin as the browser learns about it.
    """

    name: str = document_field(PluginKey.NAME)
    """
    The plugin's name, which is also its route segment.
    """

    version: str = document_field(PluginKey.VERSION)
    """
    The plugin's version.
    """

    module: Optional[str] = document_field(PluginKey.MODULE, default=None)
    """
    The URL of the plugin's browser module, or None for a plugin without one.
    """

    capabilities: List[Capability] = document_field(
        PluginKey.CAPABILITIES, default_factory=list
    )
    """
    What the plugin adds.
    """

    navigation: Optional[PluginNavigation] = document_field(
        PluginKey.NAVIGATION, default=None
    )
    """
    The plugin's entry in the viewer's navigation, or None for a plugin without one.
    """


class BuildKey(StrEnum):
    """
    The keys of a build stamp.
    """

    COMMIT = "commit"
    """
    The commit the build was made from.
    """

    BUILT_AT = "builtAt"
    """
    When the build was made.
    """

    SOURCE = "source"
    """
    The repository the build was made from.
    """


@dataclass(kw_only=True)
class BuildStamp(JsonDocument):
    """
    Which commit a published build of the viewer shows, and when it was made.
    """

    commit: str = document_field(BuildKey.COMMIT)
    """
    The full hash of the commit the build was made from.
    """

    built_at: str = document_field(BuildKey.BUILT_AT)
    """
    When the build was made, as an ISO 8601 date and time in UTC.
    """

    source: Optional[str] = document_field(BuildKey.SOURCE, default=None)
    """
    The web address of the repository the build was made from.
    """


class ViewerInfoKey(StrEnum):
    """
    The keys of the answer to :attr:`ViewerRoute.INFO`.
    """

    PROTOCOL_VERSION = "protocolVersion"
    """
    The protocol version the viewer speaks.
    """

    VERSION = "version"
    """
    The viewer's version.
    """

    PLUGINS = "plugins"
    """
    The loaded plugins.
    """

    BUILD = "build"
    """
    The build stamp of a published build.
    """

    SERVING = "serving"
    """
    How the viewer is served, a :class:`Serving` value.
    """

    LOGO_TEXT = "logoText"
    """
    What the logo shows, for people who cannot see it.
    """


class Serving(StrEnum):
    """
    How the viewer's files reach the browser.
    """

    SERVER = "server"
    """
    The viewer's server answers every route, the plugins' included.
    """

    STATIC = "static"
    """
    A static site: the viewer's JSON routes are files and plugin routes are absent.
    """


@dataclass(kw_only=True)
class ViewerInfo(JsonDocument):
    """
    What the viewer says about itself.
    """

    version: str = document_field(ViewerInfoKey.VERSION)
    """
    The viewer's version.
    """

    protocol_version: ProtocolVersion = document_field(
        ViewerInfoKey.PROTOCOL_VERSION, default=CURRENT_VERSION
    )
    """
    The protocol version the viewer speaks.
    """

    plugins: List[PluginInfo] = document_field(
        ViewerInfoKey.PLUGINS, default_factory=list
    )
    """
    The loaded plugins.
    """

    build: Optional[BuildStamp] = document_field(ViewerInfoKey.BUILD, default=None)
    """
    The build stamp of a published build, or None for a viewer served from an
    installation.
    """

    serving: Serving = document_field(ViewerInfoKey.SERVING, default=Serving.SERVER)
    """
    How the viewer is served.
    """

    logo_text: Optional[str] = document_field(ViewerInfoKey.LOGO_TEXT, default=None)
    """
    What the logo at :attr:`ViewerRoute.LOGO` shows, or None for the AICOR logo.
    """
