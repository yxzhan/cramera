"""
What each robot model reaches, for the quick check of a marker dragged across the scene:

measured once per model by cramera-bridge's reach command in a child process of its own,
then kept, since a model's reach does not change while the plan builder runs.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from typing_extensions import Dict

from cramera_plan_builder.bridge_processes import BridgeModule, BridgeProcess
from cramera_plan_builder.exceptions import PlanBuilderError, ReachNotMeasured
from cramera_protocol.reach import ReachRequest, RobotReach

# %% reaches


@dataclass(frozen=True)
class RobotReaches(BridgeProcess):
    """
    Measures what robot models reach, once per model.
    """

    measured: Dict[str, RobotReach] = field(default_factory=dict)
    """
    The reach of each model measured so far, by the model's identifier.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    """
    Lets one request at a time measure, so a model is measured once.
    """

    @property
    def module(self) -> BridgeModule:
        return BridgeModule.REACH

    def describe_failure(self, reason: str, output: str) -> PlanBuilderError:
        return ReachNotMeasured(reason, output)

    def reach(self, request: ReachRequest) -> RobotReach:
        """
        :param request: The robot model.
        :return: What it reaches, measured on the first request for it.
        :raises CramUnavailable: Without the CRAM workspace.
        :raises ReachNotMeasured: When the measure ends without an answer.
        """
        with self.lock:
            if request.robot not in self.measured:
                self.measured[request.robot] = RobotReach.from_json(
                    self.answer(request)
                )
            return self.measured[request.robot]
