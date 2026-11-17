"""
Scanning the scene in a process of its own: the scanner is handed what to scan and its
scan is read back.
"""

from __future__ import annotations

import logging
from importlib.machinery import ModuleSpec

import pytest

from cramera_plan_builder.exceptions import CramUnavailable, ScanFailed
from cramera_plan_builder.scans import SceneScans
from cramera_plan_builder.workspace import CramModules
from cramera_protocol.detection import DetectionGrounding, SceneScan, SceneScanOrder
from cramera_protocol.plan_builder import DemoDocument

from .dataset.given_workspace import DATASET

INTERPRETER = DATASET / "scanning_interpreter.py"
"""
Writes a scan that saw the order's simulated objects.
"""

# %% fixtures


@pytest.fixture
def order(plan) -> SceneScanOrder:
    """
    :return: A scan of the test plan's world whose camera sees the plan's objects.
    """
    return SceneScanOrder(demo=DemoDocument(plan=plan), simulated_objects=plan.objects)


def installed() -> CramModules:
    """
    :return: Modules of which every one is installed.
    """
    return CramModules(
        finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
    )


# %% scanning


def test_the_scan_the_scanner_writes_is_answered(order):
    scans = SceneScans(interpreter=str(INTERPRETER), modules=installed())

    scan = scans.scan(order)

    assert scan == SceneScan(
        objects=order.simulated_objects, grounding=DetectionGrounding.PERCEIVED
    )


def test_a_scanner_ending_without_a_scan_fails_the_scan(order):
    scans = SceneScans(interpreter="false", modules=installed())

    with pytest.raises(ScanFailed):
        scans.scan(order)


def test_without_the_cram_workspace_nothing_is_scanned(order):
    scans = SceneScans(
        interpreter=str(INTERPRETER), modules=CramModules(finder=lambda name: None)
    )

    with pytest.raises(CramUnavailable):
        scans.scan(order)


def test_how_long_the_scanner_took_is_logged(order, caplog):
    scans = SceneScans(interpreter=str(INTERPRETER), modules=installed())

    with caplog.at_level(logging.INFO, logger="cramera_plan_builder.bridge_processes"):
        scans.scan(order)

    [record] = caplog.records
    assert record.getMessage().startswith("cramera_bridge.plans.scanning answered in ")
    assert record.getMessage().endswith(" s")


def test_a_scanner_that_does_not_answer_in_time_fails_the_scan_with_its_output(order):
    scans = SceneScans(
        interpreter=str(DATASET / "hanging_interpreter.py"),
        timeout=0.5,
        modules=installed(),
    )

    with pytest.raises(ScanFailed) as failure:
        scans.scan(order)

    assert "no answer within 0.5 s" in failure.value.message()
    assert "waiting for the perception" in failure.value.message()
