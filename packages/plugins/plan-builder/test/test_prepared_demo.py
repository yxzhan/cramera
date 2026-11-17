"""
A demo's interpreter started ahead of its run.
"""

from __future__ import annotations

import os
import subprocess
import sys

from cramera_plan_builder.prepared_demo import DemoOrder, PreparedDemoOption

from .dataset.given_workspace import DATASET

PREPARED = DATASET / "prepared"
"""
The stand-ins of a preparation and a demo.
"""

TIMEOUT = 30.0
"""
How long a prepared interpreter may take, in seconds.
"""


def started_interpreter(*arguments: str) -> subprocess.Popen:
    """
    :param arguments: The interpreter's arguments.
    :return: A prepared demo's interpreter that finds the stand-ins.
    """
    return subprocess.Popen(
        [sys.executable, "-m", "cramera_plan_builder.prepared_demo", *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(DATASET), *sys.path])},
    )


def ordered(process: subprocess.Popen, order: DemoOrder) -> tuple[int, list[str]]:
    """
    :param process: A started prepared interpreter.
    :param order: The demo it is handed.
    :return: Its exit status and the lines it printed, once it ended.
    """
    output, _ = process.communicate(order.to_line(), timeout=TIMEOUT)
    return process.returncode, output.decode().splitlines()


def test_the_ordered_demo_runs_with_its_arguments_in_its_directory():
    demo = PREPARED / "demo.py"

    status, lines = ordered(
        started_interpreter(), DemoOrder(command=[str(demo), "0", "--port", "1"])
    )

    assert status == 0
    assert lines == [str(["0", "--port", "1"]), str(PREPARED), "None", "unflushed end"]


def test_the_preparation_runs_before_the_demo():
    status, lines = ordered(
        started_interpreter(PreparedDemoOption.PREPARE, "prepared.preparation:prepare"),
        DemoOrder(command=[str(PREPARED / "demo.py"), "0"]),
    )

    assert status == 0
    assert lines[2] == "prepared"


def test_the_demo_ends_the_interpreter_with_its_status():
    status, lines = ordered(
        started_interpreter(), DemoOrder(command=[str(PREPARED / "demo.py"), "3"])
    )

    assert status == 3
    assert lines[-1] == "unflushed end"


def test_an_interpreter_whose_input_ends_before_an_order_runs_nothing():
    process = started_interpreter()

    output, _ = process.communicate(b"", timeout=TIMEOUT)

    assert process.returncode == 0
    assert output == b""
