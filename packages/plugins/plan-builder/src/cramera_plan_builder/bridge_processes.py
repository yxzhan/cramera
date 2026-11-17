"""
Asking cramera-bridge in a child process of its own: a command module of the bridge is
handed an order document in a file and writes its answer document into another, so the
plan builder never imports CRAM and every request starts from a fresh world. The process
of the next order is started ahead, so its imports are done when the order comes.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import Any, Callable, List

from cramera_plan_builder.exceptions import CramUnavailable, PlanBuilderError
from cramera_plan_builder.workspace import CramModules
from cramera_protocol.document import JsonDocument

logger = logging.getLogger(__name__)

# %% files


class ProcessFile(StrEnum):
    """
    The files a bridge process reads and writes in its own directory.
    """

    ORDER = "order.json"
    """
    What the bridge is asked.
    """

    ANSWER = "answer.json"
    """
    What it answers.
    """

    OUTPUT = "output.txt"
    """
    What it prints.
    """


class InterpreterOption(StrEnum):
    """
    The options of the interpreter running a bridge command.
    """

    RUN_MODULE = "-m"
    """
    Runs a module as the program.
    """


class BridgeModule(StrEnum):
    """
    The command modules of cramera-bridge the plan builder runs.
    """

    SCANNER = "cramera_bridge.plans.scanning"
    """
    Scans the scene.
    """

    VALIDATOR = "cramera_bridge.plans.validating"
    """
    Validates a plan.
    """

    REACH = "cramera_bridge.plans.reaching"
    """
    Measures what a robot model reaches.
    """

    MIRROR = "cramera_bridge.plans.mirroring"
    """
    Mirrors where the real robot stands.
    """


# %% output


class TracebackHeader(StrEnum):
    """
    The line Python starts the traceback of an exception that ends a program with.
    """

    MOST_RECENT_CALL_LAST = "Traceback (most recent call last):"
    """
    The traceback lists the frames down to the exception, then the exception.
    """


@dataclass(frozen=True)
class ProcessOutput:
    """
    What a bridge process printed, its standard error included.
    """

    text: str
    """
    The printed text.
    """

    def final_error(self, length_limit: int) -> str:
        """
        :param length_limit: How many characters the error may have.
        :return: The message of the exception the process ended with, its type and
            frames left out and its lines joined into one, else the last line that
            holds text; cut to the limit.
        """
        lines = [line for line in self.text.splitlines() if line.strip()]
        error = self.exception_message(lines) or (lines[-1].strip() if lines else "")
        if len(error) <= length_limit:
            return error
        return error[: length_limit - 1] + "…"

    @staticmethod
    def exception_message(lines: List[str]) -> str:
        """
        :param lines: The printed lines that hold text.
        :return: The message of the exception after the last traceback, on one line,
            or an empty text when no traceback ends in an exception.
        """
        if TracebackHeader.MOST_RECENT_CALL_LAST not in lines:
            return ""
        header = (
            len(lines) - 1 - lines[::-1].index(TracebackHeader.MOST_RECENT_CALL_LAST)
        )
        after = lines[header + 1 :]
        first = next(
            (index for index, line in enumerate(after) if not line.startswith(" ")),
            None,
        )
        if first is None:
            return ""
        kind, _, message = after[first].partition(": ")
        return " ".join(line.strip() for line in [message or kind, *after[first + 1 :]])


# %% started processes


@dataclass
class StartedProcess:
    """
    A bridge process of one order, started before the order is written: the command
    module reads its order once its standard input closes.
    """

    process: subprocess.Popen
    """
    The running process; its output goes to :attr:`output_file`.
    """

    directory: tempfile.TemporaryDirectory
    """
    The directory of the process's order, answer and output, removed by :meth:`end`.
    """

    @classmethod
    def start(cls, command: Callable[[Path, Path], List[str]]) -> StartedProcess:
        """
        :param command: The command line running the module on an order file and an
            answer file.
        :return: The process, started on the files of a new directory.
        """
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        with open(root / ProcessFile.OUTPUT, "wb") as output:
            process = subprocess.Popen(
                command(root / ProcessFile.ORDER, root / ProcessFile.ANSWER),
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        return cls(process=process, directory=directory)

    def file(self, name: ProcessFile) -> Path:
        """
        :param name: One of the process's files.
        :return: Where the file lies.
        """
        return Path(self.directory.name) / name

    def is_waiting(self) -> bool:
        """
        :return: Whether the process still runs and has not been handed its order.
        """
        return self.process.poll() is None and not self.process.stdin.closed

    def hand_over(self, order: JsonDocument) -> None:
        """
        Write the order and close the process's input, so the process reads it.
        """
        self.file(ProcessFile.ORDER).write_text(
            json.dumps(order.to_json()), encoding="utf-8"
        )
        self.process.stdin.close()

    def output(self, limit: int) -> str:
        """
        :param limit: How many characters are kept.
        :return: The newest characters the process printed.
        """
        return (
            self.file(ProcessFile.OUTPUT)
            .read_bytes()
            .decode("utf-8", errors="replace")[-limit:]
        )

    def end(self) -> None:
        """
        Kill the process if it still runs and remove its directory.
        """
        self.process.kill()
        self.process.wait()
        self.process.stdin.close()
        self.directory.cleanup()


@dataclass
class NextProcess:
    """
    The process started ahead for the next order; each process answers one order only.
    """

    waiting: StartedProcess | None = None
    """
    The process waiting for its order, None while there is none.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    """
    Hands each waiting process to one order only.
    """

    def take(self, command: Callable[[Path, Path], List[str]]) -> StartedProcess:
        """
        :param command: The command line running the module, see
            :meth:`StartedProcess.start`.
        :return: The waiting process, or a new one when none waits or the waiting one
            ended.
        """
        with self.lock:
            taken, self.waiting = self.waiting, None
        if taken is not None and taken.is_waiting():
            return taken
        if taken is not None:
            taken.end()
        return StartedProcess.start(command)

    def prepare(self, command: Callable[[Path, Path], List[str]]) -> None:
        """
        Start a process waiting for the next order, unless one waits already.

        :param command: The command line running the module, see
            :meth:`StartedProcess.start`.
        """
        with self.lock:
            if self.waiting is None:
                self.waiting = StartedProcess.start(command)


