"""
``cramera connect``: open a viewer that runs on another machine, or in a container
there, in this machine's browser, through the user's own ``ssh``; and ``cramera pipe``,
its far side inside a container.

Authentication, keys and ``~/.ssh/config`` stay with ``ssh``. Nothing on the far side
listens beyond its loopback address: without a container, ``ssh`` forwards the viewer's
port; with one, every browser connection is carried by its own ``docker exec`` of
``cramera pipe`` into the container's loopback. This machine listens on its loopback
address, so the page's requests name ``localhost`` and the viewer counts them as its own
machine's.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import shlex
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import webbrowser
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import IO, Iterator, List, Optional, Sequence, Type

from cramera.exceptions import RemoteViewerDidNotStart, SshConnectionFailed
from cramera.http.addresses import LOOPBACK, url_of
from cramera.splice import SocketEnd, Splice, StreamEnd
from cramera.viewer_options import DEFAULT_PORT, Announcement, Option, Subcommand

logger = logging.getLogger(__name__)
"""
The logger of the command.
"""

# %% names


class ConnectOption(StrEnum):
    """
    The options of ``cramera connect``.
    """

    CONTAINER = "--container"
    """
    The Docker container on the far side the viewer runs in.
    """

    PORT = "--port"
    """
    The port the viewer listens on, on the far side.
    """

    LOCAL_PORT = "--local-port"
    """
    The port this machine's browser opens the viewer at; the viewer's port by default.
    """

    START = "--start"
    """
    Also start the viewer on the far side, and end it when the connection closes.
    """

    NO_BROWSER = "--no-browser"
    """
    Do not open the viewer in a browser.
    """

    REMOTE_COMMAND = "--remote-command"
    """
    How the far side runs ``cramera``, such as ``/opt/venv/bin/cramera``.
    """


class PipeOption(StrEnum):
    """
    The options of ``cramera pipe``.
    """

    PORT = "--port"
    """
    The port of the viewer on this machine the standard input and output are joined to.
    """


class Program(StrEnum):
    """
    The programs the command runs.
    """

    SSH = "ssh"
    """
    The SSH client.
    """

    DOCKER = "docker"
    """
    The Docker client on the far side.
    """

    CRAMERA = "cramera"
    """
    The ``cramera`` command on the far side.
    """


class SshFlag(StrEnum):
    """
    The ``ssh`` flags the command passes.
    """

    BACKGROUND = "-f"
    """
    Go to the background once connected and every forwarding is in place.
    """

    NO_COMMAND = "-N"
    """
    Run no command; only hold the connection.
    """

    MASTER = "-M"
    """
    Hold a connection that later ``ssh`` calls share.
    """

    CONTROL_SOCKET = "-S"
    """
    The socket the shared connection is reached at.
    """

    SETTING = "-o"
    """
    A configuration setting.
    """

    LOCAL_FORWARD = "-L"
    """
    Forward a port of this machine to an address of the far side.
    """

    CONTROL_REQUEST = "-O"
    """
    Ask the shared connection to do something.
    """


class SshSetting(StrEnum):
    """
    The configuration settings the command gives ``ssh``.
    """

    EXIT_ON_FORWARD_FAILURE = "ExitOnForwardFailure=yes"
    """
    Fail to connect when a port cannot be forwarded, such as one already in use.
    """


class SshControlRequest(StrEnum):
    """
    The requests the command makes of the shared connection.
    """

    EXIT = "exit"
    """
    Close the connection.
    """


class DockerWord(StrEnum):
    """
    The words of the ``docker`` command that runs a program in a container.
    """

    EXEC = "exec"
    """
    Run a program in a running container.
    """

    INTERACTIVE = "-i"
    """
    Keep the program's standard input open.
    """


CONTROL_SOCKET_NAME = "control"
"""
The file name of the shared connection's socket, inside a directory of its own.
"""

# %% ssh


@dataclass(frozen=True)
class SshConnection:
    """
    One connection to the far side through the user's ``ssh``, which every later command
    shares, so the user authenticates once.
    """

    destination: str
    """
    The destination as ``ssh`` takes it, such as ``user@server`` or a host of
    ``~/.ssh/config``.
    """

    control_socket: Path
    """
    The socket the shared connection is reached at.
    """

    ssh: str = Program.SSH
    """
    The ``ssh`` program.
    """

    def open(self, forwards: Sequence[str]) -> None:
        """
        Connect, asking the user for a password or passphrase as ``ssh`` does, and leave
        the connection in the background.

        :param forwards: The ports to forward, as ``-L`` takes them.
        :raises SshConnectionFailed: When ``ssh`` could not connect or forward.
        """
        arguments = [
            self.ssh,
            SshFlag.BACKGROUND,
            SshFlag.NO_COMMAND,
            SshFlag.MASTER,
            SshFlag.CONTROL_SOCKET,
            str(self.control_socket),
            SshFlag.SETTING,
            SshSetting.EXIT_ON_FORWARD_FAILURE,
        ]
        for forward in forwards:
            arguments += [SshFlag.LOCAL_FORWARD, forward]
        status = subprocess.run([*arguments, self.destination]).returncode
        if status != 0:
            raise SshConnectionFailed(self.destination, status)

    def command(self, remote: Sequence[str]) -> List[str]:
        """
        :param remote: A command of the far side.
        :return: The local command that runs it through the shared connection.
        """
        return [
            self.ssh,
            SshFlag.CONTROL_SOCKET,
            str(self.control_socket),
            self.destination,
            shlex.join(remote),
        ]

    def close(self) -> None:
        """
        Close the shared connection, and with it every forwarding.
        """
        subprocess.run(
            [
                self.ssh,
                SshFlag.CONTROL_SOCKET,
                str(self.control_socket),
                SshFlag.CONTROL_REQUEST,
                SshControlRequest.EXIT,
                self.destination,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@dataclass(frozen=True)
class FarSide:
    """
    Where the viewer runs on the far side: on the machine itself, or in one of its
    containers.
    """

    container: Optional[str] = None
    """
    The container; None for the machine itself.
    """

    cramera: List[str] = field(default_factory=lambda: [Program.CRAMERA.value])
    """
    How the far side runs ``cramera``.
    """

    def command(self, arguments: Sequence[str]) -> List[str]:
        """
        :param arguments: The arguments of ``cramera``.
        :return: The command that runs ``cramera`` with them where the viewer runs.
        """
        if self.container is None:
            return [*self.cramera, *arguments]
        return [
            Program.DOCKER,
            DockerWord.EXEC,
            DockerWord.INTERACTIVE,
            self.container,
            *self.cramera,
            *arguments,
        ]


# %% carrying connections


class CarriedConnection(socketserver.BaseRequestHandler):
    """
    Carries one browser connection through its own process.
    """

    carrier: List[str]
    """
    The command whose standard input and output reach the viewer's port.
    """

    def handle(self) -> None:
        process = subprocess.Popen(
            self.carrier, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        Splice(SocketEnd(self.request), StreamEnd(process.stdout, process.stdin)).run()
        process.stdout.close()
        process.wait()


class CarryingServer(socketserver.ThreadingTCPServer):
    """
    Accepts browser connections, each carried in a thread of its own.
    """

    daemon_threads = True
    allow_reuse_address = True


@dataclass
class ConnectionCarrier:
    """
    Listens on this machine's loopback address and carries every connection to the
    viewer through a process of its own, between :meth:`start` and :meth:`stop`.
    """

    port: int
    """
    The port to listen on.
    """

    carrier: List[str]
    """
    The command whose standard input and output reach the viewer's port.
    """

    server: Optional[CarryingServer] = field(default=None, init=False)
    """
    The listening server while started.
    """

    thread: Optional[threading.Thread] = field(default=None, init=False)
    """
    The accepting thread while started.
    """

    def start(self) -> None:
        """
        Listen and start accepting.
        """
        handler: Type[CarriedConnection] = type(
            "BoundCarriedConnection", (CarriedConnection,), {"carrier": self.carrier}
        )
        self.server = CarryingServer((LOOPBACK, self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """
        Stop accepting and close the listening socket; carried connections end with
        their processes.
        """
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


# %% starting the viewer


@dataclass
class StartedViewer:
    """
    A viewer started on the far side, which ends when its standard input closes.
    """

    command: List[str]
    """
    The local command that starts it.
    """

    process: Optional[subprocess.Popen] = field(default=None, init=False)
    """
    The running command while started.
    """

    def start(self) -> None:
        """
        Start the viewer and wait until it serves, passing on what it says.

        :raises RemoteViewerDidNotStart: When it ends before it serves.
        """
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for line in self.process.stderr:
            self.pass_on(line)
            if line.decode(errors="replace").startswith(Announcement.SERVING):
                break
        else:
            self.process.wait()
            raise RemoteViewerDidNotStart(shlex.join(self.command))
        threading.Thread(
            target=self.pass_on_all, args=(self.process.stderr,), daemon=True
        ).start()

    @classmethod
    def pass_on_all(cls, messages: IO[bytes]) -> None:
        """
        :param messages: The viewer's standard error, passed on until it ends.
        """
        for line in messages:
            cls.pass_on(line)

    @staticmethod
    def pass_on(line: bytes) -> None:
        """
        :param line: A line the viewer said, written to this process's standard error.
        """
        sys.stderr.buffer.write(line)
        sys.stderr.buffer.flush()

    def stop(self) -> None:
        """
        Close the viewer's standard input, which ends it, and wait for the command.
        """
        self.process.stdin.close()
        self.process.wait()


# %% connect


@dataclass(frozen=True)
class ConnectConfiguration:
    """
    What ``cramera connect`` was asked to connect to.
    """

    destination: str
    """
    The machine serving the viewer, as ``ssh`` takes it.
    """

    far_side: FarSide = field(default_factory=FarSide)
    """
    Where the viewer runs there.
    """

    port: int = DEFAULT_PORT
    """
    The port the viewer listens on, on the far side.
    """

    local_port: int = DEFAULT_PORT
    """
    The port this machine's browser opens the viewer at.
    """

    start: bool = False
    """
    Whether to start the viewer on the far side, ending it with the connection.
    """

    open_browser: bool = True
    """
    Whether to open the viewer in a browser.
    """

    ssh: str = Program.SSH
    """
    The ``ssh`` program.
    """

    @classmethod
    def from_arguments(cls, arguments: Sequence[str]) -> ConnectConfiguration:
        """
        :param arguments: The arguments after ``cramera connect``.
        :return: The configuration they describe.
        """
        parser = argparse.ArgumentParser(
            prog=f"{Program.CRAMERA} {Subcommand.CONNECT}",
            description=(
                "Open a cramera viewer that runs on another machine, or in a container "
                "there, in this machine's browser, through ssh."
            ),
        )
        parser.add_argument("destination", help="user@server, as ssh takes it")
        parser.add_argument(ConnectOption.CONTAINER, default=None)
        parser.add_argument(ConnectOption.PORT, type=int, default=DEFAULT_PORT)
        parser.add_argument(ConnectOption.LOCAL_PORT, type=int, default=None)
        parser.add_argument(ConnectOption.START, action="store_true")
        parser.add_argument(ConnectOption.NO_BROWSER, action="store_true")
        parser.add_argument(ConnectOption.REMOTE_COMMAND, default=Program.CRAMERA)
        parsed = parser.parse_args(list(arguments))
        return cls(
            destination=parsed.destination,
            far_side=FarSide(
                container=parsed.container,
                cramera=shlex.split(parsed.remote_command),
            ),
            port=parsed.port,
            local_port=parsed.port if parsed.local_port is None else parsed.local_port,
            start=parsed.start,
            open_browser=not parsed.no_browser,
        )

    def url(self) -> str:
        """
        :return: The address this machine's browser opens the viewer at.
        """
        return url_of(LOOPBACK, self.local_port)

    def forwards(self) -> List[str]:
        """
        :return: The ports ``ssh`` forwards: the viewer's, when it runs on the machine
            itself; none for a container, whose connections are carried one by one.
        """
        if self.far_side.container is not None:
            return []
        return [f"{LOOPBACK}:{self.local_port}:{LOOPBACK}:{self.port}"]

    def viewer_arguments(self) -> List[str]:
        """
        :return: The arguments that start the viewer on the far side.
        """
        return [Option.NO_BROWSER, Option.PORT, str(self.port), Option.LIFELINE]

    def pipe_arguments(self) -> List[str]:
        """
        :return: The arguments that join a carrying process to the viewer's port.
        """
        return [Subcommand.PIPE, PipeOption.PORT, str(self.port)]

    @contextlib.contextmanager
    def connected(self) -> Iterator[str]:
        """
        Connect, start the viewer when asked to, and carry or forward the browser's
        connections; everything is closed again in reverse order on leaving.

        :return: The address this machine's browser opens the viewer at.
        :raises SshConnectionFailed: When ``ssh`` could not connect or forward.
        :raises RemoteViewerDidNotStart: When the started viewer ended before it served.
        """
        with tempfile.TemporaryDirectory(prefix="cramera-ssh-") as directory:
            ssh = SshConnection(
                self.destination, Path(directory) / CONTROL_SOCKET_NAME, self.ssh
            )
            ssh.open(self.forwards())
            with contextlib.ExitStack() as opened:
                opened.callback(ssh.close)
                if self.start:
                    viewer = StartedViewer(
                        ssh.command(self.far_side.command(self.viewer_arguments()))
                    )
                    viewer.start()
                    opened.callback(viewer.stop)
                if self.far_side.container is not None:
                    carrier = ConnectionCarrier(
                        self.local_port,
                        ssh.command(self.far_side.command(self.pipe_arguments())),
                    )
                    carrier.start()
                    opened.callback(carrier.stop)
                yield self.url()


def connect(arguments: Sequence[str]) -> None:
    """
    ``cramera connect``: hold the connection until the process is interrupted or
    terminated.

    :param arguments: The arguments after ``cramera connect``.
    """
    configuration = ConnectConfiguration.from_arguments(arguments)
    stop = threading.Event()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, lambda *received: stop.set())
    # a connection that could not be made is said as the command's failure, not as a
    # traceback
    try:
        with configuration.connected() as url:
            logger.info(
                "%s shows the viewer of %s; Ctrl-C closes the connection",
                url,
                configuration.destination,
            )
            if configuration.open_browser:
                webbrowser.open(url)
            stop.wait()
    except (SshConnectionFailed, RemoteViewerDidNotStart) as error:
        logger.error("%s", error.message())
        sys.exit(1)


# %% pipe


def pipe(arguments: Sequence[str]) -> None:
    """
    ``cramera pipe``: join the standard input and output to a viewer's port on this
    machine until both directions have ended.

    :param arguments: The arguments after ``cramera pipe``.
    """
    parser = argparse.ArgumentParser(
        prog=f"{Program.CRAMERA} {Subcommand.PIPE}",
        description=(
            "Join the standard input and output to the port of a cramera viewer on "
            "this machine; the far side of cramera connect inside a container."
        ),
    )
    parser.add_argument(PipeOption.PORT, type=int, default=DEFAULT_PORT)
    parsed = parser.parse_args(list(arguments))
    with socket.create_connection((LOOPBACK, parsed.port)) as connection:
        Splice(
            SocketEnd(connection), StreamEnd(sys.stdin.buffer, sys.stdout.buffer)
        ).run()
