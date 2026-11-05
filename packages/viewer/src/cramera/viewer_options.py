"""
The names the ``cramera`` command is called with: its subcommands, its options and what
it says once it serves.
"""

from __future__ import annotations

from enum import StrEnum

# %% names


class Subcommand(StrEnum):
    """
    The subcommands ``cramera`` takes in place of serving the viewer.
    """

    CONNECT = "connect"
    """
    Open a viewer that runs on another machine, or in a container there, in this
    machine's browser.
    """

    PIPE = "pipe"
    """
    Join the standard input and output to a viewer's port on this machine; the far side
    of :attr:`CONNECT` inside a container.
    """


class Option(StrEnum):
    """
    The command's options.
    """

    HOST = "--host"
    """
    The address to listen on.
    """

    LAN = "--lan"
    """
    Listen on every address of this machine, so other devices of its network, such as a
    phone, can watch.
    """

    PUBLIC_ORIGIN = "--public-origin"
    """
    The origin a password-checking reverse proxy on this machine serves the viewer at,
    such as ``https://cramera.informatik.uni-bremen.de``; its pages may start, stop and
    step runs.
    """

    PORT = "--port"
    """
    The port to listen on.
    """

    SCENES = "--scenes"
    """
    A further shared scenes directory.
    """

    DEMO_SCENES = "--demo-scenes"
    """
    A directory of demo scenes.
    """

    NO_BROWSER = "--no-browser"
    """
    Do not open the viewer in a browser.
    """

    KEEP_RUNNING = "--keep-running"
    """
    Fail when the port is in use instead of stopping the viewer that holds it.
    """

    LOGO = "--logo"
    """
    An image the top bar shows instead of the AICOR logo.
    """

    LOGO_TEXT = "--logo-text"
    """
    What that image shows, for people who cannot see it.
    """

    LIFELINE = "--lifeline"
    """
    End the viewer once its standard input closes, as it does when the SSH connection
    that started it ends.
    """


class Announcement(StrEnum):
    """
    What the command says on its standard error once a state is reached.
    """

    SERVING = "cramera serves"
    """
    The viewer listens; the address it serves follows.
    """


DEFAULT_PORT = 8711
"""
The port the viewer listens on unless told otherwise.
"""
