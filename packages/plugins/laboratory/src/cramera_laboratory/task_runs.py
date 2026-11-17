"""
Robot task demos the laboratory runs on request, each in a process of its own with the
CRAM workspace, so the viewer stays responsive and runs without CRAM itself.

The process is the plugin's own: it is started on request, its output goes to a log file
in the viewer's data directory, and it is ended on request or viewer shutdown.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from typing_extensions import List, Optional, Sequence

from cramera.cram_imports import CramImports
from cramera.locations import DataLocations
from cramera_protocol.bundle.files import BundleFile
from cramera_laboratory.resources import RESOURCES_DIRECTORY, LaboratoryResource
from cramera_protocol.laboratory.physics import LaboratoryName
from cramera_protocol.laboratory.runs import (
    DemoRoute,
    LaboratoryDemo,
    LaboratoryTask,
    TaskRun,
    TaskRunState,
    TransferOption,
)
from cramera_protocol.viewer import EnvironmentVariable, ViewerRoute

LOG_DIRECTORY = "laboratory"
"""
The folder of the viewer's data directory the demos' logs are written to.
"""

LOG_SUFFIX = ".log"
"""
The suffix of a demo's log file.
"""

ERROR_LINES = 12
"""
How many of the log's last lines a failure reports.
"""

PYTHON_MODULE_FLAG = "-m"
"""
The interpreter's flag that runs a module.
"""

BRIDGE_PACKAGES = ("cramera_bridge",)
"""
The optional package exposing the native transfer through a process boundary.
"""

STOP_TIMEOUT = 3.0
"""
Seconds a child receives to terminate before it is killed on shutdown.
"""


class ProcessEnding(StrEnum):
    """
    How a failed demo's process ended, as the first line of its error.
    """

    ENDED_BY_SIGNAL = "The demo was ended by signal {signal}."
    """
    A signal from outside ended the process, such as SIGKILL when memory runs out.
    """

    EXITED_WITH_ERROR = "The demo exited with code {code}."
    """
    The process exited with a nonzero code.
    """

    EXITED_WITHOUT_RECORDING = "The demo finished without saving its recording."
    """
    The process exited normally but saved no recording.
    """


# %% process


@dataclass
class TaskProcess:
    """
    Runs one task demo in its own process and reports how far it is.
    """

    command: List[str]
    """
    The command that runs the demo.
    """

    demo: LaboratoryDemo
    """
    Which of the laboratory's robot demos runs.
    """

    locations: DataLocations
    """
    Where the viewer keeps its scenes; the demo saves into the same local scenes.
    """

    packages: Sequence[str] = BRIDGE_PACKAGES
    """
    The packages the demo needs.
    """

    fresh_recording: bool = False
    """
    Whether every start runs the native command with a new recording name.
    """

    recording: Optional[str] = field(default=None, init=False)
    """
    The current run's recording, or None before the first run.
    """

    process: Optional[subprocess.Popen] = field(default=None, init=False)
    """
    The demo's process, once started.
    """

    ended_on_request: bool = field(default=False, init=False)
    """
    Whether the current process was ended by :meth:`stop` rather than by itself.
    """

    lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    """
    Serializes starting, stopping and reading the process.
    """

    @property
    def scene(self) -> str:
        """
        The bundle the demo saves its recording as.
        """
        return self.demo.recording.value

    @property
    def live_route(self) -> str:
        """
        The route below the laboratory's prefix that reads the running demo's live
        publisher.
        """
        return self.demo.route(DemoRoute.LIVE)

    @property
    def log(self) -> Path:
        """
        The demo's log file.
        """
        return self.locations.data / LOG_DIRECTORY / f"{self.scene}{LOG_SUFFIX}"

    @property
    def live_url_file(self) -> Path:
        """
        The current child writes its listening address here after its server starts.
        """
        return self.log.with_suffix(".live-url")

    def live_address(self) -> Optional[str]:
        """
        :return: The running child's loopback HTTP root, or None until it announces one.
        """
        if self.process is None or self.process.poll() is not None:
            return None
        try:
            address = self.live_url_file.read_text(encoding="utf-8").strip()
            parsed = urlsplit(address)
            valid = (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.port is not None
                and 0 < parsed.port <= 65535
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
                and parsed.username is None
                and parsed.password is None
            )
        except (OSError, ValueError):
            return None
        return f"http://127.0.0.1:{parsed.port}/" if valid else None

    def recording_name(self) -> str:
        """
        :return: The active recording's name, or the original recording before a run.
        """
        return self.recording or self.scene

    def available(self) -> bool:
        """
        :return: Whether every package the demo needs is installed.
        """
        if any(importlib.util.find_spec(package) is None for package in self.packages):
            return False
        if "cramera_bridge" in self.packages:
            if importlib.util.find_spec("cramera_bridge.laboratory") is None:
                return False
            if importlib.util.find_spec(LaboratoryTask.COMMAND) is None:
                return False
            command = CramImports().module(LaboratoryTask.COMMAND)
            return (
                hasattr(command, "prepare_preview")
                and hasattr(command, "runner")
                and not command.missing_libraries()
            )
        return True

    def recorded(self) -> bool:
        """
        :return: Whether the local scenes hold the demo's recording.
        """
        return (
            self.locations.local_scenes / self.recording_name() / BundleFile.SCENE
        ).is_file()

    @classmethod
    def of_demo(cls, demo: LaboratoryDemo, locations: DataLocations) -> TaskProcess:
        """
        :param demo: One of the laboratory's robot demos.
        :param locations: Where the viewer keeps its scenes.
        :return: The demo, run by the bridge's command as a module of this interpreter.
        """
        return cls(
            command=[
                sys.executable,
                PYTHON_MODULE_FLAG,
                LaboratoryTask.COMMAND.value,
                TransferOption.BUNDLE.value,
                str(RESOURCES_DIRECTORY / LaboratoryResource.BENCH),
                TransferOption.DEMO.value,
                demo.value,
            ],
            demo=demo,
            locations=locations,
            fresh_recording=True,
        )

    def state(self) -> TaskRun:
        """
        :return: How far the demo is.
        """
        with self.lock:
            return self.state_locked()

    def state_locked(self) -> TaskRun:
        """
        :return: How far the demo is; the caller holds :attr:`lock`.
        """
        available = self.available()
        result = TaskRun(
            state=TaskRunState.IDLE if available else TaskRunState.UNAVAILABLE,
            scene=self.recording_name(),
            can_run=available,
        )
        if self.process is not None and self.process.poll() is None:
            result.state = TaskRunState.RUNNING
            if self.live_address() is not None:
                result.live_url = (
                    f"{ViewerRoute.PLUGIN_API}{LaboratoryName.PLUGIN}/{self.live_route}"
                )
            return result
        self.live_url_file.unlink(missing_ok=True)
        exited_with_error = self.process is not None and self.process.returncode != 0
        if self.recorded() and not exited_with_error:
            result.state = TaskRunState.RECORDED
        elif self.process is not None and self.ended_on_request:
            result.state = TaskRunState.ENDED
        elif self.process is not None:
            result.state = TaskRunState.FAILED
            result.error = self.failure()
        return result

    def ending(self) -> str:
        """
        :return: How the finished process ended: the signal that ended it, its nonzero
            exit code, or that it saved no recording.
        """
        code = self.process.returncode
        if code < 0:
            names = {member.value: member.name for member in signal.Signals}
            return ProcessEnding.ENDED_BY_SIGNAL.format(signal=names.get(-code, -code))
        if code > 0:
            return ProcessEnding.EXITED_WITH_ERROR.format(code=code)
        return ProcessEnding.EXITED_WITHOUT_RECORDING

    def failure(self) -> str:
        """
        :return: How the failed process ended, followed by the last lines it wrote.
        """
        return "\n".join(filter(None, [self.ending(), self.last_output()]))

    def last_output(self) -> str:
        """
        :return: The last lines the demo wrote.
        """
        lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-ERROR_LINES:])

    def start(self) -> TaskRun:
        """
        Run the demo when available, preserving the recordings of earlier native runs.

        :return: How far the demo is.
        """
        with self.lock:
            current = self.state_locked()
            if current.state is TaskRunState.RUNNING or not current.can_run:
                return current
            if current.state is TaskRunState.RECORDED and not self.fresh_recording:
                return current
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self.live_url_file.unlink(missing_ok=True)
            command = list(self.command)
            if self.fresh_recording:
                self.recording = self.scene
                while (self.locations.local_scenes / self.recording).exists():
                    self.recording = f"{self.scene}_{uuid4().hex[:12]}"
                command += [
                    TransferOption.RECORDING.value,
                    self.recording,
                    TransferOption.PORT.value,
                    "0",
                    TransferOption.LIVE_URL_FILE.value,
                    str(self.live_url_file),
                ]
            environment = {
                **os.environ,
                EnvironmentVariable.DATA.value: str(self.locations.data),
            }
            with self.log.open("w", encoding="utf-8") as output:
                self.process = subprocess.Popen(
                    command,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=environment,
                )
            self.ended_on_request = False
            return self.state_locked()

    def stop(self) -> TaskRun:
        """
        End a running demo and wait for it.

        :return: How far the demo is.
        """
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.ended_on_request = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            return self.state_locked()
