"""Launch and observe the fixed PR2 laboratory transfer on the local machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from _thread import LockType
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing_extensions import Any, ClassVar

from cramera import paths


# %% execution vocabulary
class LaboratoryRoute(StrEnum):
    """Local routes for the predetermined laboratory transfer."""

    START = "/api/laboratory/pr2/start"
    """Start one transfer or return the existing running transfer."""
    STATUS = "/api/laboratory/pr2/status"
    """Read the process outcome and available viewer links."""


class RunState(StrEnum):
    """Observable states of a laboratory child process."""

    IDLE = "idle"
    """No transfer has been launched by this server."""
    RUNNING = "running"
    """The child process is still executing."""
    SUCCEEDED = "succeeded"
    """The child exited successfully and verified the recorded transfer."""
    FAILED = "failed"
    """The process or its acceptance checks failed."""


class RunField(StrEnum):
    """Fields shared by the laboratory status endpoint and its viewer."""

    OK = "ok"
    """Whether the last launch or execution succeeded."""
    STATE = "state"
    """The current process lifecycle state."""
    ERROR = "error"
    """An explanation of a launch or execution failure."""
    LOG = "log"
    """The bounded tail of the process output."""
    EXIT_CODE = "exitCode"
    """The child's exit code after completion."""
    LIVE_URL = "liveUrl"
    """The local page that can attach to the live bridge."""
    RECORDING_URL = "recordingUrl"
    """The local page containing a verified completed recording."""
    SUCCESS = "success"
    """The acceptance report's terminal verification result."""


class RunFile(StrEnum):
    """Files produced by the fixed laboratory demonstration."""

    METRICS = "acceptance_metrics.json"
    """Terminal acceptance checks for the current transfer."""
    SCENE = "scene.json"
    """The recorded world description."""
    TRAJECTORY = "trajectory.json"
    """The recorded robot and object motions."""
    LOG = "laboratory-pr2.log"
    """The stdout and stderr of the latest launched child."""


class RunEnvironment(StrEnum):
    """Process settings that keep execution local and observable."""

    DATA = "CRAMERA_DATA"
    """The same local artifact directory used by the HTTP server."""
    UNBUFFERED = "PYTHONUNBUFFERED"
    """Flush child output promptly for progress and failure reports."""
    BRIDGE_PORT = "LIVE_VIZ_PORT"
    """The standard local live-visualization bridge port."""


# %% fixed child process
@dataclass
class LaboratoryRun:
    """Own one fixed demo process and validate its terminal acceptance report."""

    data_directory: Path
    """The local CRAMERA data directory receiving logs and recordings."""
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    """The running or most recently completed child."""
    launch_error: str | None = field(default=None, init=False)
    """An operating-system failure preventing the child from starting."""
    lock: LockType = field(default_factory=threading.Lock, init=False, repr=False)
    """Serialize launch and status requests across HTTP request threads."""

    MODULE: ClassVar[str] = "cramera.laboratory_demo"
    """The only executable module this launcher accepts."""
    SCENE_NAME: ClassVar[str] = "precision_lab_pr2"
    """The separate recorded scene written by the demo."""
    BRIDGE_PORT: ClassVar[int] = 8765
    """The local live bridge port reserved by the demonstration."""
    LIVE_URL: ClassVar[str] = "/laboratory-live.html"
    """The viewer entry that attaches when the live world becomes available."""
    RECORDING_URL: ClassVar[str] = (
        f"/index.html?scene={SCENE_NAME}&layout=scene&offline=1"
    )
    """The saved transfer's isolated playback page."""
    LOG_LIMIT: ClassVar[int] = 8192
    """Maximum bytes of child output returned by each status request."""

    @property
    def output_directory(self) -> Path:
        """The generated recording directory for the fixed demo."""
        return self.data_directory / "scenes" / self.SCENE_NAME

    @property
    def metrics_path(self) -> Path:
        """The terminal acceptance report for the latest transfer."""
        return self.output_directory / RunFile.METRICS

    @property
    def log_path(self) -> Path:
        """The local log file holding the latest child output."""
        return self.data_directory / RunFile.LOG

    def start(self) -> dict[str, Any]:
        """Launch the fixed demo once, preserving an already running transfer."""
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return self._status()
            self.launch_error = None
            self.process = None
            try:
                self.data_directory.mkdir(parents=True, exist_ok=True)
                self.metrics_path.unlink(missing_ok=True)
                environment = os.environ.copy()
                environment.update(
                    {
                        RunEnvironment.DATA: str(self.data_directory),
                        RunEnvironment.UNBUFFERED: "1",
                        RunEnvironment.BRIDGE_PORT: str(self.BRIDGE_PORT),
                    }
                )
                with self.log_path.open("wb") as output:
                    self.process = subprocess.Popen(
                        [sys.executable, "-m", self.MODULE],
                        cwd=paths.repository_root(),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError as error:
                self.launch_error = str(error)
            return self._status()

    def status(self) -> dict[str, Any]:
        """Report child completion with a fresh successful acceptance report."""
        with self.lock:
            return self._status()

    def _status(self) -> dict[str, Any]:
        """Read process state while the caller holds the lifecycle lock."""
        answer: dict[str, Any] = {
            RunField.OK: True,
            RunField.STATE: RunState.IDLE,
            RunField.LIVE_URL: self.LIVE_URL,
        }
        if self.launch_error:
            answer.update(
                {
                    RunField.OK: False,
                    RunField.STATE: RunState.FAILED,
                    RunField.ERROR: self.launch_error,
                }
            )
            return answer
        if self.process is None:
            return answer
        code = self.process.poll()
        answer[RunField.EXIT_CODE] = code
        answer[RunField.LOG] = self._log_tail()
        if code is None:
            answer[RunField.STATE] = RunState.RUNNING
            return answer
        if code == 0 and self._accepted():
            answer[RunField.STATE] = RunState.SUCCEEDED
            answer[RunField.RECORDING_URL] = self.RECORDING_URL
            return answer
        answer.update(
            {
                RunField.OK: False,
                RunField.STATE: RunState.FAILED,
                RunField.ERROR: (
                    "Der PR2-Lauf wurde nicht erfolgreich abgeschlossen."
                    if code != 0
                    else "Der PR2-Lauf hat keine erfolgreiche Aufzeichnung bestätigt."
                ),
            }
        )
        return answer

    def _accepted(self) -> bool:
        """Require the demo's positive report and both replay artifacts."""
        required = (RunFile.METRICS, RunFile.SCENE, RunFile.TRAJECTORY)
        if not all((self.output_directory / name).is_file() for name in required):
            return False
        try:
            report = json.loads(self.metrics_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(report, dict) and report.get(RunField.SUCCESS) is True

    def _log_tail(self) -> str:
        """Read a bounded suffix of the child's output for diagnosis."""
        if not self.log_path.is_file():
            return ""
        with self.log_path.open("rb") as output:
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - self.LOG_LIMIT))
            return output.read(self.LOG_LIMIT).decode(errors="replace")
