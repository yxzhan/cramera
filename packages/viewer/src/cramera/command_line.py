"""
``cramera``: serve the viewer, or, with a subcommand, connect to a viewer elsewhere.
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import signal
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from typing_extensions import Callable, List, Mapping, Optional, Sequence

from cramera.application import ViewerApplication
from cramera.branding import Branding
from cramera.exceptions import LogoNotFound, UnsupportedLogo
from cramera.http.addresses import EVERY_ADDRESS, LOOPBACK
from cramera.http.origin import KEY_PARAMETER, LOCAL_HOSTS, OriginPolicy
from cramera.http.server import HttpServer, RequestAnswerer
from cramera.lifeline import Lifeline
from cramera.locations import DataLocations
from cramera.network_key import NetworkKey
from cramera.occupant import PortOccupant
from cramera.plugins import PluginLoader
from cramera.remote_connection import connect, pipe
from cramera.scenes import SceneCatalog, SceneDirectory, SceneSource
from cramera.viewer_options import DEFAULT_PORT, Announcement, Option, Subcommand
from cramera_protocol.viewer import EnvironmentVariable

logger = logging.getLogger(__name__)
"""
The logger of the command.
"""

# %% configuration


@dataclass(frozen=True)
class ViewerConfiguration:
    """
    What the ``cramera`` command was asked to serve.
    """

    locations: DataLocations
    """
    Where cramera keeps its data.
    """

    host: str = LOOPBACK
    """
    The address to listen on.
    """

    port: int = DEFAULT_PORT
    """
    The port to listen on.
    """

    extra_scenes: List[Path] = field(default_factory=list)
    """
    Further shared scenes directories.
    """

    demo_scenes: Optional[Path] = None
    """
    A directory of demo scenes.
    """

    open_browser: bool = True
    """
    Whether to open the viewer in a browser.
    """

    replace_viewer: bool = True
    """
    Whether a viewer already listening on the port is stopped and replaced.
    """

    branding: Branding = field(default_factory=Branding)
    """
    Which logo the pages show.
    """

    public_origin: Optional[str] = None
    """
    The origin a password-checking reverse proxy on this machine serves the viewer at;
    None when pages reach the viewer directly.
    """

    ends_with_input: bool = False
    """
    Whether the viewer ends once its standard input closes.
    """

    deployment: Optional[str] = None
    """
    Which build of the deployment the viewer runs in, from
    :attr:`EnvironmentVariable.DEPLOYMENT`; None outside a deployment.
    """

    @classmethod
    def from_arguments(
        cls,
        arguments: Sequence[str],
        locations: DataLocations,
        environment: Optional[Mapping[str, str]] = None,
    ) -> ViewerConfiguration:
        """
        :param arguments: The command-line arguments without the program name.
        :param locations: The locations the environment configures.
        :param environment: The environment variables, none by default.
        :return: The configuration they describe; a logo that cannot be shown ends the
            command with its reason.
        """
        parser = argparse.ArgumentParser(
            prog="cramera", description="Serve the cramera viewer."
        )
        address = parser.add_mutually_exclusive_group()
        address.add_argument(Option.HOST, default=LOOPBACK)
        address.add_argument(Option.LAN, action="store_true")
        parser.add_argument(Option.PUBLIC_ORIGIN, default=None)
        parser.add_argument(Option.PORT, type=int, default=DEFAULT_PORT)
        parser.add_argument(Option.SCENES, type=Path, action="append", default=[])
        parser.add_argument(Option.DEMO_SCENES, type=Path, default=None)
        parser.add_argument(Option.NO_BROWSER, action="store_true")
        parser.add_argument(Option.KEEP_RUNNING, action="store_true")
        parser.add_argument(Option.LOGO, type=Path, default=None)
        parser.add_argument(Option.LOGO_TEXT, default=None)
        parser.add_argument(Option.LIFELINE, action="store_true")
        parsed = parser.parse_args(list(arguments))
        # a logo that cannot be shown is a usage error of the command, said as one
        try:
            branding = Branding.of(parsed.logo, parsed.logo_text, environment or {})
        except (LogoNotFound, UnsupportedLogo) as error:
            parser.error(error.message())
        host = EVERY_ADDRESS if parsed.lan else parsed.host
        public_origin = None
        if parsed.public_origin is not None:
            public_origin = origin_of(parsed.public_origin)
            if public_origin is None:
                parser.error(
                    f"{Option.PUBLIC_ORIGIN} takes an origin such as "
                    f"https://cramera.example.org, not {parsed.public_origin!r}"
                )
            if host not in LOCAL_HOSTS:
                parser.error(
                    f"{Option.PUBLIC_ORIGIN} serves the proxy on this machine, so the "
                    f"viewer listens on a loopback address only"
                )
        return cls(
            locations=locations,
            host=host,
            port=parsed.port,
            extra_scenes=parsed.scenes,
            demo_scenes=parsed.demo_scenes,
            open_browser=not parsed.no_browser,
            replace_viewer=not parsed.keep_running,
            branding=branding,
            public_origin=public_origin,
            ends_with_input=parsed.lifeline,
            deployment=(environment or {}).get(EnvironmentVariable.DEPLOYMENT),
        )

    def scene_directories(self) -> List[SceneDirectory]:
        """
        :return: The local, shared and demo scenes directories.
        """
        directories = [SceneDirectory(self.locations.local_scenes, SceneSource.LOCAL)]
        shared = [self.locations.shared_scenes, *self.extra_scenes]
        directories += [
            SceneDirectory(path, SceneSource.SHARED)
            for path in shared
            if path is not None
        ]
        if self.demo_scenes is not None:
            directories.append(SceneDirectory(self.demo_scenes, SceneSource.DEMO))
        return directories

    def server(self) -> HttpServer:
        """
        :return: The viewer's server, not yet started.
        """
        application = ViewerApplication(
            catalog=SceneCatalog(self.scene_directories()),
            locations=self.locations,
            plugins=PluginLoader().load(),
            branding=self.branding,
            deployment=self.deployment,
        )
        answerer = RequestAnswerer(router=application.router(), policy=self.policy())
        return HttpServer(
            answerer=answerer,
            host=self.host,
            port=self.port,
            on_stop=[application.close],
        )

    def policy(self) -> OriginPolicy:
        """
        :return: Which pages and host names the viewer answers: behind a proxy those of
            its public origin, every request as another device's that may control runs;
            on the network other devices with the key; else this machine's only.
        """
        if self.public_origin is not None:
            return OriginPolicy(
                allowed_hosts=LOCAL_HOSTS | {urlsplit(self.public_origin).hostname},
                proxied=True,
            )
        return OriginPolicy(key=self.network_key())

    def network_key(self) -> Optional[str]:
        """
        :return: The key other devices carry, when the viewer listens beyond this
            machine; None otherwise.
        """
        if self.host in LOCAL_HOSTS:
            return None
        return NetworkKey.of(self.locations).value()

    def announce_network(self, server: HttpServer) -> None:
        """
        Say where other devices of the network open the started server, with its key,
        when it listens beyond this machine.

        :param server: The started server.
        """
        if self.public_origin is not None:
            logger.info(
                "pages of %s reach this viewer through its proxy; they may watch, "
                "start, stop and step runs",
                self.public_origin,
            )
            return
        network = server.network_url()
        key = self.network_key()
        if network is not None and key is not None:
            logger.info(
                "other devices of this network open %s/?%s=%s; they may watch, but "
                "change nothing",
                network,
                KEY_PARAMETER,
                key,
            )
        elif self.host == EVERY_ADDRESS:
            logger.warning(
                "this machine has no network address other devices could open"
            )

    def start_server(self) -> HttpServer:
        """
        Start the viewer's server; when the port is held by another cramera viewer and
        :attr:`replace_viewer` is set, that viewer is stopped first.

        :return: The started server.
        :raises OSError: When the port is in use and cannot be taken over.
        """
        server = self.server()
        try:
            return server.start()
        except OSError as error:
            if error.errno != errno.EADDRINUSE or not self.replace_viewer:
                raise
        PortOccupant(self.host, self.port).replace_viewer()
        return server.start()


# %% origins


def origin_of(address: str) -> Optional[str]:
    """
    :param address: An address naming an origin, such as ``https://example.org``.
    :return: The origin, scheme and host with its port; None when the address is no
        http or https origin, or names a path, a query or a fragment.
    """
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    return f"{parts.scheme}://{parts.netloc}"


# %% command


def main(arguments: Optional[Sequence[str]] = None) -> None:
    """
    Run the subcommand the arguments start with, or else serve the viewer.

    :param arguments: The command-line arguments; the process arguments when None.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    given = list(arguments if arguments is not None else sys.argv[1:])
    subcommands: Mapping[str, Callable[[Sequence[str]], None]] = {
        Subcommand.CONNECT: connect,
        Subcommand.PIPE: pipe,
    }
    if given and given[0] in subcommands:
        subcommands[given[0]](given[1:])
        return
    serve(given)


def serve(arguments: Sequence[str]) -> None:
    """
    Serve the viewer until the process is interrupted or terminated, or, when asked to,
    until its standard input closes.

    :param arguments: The command-line arguments without the program name.
    """
    locations = DataLocations.from_environment(os.environ, Path.home())
    configuration = ViewerConfiguration.from_arguments(arguments, locations, os.environ)
    # The stop signals are handled, not blocked: every thread started here and every
    # process such a thread starts, such as a run on the real robot, would inherit a
    # blocked signal, and a run blocking SIGTERM ignored its Stop.
    stopped = threading.Event()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, lambda *received: stopped.set())
    if configuration.ends_with_input:
        Lifeline(sys.stdin.fileno()).watch()
    try:
        server = configuration.start_server()
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        logger.error(
            "port %d is in use; stop the program listening there, or choose another %s",
            configuration.port,
            Option.PORT,
        )
        sys.exit(1)
    try:
        logger.info("%s %s", Announcement.SERVING, server.url())
        configuration.announce_network(server)
        if configuration.open_browser:
            webbrowser.open(server.url())
        stopped.wait()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
