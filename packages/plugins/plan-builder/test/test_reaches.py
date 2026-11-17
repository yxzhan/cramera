"""
Measuring what a robot model reaches in a process of its own, once per model.
"""

from __future__ import annotations

from importlib.machinery import ModuleSpec

import pytest

from cramera_plan_builder.exceptions import ReachNotMeasured
from cramera_plan_builder.reaches import RobotReaches
from cramera_plan_builder.workspace import CramModules
from cramera_protocol.geometry import Vector3
from cramera_protocol.reach import ReachRequest

from .dataset.given_workspace import DATASET

INTERPRETER = DATASET / "reaching_interpreter.py"
"""
Writes that the robot's one arm reaches 1 m around a shoulder above the origin.
"""


def installed() -> CramModules:
    """
    :return: Modules of which every one is installed.
    """
    return CramModules(
        finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
    )


def test_a_models_reach_is_measured_once_and_kept(tmp_path, monkeypatch):
    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("REACH_CALLS", str(calls))
    reaches = RobotReaches(interpreter=str(INTERPRETER), modules=installed())

    first = reaches.reach(ReachRequest(robot="tracy"))
    second = reaches.reach(ReachRequest(robot="tracy"))

    assert first == second
    assert first.reaches(Vector3(0.5, 0, 1))
    assert not first.reaches(Vector3(1.5, 0, 0))
    assert calls.read_text("utf-8").split() == ["tracy"]


def test_a_measure_ending_without_an_answer_is_refused():
    reaches = RobotReaches(interpreter="false", modules=installed())

    with pytest.raises(ReachNotMeasured):
        reaches.reach(ReachRequest(robot="tracy"))
