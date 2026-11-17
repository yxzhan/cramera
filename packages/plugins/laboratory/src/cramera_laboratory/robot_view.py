"""
Local robot preview preparation and live observations through the viewer's origin.

The preview crosses the bridge as paths and a protocol document. Live reads only reach
the loopback publisher announced by the viewer's own running task process.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import urlopen

from cramera.cram_imports import CramImports
from cramera.http.messages import Response
from cramera_laboratory.exceptions import RobotDemoUnavailable, UnknownLiveRead
from cramera_laboratory.resources import LaboratoryResource, LaboratoryShelf
from cramera_laboratory.task_runs import TaskProcess
from cramera_protocol.laboratory.physics import InstalledLaboratory
from cramera_protocol.laboratory.runs import LaboratoryTask
from cramera_protocol.live import LiveRoute

LIVE_READS = frozenset(
    route.value.lstrip("/")
    for route in (
        LiveRoute.INFO,
        LiveRoute.STATE,
        LiveRoute.PLAN,
        LiveRoute.STATECHART,
        LiveRoute.RECORDING,
        LiveRoute.LIVE_SCENE,
    )
)
"""
The observation routes the dedicated page needs; no arbitrary URL is forwarded.
"""

LIVE_TIMEOUT = 10.0
"""
Seconds to wait for a live answer, including preparing its first scene bundle.
"""

ROBOT_SETUP = (
    "The local PR2 demo needs the current cramera-bridge and CRAM workspace. "
    "Run scripts/install_stack.py in the viewer's environment, source ROS and "
    "the robot descriptions, then restart cramera."
)
"""
The setup explanation used when the preview cannot import its native bridge.
"""


@dataclass
class RobotLaboratoryView:
    """
    Prepares the real robot's initial view and reads its owned live publisher.
    """

    task: TaskProcess
    """
    The demo whose local environment, scene directory and live publisher are used.
    """

    shelf: LaboratoryShelf
    """
    The packaged compact workbench.
    """

    preparation: threading.Lock = field(default_factory=threading.Lock, init=False)
    """
    Serializes preview preparation when several browser clients open the page.
    """

    def preview(self) -> InstalledLaboratory:
        """
        :return: The prepared initial scene containing the small lab and its PR2.
        :raises RobotDemoUnavailable: When this environment lacks the current bridge.
        """
        if not self.task.available():
            raise RobotDemoUnavailable(ROBOT_SETUP)
        command = CramImports().module(LaboratoryTask.COMMAND)
        if not hasattr(command, "prepare_preview"):
            raise RobotDemoUnavailable(ROBOT_SETUP)
        with self.preparation:
            try:
                saved = command.prepare_preview(
                    str(self.shelf.laboratory(LaboratoryResource.BENCH).directory),
                    str(self.task.locations.local_scenes),
                )
            except (ImportError, OSError) as error:
                raise RobotDemoUnavailable(
                    f"{ROBOT_SETUP} Preparation failed: {error}"
                ) from error
        return InstalledLaboratory(scene=saved.scene)

    def live_read(self, path: str) -> Response:
        """
        :param path: A relative observation route below the live proxy prefix.
        :return: The running demo's JSON response.
        :raises UnknownLiveRead: When the route is not an observation route.
        :raises RobotDemoUnavailable: When the owned demo is not publishing.
        """
        if path not in LIVE_READS:
            raise UnknownLiveRead(path)
        address = self.task.live_address()
        if address is None:
            raise RobotDemoUnavailable("The local PR2 demo is not publishing yet.")
        try:
            with urlopen(f"{address}{path}", timeout=LIVE_TIMEOUT) as answer:
                return Response.of_json(json.load(answer))
        except (
            URLError,
            HTTPException,
            ConnectionError,
            TimeoutError,
            ValueError,
        ) as error:
            raise RobotDemoUnavailable(
                "The local PR2 live publisher is unavailable; check the demo's state."
            ) from error
