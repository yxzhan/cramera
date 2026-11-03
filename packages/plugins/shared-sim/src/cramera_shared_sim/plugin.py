"""
The Shared Sim plugin of the cramera viewer: a **Shared Sim** page in the top bar that
shows Robot Lobby (MuJoCo and Filament in the browser, a physics world shared by
everyone connected) under cramera's header.

The lobby is managed and published on its own, at https://yxzhan.github.io/robot-lobby/;
the page embeds it in a frame (``web/shared-sim-main.js``). The plugin has browser files
only and no routes, so it works the same on a running viewer and on a static site
exported with ``cramera-export-site --plugin shared-sim``.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from cramera.plugins import PageEntry, ViewerPlugin
from cramera_protocol.viewer import NavigationGroup

NAME = "shared-sim"
"""
The plugin's name and route segment.
"""

WEB_DIRECTORY = Path(__file__).parent / "web"
"""
The plugin's browser files.
"""

PAGE = "shared-sim.html"
"""
The plugin's page, relative to :data:`WEB_DIRECTORY`.
"""


class SharedSimPlugin(ViewerPlugin):
    """
    The Shared Sim page.
    """

    def name(self) -> str:
        return NAME

    def version(self) -> str:
        return version("cramera-shared-sim")

    def web_directory(self) -> Path:
        return WEB_DIRECTORY

    def navigation(self) -> PageEntry:
        return PageEntry("Shared Sim", NavigationGroup.LABORATORY, PAGE)
