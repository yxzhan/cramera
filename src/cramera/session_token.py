"""
The access token of the Jupyter server this viewer runs next to.

Behind JupyterHub or BinderHub the viewer is reached through the Jupyter server's
proxy, so a link that brings someone else into the session has to carry that server's
token. The page cannot know it when it was opened without one in its url (from the
launcher, say), but the viewer process can read it where ``jupyter server list`` does:
the file every running Jupyter server writes into the Jupyter runtime directory.

The environment is no substitute: JupyterHub's ``JUPYTERHUB_API_TOKEN`` is the
server's credential towards the hub, and under BinderHub it is not the token the
server accepts in a url.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jupyter_core.paths import jupyter_runtime_dir
from typing_extensions import List, Optional

SERVER_FILE_PATTERN = "jpserver-*.json"
"""
The files a running Jupyter server describes itself in, one per server.
"""


class ServerFileKey(StrEnum):
    """
    The fields of a Jupyter server file this module reads.
    """

    TOKEN = "token"
    BASE_URL = "base_url"
    PID = "pid"


SERVICE_PREFIX_VARIABLE = "JUPYTERHUB_SERVICE_PREFIX"
"""
The environment variable JupyterHub sets to the url prefix of the user's server.
"""


@dataclass(frozen=True)
class RunningServer:
    """
    One Jupyter server, as it describes itself in its runtime file.
    """

    token: str
    """
    The token the server accepts in ``?token=``.
    """

    base_url: str
    """
    The url path the server is served under, e.g. ``/user/<name>/``.
    """

    process: int
    """
    The server's process id.
    """

    written: float
    """
    When the server wrote its file, as a Unix timestamp.
    """

    @classmethod
    def from_file(cls, path: Path) -> Optional[RunningServer]:
        """
        The server a runtime file describes, or None when the file cannot be read or
        names no token.

        :param path: One ``jpserver-*.json`` file.
        """
        try:
            description = json.loads(path.read_text())
            written = path.stat().st_mtime
        except (OSError, ValueError):
            # a server may be writing or removing its file at this very moment
            return None
        token = description.get(ServerFileKey.TOKEN)
        if not token:
            return None
        return cls(
            token=token,
            base_url=description.get(ServerFileKey.BASE_URL) or "/",
            process=int(description.get(ServerFileKey.PID) or 0),
            written=written,
        )

    def is_alive(self) -> bool:
        """
        Whether the server's process still runs; a server that was killed leaves its
        file behind.
        """
        processes = Path("/proc")
        if not processes.is_dir():
            return True
        return (processes / str(self.process)).exists()


def running_servers() -> List[RunningServer]:
    """
    Every live Jupyter server that has written a runtime file, newest first.
    """
    servers = [
        RunningServer.from_file(path)
        for path in Path(jupyter_runtime_dir()).glob(SERVER_FILE_PATTERN)
    ]
    live = [server for server in servers if server is not None and server.is_alive()]
    return sorted(live, key=lambda server: server.written, reverse=True)


def session_token() -> Optional[str]:
    """
    The token of the Jupyter server this viewer is proxied through, or None when no
    Jupyter server is running here.

    The server JupyterHub spawned for this user is preferred when it can be told
    apart, by its url prefix; otherwise the most recently started one.
    """
    servers = running_servers()
    if not servers:
        return None
    prefix = os.environ.get(SERVICE_PREFIX_VARIABLE)
    spawned = [server for server in servers if prefix and server.base_url == prefix]
    return (spawned or servers)[0].token
