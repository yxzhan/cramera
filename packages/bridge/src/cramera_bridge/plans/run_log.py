"""
The run log of a plan: every step as it is scheduled, starts and ends, every attempt at
it, what the camera kept in view, what a step perceived and the safety stop that ended
the run.

Each entry is written to the run's output twice: as a line a person reads, which the
plan builder shows as the run's log, and as the :class:`RunEvent` the run view follows.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from typing_extensions import Callable, List, Optional, TextIO

from cramera_bridge.plans.perception_reports import object_name
from cramera_protocol.plan_builder import (
    MonitorObservation,
    RunEvent,
    RunEventKind,
    RunMonitor,
    StepState,
)
from krrood.ormatic.utils import classproperty

# %% entries


class RunLogEntry(ABC):
    """
    One thing that happened while a plan ran.
    """

    @abstractmethod
    def line(self) -> str:
        """
        :return: The entry as a person reads it in the run log.
        """

    @abstractmethod
    def event(self) -> RunEvent:
        """
        :return: The entry as the run view follows it.
        """


class AttemptOutcome(StrEnum):
    """
    How an attempt at a step ended.
    """

    SUCCEEDED = "succeeded"
    """
    The step succeeded.
    """

    RETRYING = "failed, retrying"
    """
    The step failed and is attempted again.
    """

    ABORTED = "failed, giving up"
    """
    The step failed and no attempt is left, so the plan is aborted.
    """

    STOPPED = "stopped by a safety limit"
    """
    A motion of the step broke a safety limit, which ends the run without another
    attempt.
    """

    @property
    def step_state(self) -> StepState:
        """
        The state the step is in once an attempt ended so.
        """
        if self is AttemptOutcome.SUCCEEDED:
            return StepState.DONE
        if self is AttemptOutcome.RETRYING:
            return StepState.RETRYING
        return StepState.FAILED


@dataclass(frozen=True)
class StepAttempt(RunLogEntry):
    """
    How one attempt at a step ended.
    """

    step: str
    """
    The identity of the step.
    """

    number: int
    """
    Which attempt it was, counting from 1.
    """

    attempts: int
    """
    How many attempts the step has at most.
    """

    outcome: AttemptOutcome
    """
    How the attempt ended.
    """

    reason: str | None = None
    """
    Why the attempt failed, None when it succeeded.
    """

    next_attempt: str | None = None
    """
    How the next attempt differs, None when none follows.
    """

    arm: str | None = None
    """
    The arm the attempt used, by the protocol's identifier; None when its grounding
    chose none.
    """

    def line(self) -> str:
        text = f"{self.step}: attempt {self.number}/{self.attempts} {self.outcome}"
        if self.reason is not None:
            text = f"{text}: {self.reason}"
        if self.next_attempt is not None:
            text = f"{text} Next attempt {self.next_attempt}."
        return text

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.STEP,
            step=self.step,
            state=self.outcome.step_state,
            attempt=self.number,
            attempts=self.attempts,
            arm=self.arm,
            reason=self.reason,
            next_attempt=self.next_attempt,
        )


@dataclass(frozen=True)
class StepScheduled(RunLogEntry):
    """
    A step of the plan waits for its turn, with the arm its grounding chose.
    """

    step: str
    """
    The identity of the step.
    """

    arm: str | None = None
    """
    The arm the step uses, by the protocol's identifier; None when its grounding chose
    none.
    """

    def line(self) -> str:
        if self.arm is None:
            return f"{self.step}: pending"
        return f"{self.step}: pending, with the {self.arm} arm"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.STEP,
            step=self.step,
            state=StepState.PENDING,
            arm=self.arm,
        )


@dataclass(frozen=True)
class StepStarted(RunLogEntry):
    """
    An attempt at a step started.
    """

    step: str
    """
    The identity of the step.
    """

    number: int
    """
    Which attempt it is, counting from 1.
    """

    attempts: int
    """
    How many attempts the step has at most.
    """

    arm: str | None = None
    """
    The arm the attempt uses, by the protocol's identifier; None when its grounding
    chose none.
    """

    def line(self) -> str:
        text = f"{self.step}: attempt {self.number}/{self.attempts} running"
        if self.arm is None:
            return text
        return f"{text} with the {self.arm} arm"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.STEP,
            step=self.step,
            state=StepState.RUNNING,
            attempt=self.number,
            attempts=self.attempts,
            arm=self.arm,
        )


@dataclass(frozen=True)
class ObjectPerceived(RunLogEntry):
    """
    A step perceived an object.
    """

    step: str
    """
    The identity of the step.
    """

    target: str
    """
    The identity of the object it perceived.
    """

    color: str | None = None
    """
    The color the object shows, by the protocol's identifier; None when it shows none or
    several.
    """

    def line(self) -> str:
        if self.color is None:
            return f"{self.step}: perceived {self.target}"
        return f"{self.step}: perceived {self.target} ({self.color})"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.PERCEIVED,
            step=self.step,
            target=self.target,
            color=self.color,
        )


@dataclass(frozen=True)
class StepRegrounded(RunLogEntry):
    """
    Objects a step expects in place were found elsewhere on the table before the step
    moved anything, and the step and the ones after it were grounded anew where they
    lie, without counting an attempt.
    """

    step: str
    """
    The identity of the step.
    """

    targets: List[str]
    """
    The identities of the objects, in the order the step names them.
    """

    def line(self) -> str:
        return (
            f"{self.step}: {', '.join(self.targets)} found elsewhere on the table; "
            f"the step was grounded again where it lies"
        )

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.REGROUNDED,
            step=self.step,
            target=", ".join(self.targets),
        )


@dataclass(frozen=True)
class StepContinuedUnseen(RunLogEntry):
    """
    Objects a step expects in place were not seen, or the robot's perception did not
    answer, before the step moved anything, and the step acts where the plan was
    grounded with them, as a lenient runtime check allows.
    """

    step: str
    """
    The identity of the step.
    """

    targets: List[str]
    """
    The identities of the objects, in the order the step names them.
    """

    reason: str
    """
    What was not seen or did not answer, as a person reads it.
    """

    def line(self) -> str:
        names = ", ".join(object_name(target) for target in self.targets)
        return f"{self.step}: {self.reason}; continuing where the plan expected {names}"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.CONTINUED_UNSEEN,
            step=self.step,
            target=", ".join(self.targets),
            reason=self.reason,
        )


@dataclass(frozen=True)
class SafetyWatched(RunLogEntry):
    """
    Every motion of the run is held to the robot profile's safety limits.
    """

    def line(self) -> str:
        return "safety: limits watched ✓"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.MONITOR,
            monitor=RunMonitor.SAFETY,
            observation=MonitorObservation.HOLDS,
        )


class RecordSaving(RunLogEntry):
    """
    The run is over and its robot released, on the real robot once Giskard ended and the
    arms were commanded zero velocities; the run's record is saved now, which takes a
    while.
    """

    def line(self) -> str:
        return "run over, robot released; saving the run's record"

    def event(self) -> RunEvent:
        return RunEvent(kind=RunEventKind.RECORD_SAVING)


@dataclass(frozen=True)
class RunRefused(RunLogEntry):
    """
    The check of the real robot found something missing, so the run ended before it
    started Giskard or commanded anything.
    """

    refusal: str
    """
    What is missing and what to do, as a person reads it.
    """

    def line(self) -> str:
        return self.refusal

    def event(self) -> RunEvent:
        return RunEvent(kind=RunEventKind.REFUSED, reason=self.refusal)


@dataclass(frozen=True)
class SafetyStopped(RunLogEntry):
    """
    A motion broke a safety limit, which ended the run.
    """

    constraint: str
    """
    The broken safety limit and how it was broken, as a person reads it.
    """

    step: Optional[str] = None
    """
    The identity of the step whose motion broke it, None outside every step.
    """

    def line(self) -> str:
        return f"safety: violated ✗ ({self.constraint})"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.SAFETY_STOP, step=self.step, constraint=self.constraint
        )


class View(StrEnum):
    """
    Whether the camera sees what a step keeps in view.
    """

    IN_VIEW = "in view ✓"
    """
    The target lies inside the camera's field of view.
    """

    OUT_OF_VIEW = "out of view ✗"
    """
    The target lies outside the camera's field of view.
    """


@dataclass(frozen=True)
class LookAt(RunLogEntry):
    """
    What a step's look-at observed once it changed.
    """

    step: str
    """
    The identity of the step keeping its target in view.
    """

    target: str
    """
    The identity of the object kept in view.
    """

    view: View
    """
    Whether the camera sees it.
    """

    def line(self) -> str:
        return f"{self.step}: look-at: {self.view} ({self.target})"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.MONITOR,
            step=self.step,
            monitor=RunMonitor.LOOK_AT,
            observation=(
                MonitorObservation.HOLDS
                if self.view is View.IN_VIEW
                else MonitorObservation.VIOLATED
            ),
            target=self.target,
        )


# %% the log


@dataclass
class RunLog:
    """
    The entries of a run, each written as a line to :attr:`stream` once it happened and
    handed to every listener.
    """

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    """
    Where the lines are written.
    """

    entries: list[RunLogEntry] = field(default_factory=list)
    """
    The entries written so far, in order.
    """

    listeners: list[Callable[[RunLogEntry], None]] = field(default_factory=list)
    """
    Called with every entry once it was written.
    """

    @classproperty
    def line_prefix(cls) -> str:
        """
        :return: What every line of the run log starts with, telling it from the rest
            of the run's output.
        """
        return "[run log]"

    def write(self, entry: RunLogEntry) -> None:
        """
        :param entry: What happened; written as its line and as its event.
        """
        self.entries.append(entry)
        print(f"{self.line_prefix} {entry.line()}", file=self.stream)
        print(entry.event().to_output_line(), file=self.stream, flush=True)
        for listener in self.listeners:
            listener(entry)

    def events(self) -> List[RunEvent]:
        """
        :return: The entries written so far as the run view follows them, in order.
        """
        return [entry.event() for entry in self.entries]
