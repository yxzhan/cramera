"""
The node spun on a thread of its own.
"""

from __future__ import annotations

# %% Imports
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("rclpy")

# %% ending


def run_process(script: str) -> subprocess.CompletedProcess:
    """
    :param script: The file name of a process of the test dataset.
    :return: The ended process.
    """
    return subprocess.run(
        [sys.executable, str(Path(__file__).parent / "dataset" / script)],
        capture_output=True,
        timeout=60,
    )


def test_a_process_spinning_a_node_ends_normally() -> None:
    ended = run_process("spinning_node_process.py")

    assert ended.returncode == 0, ended.stderr


def test_a_block_ending_while_the_spinning_thread_gathers_its_wait_ends() -> None:
    ended = run_process("spinning_node_rebuilding_wait.py")

    assert ended.returncode == 0, ended.stderr
