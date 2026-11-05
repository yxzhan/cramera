"""
``cramera connect`` and ``cramera pipe``: a viewer on another machine, or in a container
there, opened in this machine's browser through ``ssh``; and the splice that carries
each connection.

A fake ``ssh`` runs the far side's commands on this machine, so a browser connection is
carried end to end: through the fake, ``cramera pipe`` and a real viewer.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest

from cramera.application import ViewerApplication
from cramera.command_line import main
from cramera.exceptions import RemoteViewerDidNotStart, SshConnectionFailed
from cramera.http.addresses import LOOPBACK
from cramera.http.messages import HttpMethod
from cramera.http.origin import OriginPolicy
from cramera.http.routing import ExactRoute, Router
from cramera.http.server import HttpServer, RequestAnswerer
from cramera.live_relay import LiveRelay
from cramera.remote_connection import (
    ConnectConfiguration,
    ConnectOption,
    DockerWord,
    FarSide,
    PipeOption,
    Program,
    SshConnection,
    SshControlRequest,
    SshFlag,
    StartedViewer,
)
from cramera.splice import ByteEnd, SocketEnd, Splice, StreamEnd
from cramera.viewer_options import DEFAULT_PORT, Announcement, Option, Subcommand
from cramera_protocol.live import LiveRoute
from cramera_protocol.viewer import EnvironmentVariable, ViewerRoute
from .test_live_relay import echo

DATASET = Path(__file__).parent / "dataset" / "remote_connection"
"""
The fake ``ssh`` and the fake far-side viewer.
"""

FAKE_SSH = DATASET / "fake_ssh.py"
"""
The executable that stands in for ``ssh``.
"""

FAKE_VIEWER = DATASET / "fake_viewer.py"
"""
The script that stands in for a viewer started on the far side.
"""

CRAMERA = [sys.executable, "-m", "cramera.command_line"]
"""
How the far side runs ``cramera`` in the tests: this interpreter, with this checkout.
"""

# %% fixtures


def free_port() -> int:
    """
    :return: A port of this machine nothing listens on.
    """
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


@pytest.fixture
def ssh_calls(tmp_path, monkeypatch) -> Path:
    """
    :return: The file the fake ``ssh`` records its calls in.
    """
    calls = tmp_path / "ssh_calls"
    monkeypatch.setenv("FAKE_SSH_CALLS", str(calls))
    return calls


def recorded(calls: Path) -> list[list[str]]:
    """
    :param calls: The fake ``ssh``'s record.
    :return: The arguments of every call.
    """
    return [shlex.split(line) for line in calls.read_text().splitlines()]


@pytest.fixture
def publisher():
    """
    :return: A fake live publisher on this machine that echoes every request.
    """
    router = Router()
    for route in LiveRoute:
        router.add(ExactRoute(HttpMethod.GET, route, echo))
        router.add(ExactRoute(HttpMethod.POST, route, echo))
    with HttpServer(RequestAnswerer(router=router, policy=OriginPolicy())) as server:
        yield server


@pytest.fixture
def remote_viewer(catalog, locations, publisher):
    """
    :return: A viewer as it runs on the far side, on its loopback address and with the
        policy of a viewer that serves this machine only, relaying the fake publisher.
    """
    application = ViewerApplication(
        catalog=catalog,
        locations=locations,
        live_relay=LiveRelay(port=publisher.bound_port()),
    )
    answerer = RequestAnswerer(router=application.router(), policy=OriginPolicy())
    with HttpServer(answerer, host=LOOPBACK, port=free_port()) as server:
        yield server


def configuration(viewer_port: int, container: str | None = "lab", **changes):
    """
    :param viewer_port: The port the far side's viewer listens on.
    :param container: The container it runs in, or None.
    :return: A connection through the fake ``ssh``.
    """
    return ConnectConfiguration(
        destination="me@server",
        far_side=FarSide(container=container, cramera=list(CRAMERA)),
        port=viewer_port,
        local_port=free_port(),
        open_browser=False,
        ssh=str(FAKE_SSH),
        **changes,
    )


# %% splice


class ResettingEnd(ByteEnd):
    """
    An end whose other side resets the connection as soon as it is read.
    """

    def read(self) -> bytes:
        raise ConnectionResetError()

    def write(self, data: bytes) -> None:
        raise BrokenPipeError()

    def finish_writing(self) -> None:
        raise BrokenPipeError()


def test_a_splice_carries_both_directions_until_each_ends():
    browser, carried = socket.socketpair()
    into_process_read, into_process_write = os.pipe()
    out_of_process_read, out_of_process_write = os.pipe()
    process_side = StreamEnd(
        source=open(out_of_process_read, "rb", buffering=0),
        sink=open(into_process_write, "wb"),
    )
    splice = threading.Thread(target=Splice(SocketEnd(carried), process_side).run)
    splice.start()
    browser.sendall(b"request")
    browser.shutdown(socket.SHUT_WR)
    with open(into_process_read, "rb") as received_by_process:
        assert received_by_process.read() == b"request"
    with open(out_of_process_write, "wb") as sent_by_process:
        sent_by_process.write(b"answer")
    assert browser.recv(100) == b"answer"
    assert browser.recv(100) == b""
    splice.join(timeout=5)
    assert not splice.is_alive()
    browser.close()
    carried.close()


def test_a_side_that_resets_ends_the_splice_instead_of_failing():
    first, second = socket.socketpair()
    first.close()
    Splice(SocketEnd(second), ResettingEnd()).run()
    second.close()


# %% far side


def test_the_far_side_runs_cramera_on_the_machine_itself():
    assert FarSide().command([Subcommand.PIPE]) == [Program.CRAMERA, Subcommand.PIPE]


def test_the_far_side_runs_cramera_in_its_container():
    far_side = FarSide(container="lab", cramera=["/opt/venv/bin/cramera"])
    assert far_side.command([Subcommand.PIPE]) == [
        Program.DOCKER,
        DockerWord.EXEC,
        DockerWord.INTERACTIVE,
        "lab",
        "/opt/venv/bin/cramera",
        Subcommand.PIPE,
    ]


def test_a_far_side_command_reaches_ssh_as_one_quoted_command(tmp_path):
    ssh = SshConnection("me@server", tmp_path / "control")
    command = ssh.command(["cramera", Subcommand.PIPE, "--port", "8711 8712"])
    assert command[:-1] == [
        Program.SSH,
        SshFlag.CONTROL_SOCKET,
        str(tmp_path / "control"),
        "me@server",
    ]
    assert shlex.split(command[-1]) == [
        "cramera",
        Subcommand.PIPE,
        "--port",
        "8711 8712",
    ]


# %% configuration


def test_the_browser_opens_the_viewer_s_port_by_default():
    parsed = ConnectConfiguration.from_arguments(["me@server"])
    assert parsed.port == DEFAULT_PORT
    assert parsed.local_port == DEFAULT_PORT
    assert parsed.far_side == FarSide()
    assert parsed.url() == f"http://{LOOPBACK}:{DEFAULT_PORT}"
    assert parsed.open_browser
    assert not parsed.start


def test_the_options_choose_container_ports_start_and_remote_command():
    parsed = ConnectConfiguration.from_arguments(
        [
            "me@server",
            ConnectOption.CONTAINER,
            "lab",
            ConnectOption.PORT,
            "8712",
            ConnectOption.LOCAL_PORT,
            "18711",
            ConnectOption.START,
            ConnectOption.NO_BROWSER,
            ConnectOption.REMOTE_COMMAND,
            "/opt/venv/bin/cramera --scenes /data",
        ]
    )
    assert parsed.far_side == FarSide(
        container="lab", cramera=["/opt/venv/bin/cramera", "--scenes", "/data"]
    )
    assert (parsed.port, parsed.local_port) == (8712, 18711)
    assert parsed.start
    assert not parsed.open_browser


def test_ssh_forwards_the_viewer_s_port_from_this_machine_s_loopback_address():
    parsed = ConnectConfiguration.from_arguments(
        ["me@server", ConnectOption.LOCAL_PORT, "18711"]
    )
    assert parsed.forwards() == [f"{LOOPBACK}:18711:{LOOPBACK}:{DEFAULT_PORT}"]


def test_ssh_forwards_nothing_for_a_container():
    parsed = ConnectConfiguration.from_arguments(
        ["me@server", ConnectOption.CONTAINER, "lab"]
    )
    assert parsed.forwards() == []


# %% connecting


def test_the_viewer_s_port_is_forwarded_by_the_shared_connection(ssh_calls, tmp_path):
    connection = configuration(DEFAULT_PORT, container=None)
    with connection.connected() as url:
        assert url == connection.url()
    opened, closed = recorded(ssh_calls)
    assert opened[-1] == "me@server"
    assert [SshFlag.LOCAL_FORWARD, connection.forwards()[0]] == opened[-3:-1]
    assert {SshFlag.BACKGROUND, SshFlag.MASTER, SshFlag.NO_COMMAND} <= set(opened)
    assert closed[-3:] == [SshFlag.CONTROL_REQUEST, SshControlRequest.EXIT, "me@server"]


def test_a_destination_ssh_cannot_reach_fails_the_connection(ssh_calls):
    connection = configuration(DEFAULT_PORT, container=None)
    unreachable = ConnectConfiguration(
        destination="unreachable",
        port=connection.port,
        local_port=connection.local_port,
        open_browser=False,
        ssh=connection.ssh,
    )
    with pytest.raises(SshConnectionFailed) as raised:
        with unreachable.connected():
            pass
    assert raised.value.destination == "unreachable"


def test_a_page_in_a_container_is_carried_to_the_viewer(ssh_calls, remote_viewer):
    connection = configuration(remote_viewer.bound_port())
    with connection.connected() as url:
        with urllib.request.urlopen(url + ViewerRoute.INFO) as answer:
            assert answer.status == HTTPStatus.OK
        with urllib.request.urlopen(url + "/") as answer:
            assert answer.status == HTTPStatus.OK
    pipes = [call for call in recorded(ssh_calls) if Subcommand.PIPE in call[-1]]
    assert len(pipes) == 2
    assert shlex.split(pipes[0][-1]) == connection.far_side.command(
        connection.pipe_arguments()
    )


def test_a_carried_page_controls_the_run_as_the_far_side_s_own(
    ssh_calls, remote_viewer
):
    connection = configuration(remote_viewer.bound_port())
    body = json.dumps({"name": "run"}).encode()
    with connection.connected() as url:
        request = urllib.request.Request(
            f"{url}{ViewerRoute.LIVE_RELAY}{LiveRoute.RECORDING_SAVE.lstrip('/')}",
            data=body,
            headers={"Content-Type": "application/json", "Origin": url},
            method=HttpMethod.POST,
        )
        with urllib.request.urlopen(request) as answer:
            echoed = json.loads(answer.read())
    assert echoed["route"] == LiveRoute.RECORDING_SAVE
    assert echoed["body"] == body.decode()


# %% starting the viewer


@pytest.fixture
def viewer_ended(tmp_path, monkeypatch) -> Path:
    """
    :return: The file the fake far-side viewer writes once its input closed.
    """
    ended = tmp_path / "viewer_ended"
    monkeypatch.setenv("FAKE_VIEWER_ENDED", str(ended))
    return ended


def test_a_started_viewer_ends_with_the_connection(ssh_calls, viewer_ended, capfd):
    connection = ConnectConfiguration(
        destination="me@server",
        far_side=FarSide(container="lab", cramera=[sys.executable, str(FAKE_VIEWER)]),
        local_port=free_port(),
        start=True,
        open_browser=False,
        ssh=str(FAKE_SSH),
    )
    with connection.connected():
        assert not viewer_ended.exists()
    assert viewer_ended.read_text().split() == connection.viewer_arguments()
    assert Announcement.SERVING in capfd.readouterr().err


def test_a_viewer_that_ends_before_it_serves_fails_the_start(ssh_calls):
    viewer = StartedViewer([sys.executable, str(FAKE_VIEWER), "fail"])
    with pytest.raises(RemoteViewerDidNotStart):
        viewer.start()


def test_the_viewer_ends_when_its_lifeline_closes(tmp_path):
    environment = {**os.environ, EnvironmentVariable.DATA: str(tmp_path)}
    process = subprocess.Popen(
        [*CRAMERA, Option.PORT, "0", Option.NO_BROWSER, Option.LIFELINE],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    next(
        line
        for line in iter(process.stderr.readline, "")
        if Announcement.SERVING in line
    )
    process.stdin.close()
    assert process.wait(timeout=20) == 0


# %% pipe


def test_cramera_pipe_joins_its_input_and_output_to_the_viewer(remote_viewer):
    request = (
        f"GET {ViewerRoute.INFO} HTTP/1.1\r\nHost: localhost\r\n"
        f"Connection: close\r\n\r\n"
    )
    answer = subprocess.run(
        [*CRAMERA, Subcommand.PIPE, PipeOption.PORT, str(remote_viewer.bound_port())],
        input=request.encode(),
        capture_output=True,
        timeout=20,
    )
    status_line = answer.stdout.split(b"\r\n", 1)[0].decode()
    assert status_line.split(" ", 2)[1] == str(HTTPStatus.OK.value)


# %% commands


@pytest.fixture
def restored_signal_handlers():
    """
    Restore the interrupt and termination handlers ``cramera connect`` replaces.
    """
    handlers = {
        stop_signal: signal.getsignal(stop_signal)
        for stop_signal in (signal.SIGINT, signal.SIGTERM)
    }
    yield
    for stop_signal, handler in handlers.items():
        signal.signal(stop_signal, handler)


def interrupt_once_connected(calls: Path) -> threading.Thread:
    """
    :param calls: The fake ``ssh``'s record.
    :return: A started thread that interrupts this process once the shared connection
        is opened.
    """

    def interrupt() -> None:
        while not calls.exists():
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def ssh_on_path(tmp_path, monkeypatch, ssh_calls) -> Path:
    """
    :return: The fake ``ssh``'s record, with the fake first on the ``PATH`` as ``ssh``.
    """
    programs = tmp_path / "programs"
    programs.mkdir()
    (programs / Program.SSH).symlink_to(FAKE_SSH)
    monkeypatch.setenv("PATH", f"{programs}{os.pathsep}{os.environ['PATH']}")
    return ssh_calls


def test_cramera_connect_holds_the_connection_until_interrupted(
    ssh_on_path, restored_signal_handlers
):
    interrupt_once_connected(ssh_on_path)
    main(
        [
            Subcommand.CONNECT,
            "me@server",
            ConnectOption.LOCAL_PORT,
            str(free_port()),
            ConnectOption.NO_BROWSER,
        ]
    )
    assert recorded(ssh_on_path)[-1][-3:] == [
        SshFlag.CONTROL_REQUEST,
        SshControlRequest.EXIT,
        "me@server",
    ]


def test_cramera_connect_fails_when_ssh_cannot_connect(
    ssh_on_path, restored_signal_handlers
):
    with pytest.raises(SystemExit) as exited:
        main([Subcommand.CONNECT, "unreachable", ConnectOption.NO_BROWSER])
    assert exited.value.code == 1


def test_cramera_pipe_runs_in_process_as_the_far_side(remote_viewer, tmp_path):
    request = (
        f"GET {ViewerRoute.INFO} HTTP/1.1\r\nHost: localhost\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    readable, writable = os.pipe()
    os.write(writable, request)
    os.close(writable)
    answer_file = tmp_path / "answer"
    with open(readable) as standard_input, open(answer_file, "w") as standard_output:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sys, "stdin", standard_input)
            patch.setattr(sys, "stdout", standard_output)
            main([Subcommand.PIPE, PipeOption.PORT, str(remote_viewer.bound_port())])
    status_line = answer_file.read_bytes().split(b"\r\n", 1)[0].decode()
    assert status_line.split(" ", 2)[1] == str(HTTPStatus.OK.value)
