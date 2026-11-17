"""
Running a generated demo as a child process the plan builder owns: one at a time, its
output kept within a bound, stopped with its whole process group, and ended by the demo
itself when the viewer goes away.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from typing_extensions import IO, List, Optional

from cramera.process_sessions import ProcessSession
from cramera_plan_builder.demos import DemoDirectory, DemoTemplate
from cramera_plan_builder.prepared_demo import DemoOrder, PreparedDemoOption
from cramera_plan_builder.exceptions import (
    DemoNotStarted,
    PlanRunning,
    RunRecordSaving,
    SafetyStopNotAcknowledged,
)
from cramera_protocol.exceptions import ProtocolError
from cramera_protocol.plan_builder import (
    DemoDocument,
    DemoOption,
    RunEvent,
    RunEventKind,
    RunMode,
    RunOutputMarker,
    RunState,
    RunStatus,
    SafetyStop,
    StopAfterMotions,
)
from cramera_protocol.robot_profile import ExecutorKind

logger = logging.getLogger(__name__)

READ_SIZE = 4096
"""
How many bytes of the demo's output are read at once.
"""

STOP_TIMEOUT = 15.0
"""
How long a demo may take to end after it was asked to, in seconds, before it is killed;
a run on the real robot spends it halting the arms and ending Giskard.
"""

STOP_POLL_INTERVAL = 0.05
"""
How often a demo that was asked to end is checked, in seconds.
"""

ARM_HALT_MODULE = "cramera_bridge.arm_halt"
"""
The module of cramera-bridge that halts the real robot's arms for the plan builder once
it stopped a run on the real robot.
"""

ARM_HALT_TIMEOUT = 20.0
"""
How long, in seconds from the stop, the plan builder's halt of the arms may take to
confirm that the arms were commanded zero velocities and stand still; it was started
with the run, so it reached the controllers long before.
"""

PREPARED_DEMO_MODULE = "cramera_plan_builder.prepared_demo"
"""
The module of a demo's interpreter started ahead of its demo.
"""

DEMO_PREPARATION = "cramera_bridge.plans.command:PlanDemoCommand.prepare"
"""
What cramera-bridge does ahead of every demo, as a prepared interpreter is told it.
"""

SUGGESTION_PREFIX = "Suggestion: "
"""
How CRAM's exceptions start the line of the suggestion that follows their error.
"""

LOG_ENCODING = "utf-8"
"""
The encoding the demo's output is read in; undecodable bytes are replaced.
"""

# %% output


@dataclass
class RunEventLines:
    """
    Takes the lines reporting run events out of a process's output as it arrives: each
    becomes a :class:`RunEvent`, the rest of the output passes on unchanged.

    A line that is not complete yet is held back only while it may still become one
    reporting an event.
    """

    events: List[RunEvent] = field(default_factory=list)
    """
    The events reported so far, in order.
    """

    held: bytearray = field(default_factory=bytearray)
    """
    The start of a line that may report an event, until its line break arrives.
    """

    mid_line: bool = False
    """
    Whether the output passed on last ended within a line, which then reports no event.
    """

    @classmethod
    def marker(cls) -> bytes:
        """
        :return: What a line reporting an event starts with.
        """
        return RunOutputMarker.RUN_EVENT.encode(LOG_ENCODING)

    def split(self, chunk: bytes) -> bytes:
        """
        :param chunk: Output that arrived.
        :return: The output not reporting events, to keep as the log; its events are
            added to :attr:`events`.
        """
        data = bytes(self.held) + chunk
        self.held.clear()
        passed = bytearray()
        start = 0
        while start < len(data):
            end = data.find(b"\n", start)
            line = data[start:] if end < 0 else data[start : end + 1]
            if self.mid_line:
                passed.extend(line)
            elif end < 0 and self.may_report_event(line):
                self.held.extend(line)
                break
            elif not self.take_event(line):
                passed.extend(line)
            self.mid_line = end < 0
            start = len(data) if end < 0 else end + 1
        return bytes(passed)

    def may_report_event(self, start: bytes) -> bool:
        """
        :param start: The start of a line.
        :return: Whether the line may still turn out to report an event.
        """
        marker = self.marker()
        return start.startswith(marker) or marker.startswith(start)

    def take_event(self, line: bytes) -> bool:
        """
        :param line: A line of output, with its line break if it has one.
        :return: Whether it reported an event, which was added to :attr:`events`; a
            line that starts like one but does not hold an event's JSON stays output.
        """
        if not line.startswith(self.marker()):
            return False
        # the process boundary, where a garbled line stays output a person can read
        try:
            event = RunEvent.from_output_line(
                line.decode(LOG_ENCODING, errors="replace").rstrip("\r\n")
            )
        except (ValueError, ProtocolError):
            return False
        self.events.append(event)
        return True


@dataclass
class OutputLog:
    """
    The newest output of a process, at most :attr:`limit` bytes of it; written by the
    reading thread and read by request threads.
    """

    limit: int
    """
    How many of the newest bytes are kept.
    """

    data: bytearray = field(default_factory=bytearray)
    """
    The kept bytes.
    """

    event_lines: RunEventLines = field(default_factory=RunEventLines)
    """
    Takes the lines reporting run events out of the output, which are all kept.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    """
    Guards :attr:`data` and :attr:`event_lines`.
    """

    def append(self, chunk: bytes) -> None:
        """
        :param chunk: Output that arrived; its lines reporting run events become
            events, and of the rest the oldest bytes beyond the limit are dropped.
        """
        with self.lock:
            self.data.extend(self.event_lines.split(chunk))
            excess = len(self.data) - self.limit
            if excess > 0:
                del self.data[:excess]

    def text(self) -> str:
        """
        :return: The kept output as text.
        """
        with self.lock:
            return self.data.decode(LOG_ENCODING, errors="replace")

    def events(self) -> List[RunEvent]:
        """
        :return: Every run event the output reported, in order.
        """
        with self.lock:
            return list(self.event_lines.events)

    def follow(self, stream: IO[bytes]) -> None:
        """
        Keep what the stream delivers until it ends.

        :param stream: The process's output.
        """
        for chunk in iter(lambda: stream.read1(READ_SIZE), b""):
            self.append(chunk)
        stream.close()


