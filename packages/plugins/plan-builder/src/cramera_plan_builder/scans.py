"""
Scanning the scene for a plan's objects: each scan runs cramera-bridge's scanner as a
child process of its own, which perceives through the robot's camera and answers with
the plan's objects after the scan.
"""

from __future__ import annotations

from dataclasses import dataclass

from cramera_plan_builder.bridge_processes import BridgeModule, BridgeProcess
from cramera_plan_builder.exceptions import PlanBuilderError, ScanFailed
from cramera_protocol.detection import SceneScan, SceneScanOrder

# %% scanner


@dataclass(frozen=True)
class SceneScans(BridgeProcess):
    """
    Scans the scene, one process per scan.
    """

    @property
    def module(self) -> BridgeModule:
        return BridgeModule.SCANNER

    def describe_failure(self, reason: str, output: str) -> PlanBuilderError:
        return ScanFailed(reason, output)

    def scan(self, order: SceneScanOrder) -> SceneScan:
        """
        :param order: The plan, the region and the scene the camera sees.
        :return: The plan's objects after the scan.
        :raises CramUnavailable: Without the CRAM workspace.
        :raises ScanFailed: When the scanner ends without a scan.
        """
        return SceneScan.from_json(self.answer(order))
