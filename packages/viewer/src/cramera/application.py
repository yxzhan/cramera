"""
The viewer application: every route the viewer serves, assembled from the scene catalog,
the browser files and the plugins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from typing_extensions import List, Optional

from cramera.branding import Branding
from cramera.confinement import ConfinedDirectory
from cramera.exceptions import FileNotInDirectory
from cramera.http.messages import HttpMethod, MediaType, Request, Response
from cramera.http.routing import ExactRoute, PrefixRoute, Router
from cramera.live_relay import LiveRelay
from cramera.locations import DataLocations
from cramera.plugins import PluginContext, ViewerPlugin
from cramera.scenes import SceneCatalog
from cramera_protocol.browser import BrowserModule
from cramera_protocol.viewer import ViewerInfo, ViewerRoute

WEB_DIRECTORY = Path(__file__).parent / "web"
"""
The browser application's files.
"""

INDEX_PAGE = "index.html"
"""
The page served for the root path.
"""

# %% application


@dataclass
class ViewerApplication:
    """
    The viewer's routes.
    """

    catalog: SceneCatalog
    """
    The scenes the viewer shows.
    """

    locations: DataLocations
    """
    Where cramera keeps its data.
    """

    plugins: List[ViewerPlugin] = field(default_factory=list)
    """
    The loaded plugins.
    """

    web_directory: Path = WEB_DIRECTORY
    """
    The browser application's files.
    """

    branding: Branding = field(default_factory=Branding)
    """
    Which logo the pages show.
    """

    live_relay: LiveRelay = field(default_factory=LiveRelay)
    """
    Reads the live publisher on this machine for pages of other devices.
    """

    deployment: Optional[str] = None
    """
    Which build of the deployment the viewer runs in, or None outside a deployment.
    """

    def router(self) -> Router:
        """
        :return: The router of every viewer and plugin route; the browser files come
            last, so no file can hide a route.
        """
        router = Router()
        router.add(ExactRoute(HttpMethod.GET, ViewerRoute.INFO, self.answer_info))
        router.add(ExactRoute(HttpMethod.GET, ViewerRoute.SCENES, self.answer_scenes))
        router.add(ExactRoute(HttpMethod.GET, ViewerRoute.LOGO, self.answer_logo))
        router.add(
            ExactRoute(
                HttpMethod.GET, ViewerRoute.PROTOCOL_MODULE, self.answer_protocol
            )
        )
        router.add(
            PrefixRoute(HttpMethod.GET, ViewerRoute.SCENE_FILES, self.answer_scene_file)
        )
        router.add(
            PrefixRoute(
                HttpMethod.GET, ViewerRoute.PLUGIN_FILES, self.answer_plugin_file
            )
        )
        for route in self.live_relay.routes():
            router.add(route)
        self.add_plugin_routes(router)
        router.add(PrefixRoute(HttpMethod.GET, "/", self.answer_web_file))
        return router

    def add_plugin_routes(self, router: Router) -> None:
        """
        :param router: The router receiving each plugin's routes below its prefix;
            the plugins start once every plugin's routes are built.
        """
        context = PluginContext(catalog=self.catalog, locations=self.locations)
        for plugin in self.plugins:
            for declaration in plugin.routes(context):
                router.add(
                    declaration.route(f"{ViewerRoute.PLUGIN_API}{plugin.name()}/")
                )
        for plugin in self.plugins:
            plugin.start()

    def close(self) -> None:
        """
        Release what the plugins own, once the viewer has stopped serving.
        """
        for plugin in self.plugins:
            plugin.close()

    # %% answers

    def answer_info(self, request: Request) -> Response:
        """
        :param request: A request for the viewer's information.
        :return: The viewer's version and plugins.
        """
        return Response.of_json(
            ViewerInfo(
                version=version("cramera"),
                plugins=[plugin.info() for plugin in self.plugins],
                logo_text=self.branding.text(),
                deployment=self.deployment,
            )
        )

    def answer_logo(self, request: Request) -> Response:
        """
        :param request: A request for the logo.
        :return: The logo's image.
        """
        return Response.of_file(self.branding.file())

    def answer_scenes(self, request: Request) -> Response:
        """
        :param request: A request for the scenes index.
        :return: The merged index of every scenes directory.
        """
        return Response.of_json(self.catalog.index())

    def answer_protocol(self, request: Request) -> Response:
        """
        :param request: A request for the protocol module.
        :return: The protocol's names as an ES module.
        """
        return Response.of_text(BrowserModule().source(), MediaType.JAVASCRIPT)

    def answer_scene_file(self, request: Request, path: str) -> Response:
        """
        :param request: A request for a bundle file.
        :param path: ``<bundle>/<file>``.
        :return: The file.
        """
        name, _, relative = path.partition("/")
        return Response.of_file(self.catalog.bundle_file(name, relative))

    def answer_plugin_file(self, request: Request, path: str) -> Response:
        """
        :param request: A request for a plugin's browser file.
        :param path: ``<plugin>/<file>``.
        :return: The file.
        :raises FileNotInDirectory: When no plugin of that name has browser files.
        """
        name, _, relative = path.partition("/")
        directories = {
            plugin.name(): plugin.web_directory()
            for plugin in self.plugins
            if plugin.web_directory() is not None
        }
        if name not in directories:
            raise FileNotInDirectory(Path(ViewerRoute.PLUGIN_FILES), path)
        return Response.of_file(ConfinedDirectory(directories[name]).file(relative))

    def answer_web_file(self, request: Request, path: str) -> Response:
        """
        :param request: A request for a browser file.
        :param path: The file, relative to the web directory; empty for the page.
        :return: The file.
        """
        return Response.of_file(
            ConfinedDirectory(self.web_directory).file(path or INDEX_PAGE)
        )