# %% the plan builder's own halt of the arms


@dataclass
class ArmHaltProcess:
    """
    The plan builder's own halt of the real robot's arms after it stopped a run there,
    independent of the run, which may have been killed before it could halt them:
    started with the run, it reaches the arms' controllers while the run goes on and,
    once its input is closed, commands them zero velocities and ends with status 0 only
    when both arms then report standing still. The operating system closes its input
    when the plan builder ends, so the arms are halted then as well.
    """

    process: subprocess.Popen
    """
    The halt's process.
    """

    reader: threading.Thread
    """
    The thread moving the halt's output into the run's output.
    """

    @classmethod
    def start(cls, command: List[str], log: OutputLog) -> ArmHaltProcess:
        """
        :param command: The command running the halt.
        :param log: The run's output, which the halt's output joins.
        :return: The started halt, waiting for its input to close.
        """
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        reader = threading.Thread(
            target=log.follow, args=(process.stdout,), daemon=True
        )
        reader.start()
        return cls(process=process, reader=reader)

    def halt(self, deadline: float) -> bool:
        """
        Let the halt command zero velocities, by closing its input.

        :param deadline: The :func:`time.monotonic` time it has to confirm by; it is
            killed when it did not end by then.
        :return: Whether every arm's controller acknowledged zero velocities in time.
        """
        self.process.stdin.close()
        while self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(STOP_POLL_INTERVAL)
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()
        self.reader.join(STOP_TIMEOUT)
        return self.process.returncode == 0

    def dismiss(self) -> None:
        """
        End the halt without halting, once the run it guarded ended on its own and
        halted the arms itself.
        """
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()
        self.reader.join(STOP_TIMEOUT)
        self.process.stdin.close()


# %% the interpreter prepared ahead


