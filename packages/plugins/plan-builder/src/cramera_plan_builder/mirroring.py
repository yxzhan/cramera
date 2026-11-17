"""
Mirroring where the real robot stands while the plan builder is attached to it, before
and between runs: cramera-bridge's posture mirror runs as a child process for as long as
the plan builder does, and its newest report is what the builder's 3D view shows the
robot in while no run publishes a world. The mirror only reads the drivers' joint
states; it commands nothing. An arm reads as live only while its driver reported within
:data:`STALE_AFTER_SECONDS` and the mirror runs, so a posture the drivers or the mirror
stopped updating is never shown as where the robot stands.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace

from typing_extensions import IO, Callable, Optional

from cramera_plan_builder.bridge_processes import BridgeModule, InterpreterOption
from cramera_plan_builder.workspace import CramModules
from cramera_protocol.plan_builder import ArmReport, RobotPosture

STALE_AFTER_SECONDS = 1.0
"""
How long after its driver's last report an arm no longer reads as live: the drivers
report many times a second and the mirror writes at 10 Hz, so a second is ten reports
missed in a row.
"""

# %% mirror


@dataclass
class PostureMirror:
    """
    Runs cramera-bridge's posture mirror and keeps the posture it reported last.
    """

    interpreter: str = field(default=sys.executable)
    """
    The Python interpreter that runs the mirror.
    """

    modules: CramModules = field(default_factory=CramModules)
    """
    Finds out whether the CRAM workspace the mirror needs is installed.
    """

    process: Optional[subprocess.Popen] = field(default=None, init=False)
    """
    The mirror's process once started, else None.
    """

    clock: Callable[[], float] = time.monotonic
    """
    Tells the time each report arrives at and each posture is answered at, in seconds.
    """

    latest: RobotPosture = field(default_factory=RobotPosture, init=False)
    """
    The posture the mirror reported last; empty before its first report.
    """

    reported_at: float = field(default=0.0, init=False)
    """
    When :attr:`latest` arrived, as :attr:`clock` tells.
    """

    def start(self) -> None:
        """
        Start the mirror unless it runs already or the CRAM workspace is missing, and
        follow its reports on a thread of its own; it ends with the plan builder, since
        its standard input closes then.
        """
        if self.process is not None or self.modules.missing():
            return
        self.process = subprocess.Popen(
            [self.interpreter, InterpreterOption.RUN_MODULE, BridgeModule.MIRROR],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        threading.Thread(
            target=self.follow, args=(self.process.stdout,), daemon=True
        ).start()

    def follow(self, reports: IO[str]) -> None:
        """
        :param reports: The mirror's output, one posture as JSON per line, each kept as
            :attr:`latest` until it ends.
        """
        for line in reports:
            self.reported_at = self.clock()
            self.latest = RobotPosture.from_json(json.loads(line))

    def posture(self) -> RobotPosture:
        """
        :return: The posture the mirror reported last, each arm aged by the time since
            and live only while its driver reported within :data:`STALE_AFTER_SECONDS`
            and the mirror still runs; an empty posture while no mirror was started.
        """
        if self.process is None:
            return RobotPosture()
        latest = self.latest
        waited = self.clock() - self.reported_at
        running = self.process.poll() is None
        return replace(
            latest,
            arms=[self.aged(arm, waited, running) for arm in latest.arms],
            mirrored=True,
        )

    @staticmethod
    def aged(arm: ArmReport, waited: float, running: bool) -> ArmReport:
        """
        :param arm: When an arm's driver last reported, as the mirror wrote it.
        :param waited: Seconds since the mirror wrote it.
        :param running: Whether the mirror still runs.
        :return: When the arm's driver last reported, as of now.
        """
        if arm.age_seconds is None:
            return replace(arm, live=False)
        age = arm.age_seconds + waited
        return replace(
            arm, age_seconds=age, live=running and age <= STALE_AFTER_SECONDS
        )

    def close(self) -> None:
        """
        End the mirror, if it was started.
        """
        if self.process is None:
            return
        self.process.stdin.close()
        self.process.terminate()
        self.process.wait()
