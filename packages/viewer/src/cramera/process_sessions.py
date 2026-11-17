"""
Ending a process session that is not a child of the ending process, such as the Giskard
a killed run on the real robot left behind.

The session is signalled and looked at through ``pkill`` and ``pgrep``: a session whose
last process ends meanwhile is then no error but an answer, as it is for a child, whose
identity stays valid until it is reaped.
"""

from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass
from enum import IntEnum

POLL_INTERVAL = 0.05
"""
How often, in seconds, a session that was signalled is looked at.
"""


class MatchStatus(IntEnum):
    """
    The exit statuses of ``pgrep`` and ``pkill``.
    """

    MATCHED = 0
    """
    At least one process matched.
    """

    NONE_MATCHED = 1
    """
    No process matched.
    """


@dataclass(frozen=True)
class ProcessSession:
    """
    The processes of one session, such as one started with ``start_new_session``.
    """

    leader: int
    """
    The process identity of the process that started the session, which is the
    session's identity.
    """

    interrupt_timeout: float = 3.0
    """
    How long, in seconds, the session may take to end once interrupted before it is
    killed.
    """

    kill_timeout: float = 2.0
    """
    How long, in seconds, the session may take to end once killed.
    """

    def is_empty(self) -> bool:
        """
        :return: Whether no process of the session lives any more.
        """
        return self.match("pgrep") is MatchStatus.NONE_MATCHED

    def signal(self, number: signal.Signals) -> None:
        """
        :param number: The signal every process of the session receives; a session
            without processes receives nothing.
        """
        self.match("pkill", f"-{number.value}")

    def end(self) -> bool:
        """
        Interrupt the session, as Giskard expects to be ended, and kill what of it is
        left once :attr:`interrupt_timeout` passed.

        :return: Whether no process of the session is left.
        """
        self.signal(signal.SIGINT)
        if self.emptied(self.interrupt_timeout):
            return True
        self.signal(signal.SIGKILL)
        return self.emptied(self.kill_timeout)

    def emptied(self, timeout: float) -> bool:
        """
        :param timeout: How long, in seconds, to wait.
        :return: Whether the session emptied within `timeout`.
        """
        deadline = time.monotonic() + timeout
        while not self.is_empty():
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_INTERVAL)
        return True

    def match(self, program: str, *options: str) -> MatchStatus:
        """
        :param program: ``pgrep`` or ``pkill``.
        :param options: Its options before the session's.
        :return: Whether a process of the session matched.
        """
        return MatchStatus(
            subprocess.run(
                [program, *options, "--session", str(self.leader)],
                stdout=subprocess.DEVNULL,
            ).returncode
        )