@dataclass
class PreparedProcess:
    """
    A demo's interpreter started ahead of its demo, which prepares what every demo needs
    and then waits for the demo it is handed (see
    :mod:`cramera_plan_builder.prepared_demo`); its output is read from its start, so
    what the preparation printed opens the demo's output.
    """

    process: subprocess.Popen
    """
    The interpreter's process, leader of its own process group.
    """

    log: OutputLog
    """
    The interpreter's newest output, which becomes its demo's.
    """

    reader: threading.Thread
    """
    The thread moving the output into :attr:`log`.
    """

    @classmethod
    def start(
        cls, command: List[str], directory: Path, log: OutputLog
    ) -> Optional[PreparedProcess]:
        """
        :param command: The command running the prepared interpreter.
        :param directory: Where it starts, created when missing.
        :param log: Receives its output.
        :return: The started interpreter; None when the operating system could not
            start it, so the next demo starts an interpreter of its own.
        """
        directory.mkdir(parents=True, exist_ok=True)
        # the boundary to the operating system, where a refused start leaves the next
        # demo to start as it would without preparation
        try:
            process = subprocess.Popen(
                command,
                cwd=directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            logger.warning("No demo interpreter was prepared ahead: %s", error)
            return None
        reader = threading.Thread(
            target=log.follow, args=(process.stdout,), daemon=True
        )
        reader.start()
        return cls(process=process, log=log, reader=reader)

    def hand_over(self, command: List[str]) -> bool:
        """
        :param command: The demo file and its arguments.
        :return: Whether the interpreter took the demo; one that ended, before or while
            it was handed the demo, did not, and was ended.
        """
        if self.process.poll() is not None:
            self.end()
            return False
        # the pipe to a process that may end at any moment, where an ended one leaves
        # the demo to start an interpreter of its own
        try:
            self.process.stdin.write(DemoOrder(command=command).to_line())
            self.process.stdin.flush()
        except BrokenPipeError:
            self.end()
            return False
        return True

    def end(self) -> None:
        """
        Kill the interpreter, which no demo was handed, and release its pipes.
        """
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()
        self.reader.join(STOP_TIMEOUT)
        self.process.stdin.close()


# %% one process


@dataclass
class DemoProcess:
    """
    One started demo: its process, the thread reading its output, and the pipe whose end
    tells the demo to stop when this process goes away.
    """

    path: Path
    """
    The demo file.
    """

    mode: RunMode
    """
    What the demo does.
    """

    process: subprocess.Popen
    """
    The demo's process, leader of its own process group.
    """

    log: OutputLog
    """
    The demo's newest output.
    """

    reader: threading.Thread
    """
    The thread moving the output into :attr:`log`.
    """

    stopped: bool = False
    """
    Whether the demo was stopped rather than ending on its own.
    """

    arm_halt: Optional[ArmHaltProcess] = None
    """
    The plan builder's own halt of the arms, started with a demo performing its plan on
    the real robot, so stopping the demo ends the Giskard it started and halts the arms
    in any case; None for any other demo.
    """

    halted: bool = False
    """
    Whether stopping the demo on the real robot was confirmed: its Giskard ended and
    every arm's controller acknowledged zero velocities.
    """

    acknowledged: bool = False
    """
    Whether a person acknowledged the safety stop that cancelled the demo.
    """

    stop_after_motions: Optional[StopAfterMotions] = None
    """
    What a Stop did that came while the demo saved its run's record, which it leaves
    the demo to finish; None when no such Stop came.
    """

    @classmethod
    def start(
        cls,
        command: List[str],
        path: Path,
        mode: RunMode,
        log: OutputLog,
        arm_halt_command: Optional[List[str]] = None,
        prepared: Optional[PreparedProcess] = None,
    ) -> DemoProcess:
        """
        :param command: The command running the demo.
        :param path: The demo file.
        :param mode: What the demo does.
        :param log: Receives the demo's output.
        :param arm_halt_command: The command of the plan builder's own halt of the
            arms, for a demo performing its plan on the real robot; else None.
        :param prepared: An interpreter prepared ahead, which runs the demo when it
            takes it; the demo starts in an interpreter of its own otherwise.
        :return: The started demo.
        :raises DemoNotStarted: When the operating system cannot start the command.
        """
        if prepared is not None and prepared.hand_over(command[1:]):
            return cls(
                path=path,
                mode=mode,
                process=prepared.process,
                log=prepared.log,
                reader=prepared.reader,
                arm_halt=cls.started_arm_halt(arm_halt_command, prepared.log),
            )
        # the boundary to the operating system, where a refused start becomes an answer
        try:
            process = subprocess.Popen(
                command,
                cwd=path.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            raise DemoNotStarted(path, str(error)) from error
        reader = threading.Thread(
            target=log.follow, args=(process.stdout,), daemon=True
        )
        reader.start()
        return cls(
            path=path,
            mode=mode,
            process=process,
            log=log,
            reader=reader,
            arm_halt=cls.started_arm_halt(arm_halt_command, log),
        )

    @staticmethod
    def started_arm_halt(
        command: Optional[List[str]], log: OutputLog
    ) -> Optional[ArmHaltProcess]:
        """
        :param command: The command of the plan builder's own halt of the arms, or None.
        :param log: The demo's output, which the halt's output joins.
        :return: The started halt, None without a command.
        """
        return None if command is None else ArmHaltProcess.start(command, log)

    def state(self) -> RunState:
        """
        :return: Whether the demo runs, saves the record of its run once that is over,
            ended on its own, was cancelled by a safety stop or was stopped.
        """
        return_code = self.process.poll()
        if return_code is None:
            return RunState.SAVING if self.saves_its_record() else RunState.RUNNING
        if self.stopped:
            if self.arm_halt is not None and not self.halted:
                return RunState.HALT_UNCONFIRMED
            return RunState.STOPPED
        if self.refused_before_motion():
            return RunState.REFUSED
        if return_code == 0:
            return RunState.FINISHED
        if self.safety_stop() is not None:
            return RunState.SAFETY_STOPPED
        if self.saves_its_record():
            return RunState.SAVE_FAILED
        return RunState.FAILED

    def refused_before_motion(self) -> bool:
        """
        :return: Whether the demo ended on its own once it reported that its robot's
            check refused the run, which it reports only before it started Giskard or
            commanded the arms; a demo that was stopped or reported more is not taken
            for one.
        """
        if self.process.poll() is None or self.stopped:
            return False
        return [event.kind for event in self.log.events()] == [RunEventKind.REFUSED]

    def saves_its_record(self) -> bool:
        """
        :return: Whether the demo reported that its run is over and its robot released,
            and that it saves the run's record.
        """
        return any(
            event.kind is RunEventKind.RECORD_SAVING for event in self.log.events()
        )

    def safety_stop(self) -> Optional[SafetyStop]:
        """
        :return: The safety stop that cancelled the demo once it ended, as far as a
            person acknowledged it, or None.
        """
        if self.process.poll() in (None, 0) or self.stopped:
            return None
        stop = SafetyStop.from_output(self.log.text())
        if stop is None:
            return None
        stop.acknowledged = self.acknowledged
        return stop

    def awaits_acknowledgement(self) -> bool:
        """
        :return: Whether a safety stop cancelled the demo and nobody acknowledged it.
        """
        stop = self.safety_stop()
        return stop is not None and not stop.acknowledged

    def status(self) -> RunStatus:
        """
        :return: The demo's state, newest output and safety stop.
        """
        return RunStatus(
            state=self.state(),
            mode=self.mode,
            return_code=self.process.poll(),
            log=self.reported_log(),
            path=str(self.path),
            safety_stop=self.safety_stop(),
            events=self.log.events(),
            stop_after_motions=self.stop_after_motions,
        )

    def reported_log(self) -> str:
        """
        :return: The demo's newest output; once it failed with an exception whose
            suggestion stands on a line of its own, followed by a line of the error and
            then the suggestion, the line the page reads as why the run failed.
        """
        text = self.log.text()
        if self.process.poll() in (None, 0):
            return text
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2 or not lines[-1].startswith(SUGGESTION_PREFIX):
            return text
        error = lines[-2].partition(": ")[2] or lines[-2]
        return f"{text.rstrip()}\n{error} {lines[-1]}\n"

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """
        Ask the demo's process group to end, kill it when it does not end in time, and
        release the pipes and the reading thread; stopping an ended demo only releases
        them. A process the demo started that keeps the output open is not waited for
        longer than the timeout.

        A demo on the real robot is not trusted to have halted it, as it may have been
        killed: every session it started, such as its Giskard's, is ended (interrupted,
        so Giskard commands zero velocities, then killed), and the plan builder's own
        halt commands every arm zero velocities and waits for them to be acknowledged.

        A demo whose run is over and which saves its run's record is left to save it
        and end on its own (see :meth:`stop_after_motions_are_over`).

        :param timeout: How long the demo may take to end, in seconds.
        """
        if self.process.poll() is None and self.saves_its_record():
            self.stop_after_motions_are_over()
            return
        if self.process.poll() is None:
            self.stopped = True
            started = time.monotonic()
            sessions = self.started_sessions()
            self.signal(signal.SIGTERM)
            deadline = started + timeout
            while self.process.poll() is None and time.monotonic() < deadline:
                time.sleep(STOP_POLL_INTERVAL)
            if self.process.poll() is None:
                sessions |= self.started_sessions()
                self.signal(signal.SIGKILL)
            self.process.wait()
            if self.arm_halt is not None:
                ended = all([ProcessSession(leader).end() for leader in sessions])
                halted = self.arm_halt.halt(max(deadline, started + ARM_HALT_TIMEOUT))
                self.halted = ended and halted
        if self.arm_halt is not None:
            self.arm_halt.dismiss()
        self.process.wait()
        self.reader.join(timeout)
        self.process.stdin.close()

    def stop_after_motions_are_over(self) -> None:
        """
        Halt the real robot's arms once more and confirm that they stand still, as a
        stop does, but leave the demo, whose robot is released already, to save its
        run's record; a demo on no real robot is left alone. Asked again, it answers
        what the first halt confirmed.
        """
        if self.arm_halt is None or self.stop_after_motions is not None:
            return
        halted = self.arm_halt.halt(time.monotonic() + ARM_HALT_TIMEOUT)
        self.stop_after_motions = (
            StopAfterMotions.HALTED if halted else StopAfterMotions.HALT_UNCONFIRMED
        )

    def started_sessions(self) -> set[int]:
        """
        :return: The process identities of the demo's own processes, each of which may
            lead a session of its own, as its Giskard does; asked before the demo ends,
            as they belong to another process afterwards.
        """
        children = subprocess.run(
            ["pgrep", "--parent", str(self.process.pid)],
            stdout=subprocess.PIPE,
            text=True,
        )
        return {int(pid) for pid in children.stdout.split()}

    def signal(self, number: signal.Signals) -> None:
        """
        :param number: A signal for the demo's whole process group; the group exists
            while the demo's process is not reaped, which only :meth:`stop` does.
        """
        os.killpg(self.process.pid, number)


# %% runner


@dataclass
class DemoRunner:
    """
    Starts the demo of a plan, replacing the one that runs, and stops it.
    """

    directory: DemoDirectory
    """
    Where demos are written.
    """

    port: int
    """
    The port started demos publish their world on.
    """

    log_limit: int
    """
    How many of the newest bytes of a demo's output are kept.
    """

    template: DemoTemplate = field(default_factory=DemoTemplate)
    """
    Renders a plan into its demo.
    """

    stop_timeout: float = STOP_TIMEOUT
    """
    How long a demo may take to end after it was asked to, in seconds.
    """

    arm_halt: Optional[List[str]] = None
    """
    The command of the plan builder's own halt of the real robot's arms; cramera-bridge's
    when None.
    """

    prepared_program: Optional[List[str]] = None
    """
    The command of a demo's interpreter started ahead while no run goes on (see
    :mod:`cramera_plan_builder.prepared_demo`); None starts each demo's interpreter
    when the demo starts.
    """

    prepared: Optional[PreparedProcess] = field(default=None, init=False)
    """
    The interpreter prepared for the next demo, None while there is none.
    """

    current: Optional[DemoProcess] = field(default=None, init=False)
    """
    The demo started last, running or ended.
    """

    lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    """
    Lets one request at a time start, stop or read the demo.
    """

    def start(
        self,
        demo: DemoDocument,
        mode: RunMode,
        executor: Optional[ExecutorKind] = None,
    ) -> RunStatus:
        """
        :param demo: A checked plan and its environment.
        :param mode: What the demo does.
        :param executor: How a demo performing the plan executes it; the mode's default
            when None.
        :return: The started demo's state.
        :raises DemoNotStarted: When the operating system cannot start it.
        :raises SafetyStopNotAcknowledged: While nobody acknowledged the safety stop
            of the demo started last.
        :raises RunRecordSaving: While the demo started last saves its run's record.
        """
        with self.lock:
            self.refuse_to_start()
            self.release()
            saved = self.directory.write(self.template.render(demo))
            path = Path(saved.path)
            prepared, self.prepared = self.prepared, None
            self.current = DemoProcess.start(
                self.command(path, mode, executor),
                path,
                mode,
                OutputLog(self.log_limit),
                arm_halt_command=(
                    self.arm_halt_command()
                    if (executor or mode.default_executor) is ExecutorKind.REAL
                    else None
                ),
                prepared=prepared,
            )
            return self.current.status()

    def command(
        self, path: Path, mode: RunMode, executor: Optional[ExecutorKind] = None
    ) -> List[str]:
        """
        :param path: A demo file.
        :param mode: What the demo does.
        :param executor: How a demo performing the plan executes it; the mode's default
            when None.
        :return: The command running it, ending with the viewer's process.
        """
        command = [
            self.directory.interpreter,
            str(path),
            DemoOption.PORT,
            str(self.port),
            DemoOption.LIFELINE,
        ]
        if mode is RunMode.STAGE:
            command.append(DemoOption.STAGE)
            return command
        command.extend((DemoOption.EXECUTOR, executor or mode.default_executor))
        return command

    def arm_halt_command(self) -> List[str]:
        """
        :return: The command of the plan builder's own halt of the real robot's arms,
            run by the interpreter the demos run with.
        """
        return self.arm_halt or [
            self.directory.interpreter,
            "-m",
            ARM_HALT_MODULE,
        ]

    @staticmethod
    def prepared_program_command(interpreter: str) -> List[str]:
        """
        :param interpreter: The Python interpreter the demos run with.
        :return: The command of an interpreter prepared ahead with cramera-bridge's
            preparation of every demo.
        """
        return [
            interpreter,
            "-m",
            PREPARED_DEMO_MODULE,
            PreparedDemoOption.PREPARE,
            DEMO_PREPARATION,
        ]

    def stop(self) -> RunStatus:
        """
        :return: The stopped demo's state, or the idle state when none was started.
        """
        with self.lock:
            if self.current is None:
                return RunStatus()
            self.current.stop(self.stop_timeout)
            self.prepare_next_demo()
            return self.current.status()

    def status(self) -> RunStatus:
        """
        :return: The state of the demo started last, or the idle state; an interpreter
            is prepared for the next demo once no run goes on.
        """
        with self.lock:
            self.prepare_next_demo()
            return RunStatus() if self.current is None else self.current.status()

    def prepare_next_demo(self) -> None:
        """
        Start an interpreter for the next demo while none is prepared and no demo runs
        but a staged scene, so its preparation takes nothing from a run; the caller
        holds :attr:`lock`.
        """
        if self.prepared_program is None or self.prepared is not None:
            return
        if (
            self.current is not None
            and self.current.process.poll() is None
            and self.current.mode is not RunMode.STAGE
        ):
            return
        self.prepared = PreparedProcess.start(
            self.prepared_program,
            self.directory.directory,
            OutputLog(self.log_limit),
        )

    def end_prepared(self) -> None:
        """
        End the interpreter prepared for the next demo, if any.
        """
        with self.lock:
            prepared, self.prepared = self.prepared, None
        if prepared is not None:
            prepared.end()

    def acknowledge(self) -> RunStatus:
        """
        Acknowledge the safety stop of the demo started last, so a demo may start again.

        :return: The state of the demo started last, or the idle state.
        """
        with self.lock:
            if self.current is None:
                return RunStatus()
            self.current.acknowledged = True
            return self.current.status()

    def refuse_to_start(self) -> None:
        """
        Refuse a new demo or validation while the demo started last holds it back.

        :raises SafetyStopNotAcknowledged: While nobody acknowledged the safety stop of
            the demo started last.
        :raises RunRecordSaving: While the demo started last saves its run's record.
        """
        if self.current is None:
            return
        if self.current.awaits_acknowledgement():
            raise SafetyStopNotAcknowledged(self.current.safety_stop().constraint)
        if self.current.state() is RunState.SAVING:
            raise RunRecordSaving()

    def refuse_to_validate(self) -> Optional[DemoProcess]:
        """
        Refuse a validation while a demo performs a plan, as well as whenever a new demo
        is refused; a staged live scene performs none.

        :return: The demo started last, which :meth:`refuse_validated` compares with the
            demo started last once the validation is over.
        :raises PlanRunning: While a demo that is no staged scene runs.
        :raises SafetyStopNotAcknowledged: While nobody acknowledged the safety stop of
            the demo started last.
        :raises RunRecordSaving: While the demo started last saves its run's record.
        """
        with self.lock:
            self.refuse_to_start()
            if (
                self.current is not None
                and self.current.mode is not RunMode.STAGE
                and self.current.state() is RunState.RUNNING
            ):
                raise PlanRunning(self.current.mode.value)
            return self.current

    def refuse_validated(self, before: Optional[DemoProcess]) -> None:
        """
        Refuse a validation's report when any demo started while it was validated: a
        run on the real robot closes the gate as it starts, and the report must not
        open it again, not even when a staged scene replaced that run meanwhile.

        :param before: What :meth:`refuse_to_validate` answered before the validation.
        :raises PlanRunning: When a demo started meanwhile.
        """
        with self.lock:
            if self.current is not before:
                raise PlanRunning(self.current.mode.value)

    def release(self) -> None:
        """
        Stop the demo started last, if any; the caller holds :attr:`lock`.
        """
        if self.current is not None:
            self.current.stop(self.stop_timeout)
