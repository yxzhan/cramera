"""
Ending a process session that is not a child of the ending process.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading

from cramera.process_sessions import ProcessSession

SLEEPING = "import time; print('up', flush=True); time.sleep(60)"
"""
A program that sleeps until it is ended.
"""

IGNORING_INTERRUPTION = (
    "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
    "print('up', flush=True); time.sleep(60)"
)
"""
A program that ignores being interrupted, as a Giskard started with SIGINT blocked did.
"""


def started_session(program: str) -> subprocess.Popen:
    """
    :return: `program`, run as the leader of a session of its own, once it is up; it
        is reaped as soon as it ends, as a process left behind by its parent is.
    """
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        start_new_session=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "up"
    threading.Thread(target=process.wait, daemon=True).start()
    return process


def test_an_interrupted_session_ends():
    process = started_session(SLEEPING)
    session = ProcessSession(process.pid)

    assert session.end()
    assert process.wait(timeout=5) != 0


def test_a_session_ignoring_interruption_is_killed():
    process = started_session(IGNORING_INTERRUPTION)
    session = ProcessSession(process.pid, interrupt_timeout=0.3)

    assert session.end()
    assert process.wait(timeout=5) == -signal.SIGKILL


def test_a_session_without_processes_has_ended():
    process = started_session(SLEEPING)
    process.kill()
    process.wait()

    assert ProcessSession(process.pid).is_empty()
    assert ProcessSession(process.pid).end()