# %% process


@dataclass(frozen=True)
class BridgeProcess(ABC):
    """
    Answers an order with a command module of cramera-bridge, one process per order.
    """

    interpreter: str = field(default=sys.executable)
    """
    The Python interpreter that runs the module.
    """

    timeout: float = 300.0
    """
    How long the process may take, in seconds.
    """

    output_limit: int = 65536
    """
    How many of the newest characters of a failed process's output its error carries.
    """

    reason_length_limit: int = 300
    """
    How many characters the one-line reason of a failed process may have.
    """

    modules: CramModules = field(default_factory=CramModules)
    """
    Finds out whether the CRAM workspace the module needs is installed.
    """

    next_process: NextProcess = field(default_factory=NextProcess, compare=False)
    """
    The process started for the next order once an order is answered.
    """

    @property
    @abstractmethod
    def module(self) -> BridgeModule:
        """
        The command module that answers.
        """

    @abstractmethod
    def describe_failure(self, reason: str, output: str) -> PlanBuilderError:
        """
        :param reason: Why the process ended without an answer, in one short line.
        :param output: The newest output of the process.
        :return: The error the failed process is reported as.
        """

    def answer(self, order: JsonDocument) -> Any:
        """
        Hands the order to the process waiting for it, or to a new one, and starts the
        process of the next order once it ended; logs how long the order took.

        :param order: What the module is asked.
        :return: The JSON of the module's answer.
        :raises CramUnavailable: Without the CRAM workspace.
        :raises PlanBuilderError: As :meth:`describe_failure` says, when the process ends
            without an answer or gives none within :attr:`timeout`, when it is killed.
        """
        missing = self.modules.missing()
        if missing:
            raise CramUnavailable(missing)
        started = time.monotonic()
        process = self.next_process.take(self.command)
        timed_out = threading.Event()
        deadline = threading.Timer(
            self.timeout, lambda: (timed_out.set(), process.process.kill())
        )
        deadline.start()
        process.hand_over(order)
        process.process.wait()
        deadline.cancel()
        output = process.output(self.output_limit)
        answer_file = process.file(ProcessFile.ANSWER)
        answered = process.process.returncode == 0 and answer_file.is_file()
        answer = answer_file.read_text(encoding="utf-8") if answered else None
        process.end()
        self.next_process.prepare(self.command)
        final_error = ProcessOutput(output).final_error(self.reason_length_limit)
        if timed_out.is_set():
            raise self.describe_failure(
                f"no answer within {self.timeout:g} s; it last printed: {final_error}",
                f"{output}\nGave up: no answer within {self.timeout:g} s.",
            )
        if answer is None:
            raise self.describe_failure(final_error, output)
        logger.info("%s answered in %.1f s", self.module, time.monotonic() - started)
        return json.loads(answer)

    def command(self, order_file: Path, answer_file: Path) -> List[str]:
        """
        :param order_file: The file holding the order.
        :param answer_file: The file the answer is written to.
        :return: The command running the module.
        """
        return [
            self.interpreter,
            InterpreterOption.RUN_MODULE,
            self.module,
            str(order_file),
            str(answer_file),
        ]
