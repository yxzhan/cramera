"""
Answering an order with a bridge process: each order gets a process of its own, started
before the order comes so its imports are done, and a process that does not answer in
time, or ends without an answer, fails its order and serves no other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

from cramera_plan_builder.bridge_processes import (
    BridgeModule,
    BridgeProcess,
    ProcessOutput,
)
from cramera_plan_builder.exceptions import PlanBuilderError, ScanFailed
from cramera_plan_builder.workspace import CramModules
from cramera_protocol.document import JsonDocument, document_field

from .dataset.given_workspace import DATASET

INTERPRETER = DATASET / "ordered_interpreter.py"
"""
Says its process id, then answers, hangs or crashes as its order says.
"""

# %% orders


class Behaviour(StrEnum):
    """
    What the stand-in interpreter does with an order.
    """

    ANSWER = "answer"
    """
    Answers its process id.
    """

    HANG = "hang"
    """
    Never answers.
    """

    CRASH = "crash"
    """
    Writes an answer and then fails.
    """

    RAISE = "raise"
    """
    Prints a warning and fails with a traceback.
    """


@dataclass(kw_only=True)
class BehaviourOrder(JsonDocument):
    """
    An order telling the stand-in interpreter what to do.
    """

    behaviour: Behaviour = document_field("behaviour")
    """
    What the interpreter does.
    """


@dataclass(frozen=True)
class OrderedProcess(BridgeProcess):
    """
    A bridge process of the stand-in interpreter, failing as a scan does.
    """

    @property
    def module(self) -> BridgeModule:
        return BridgeModule.SCANNER

    def describe_failure(self, reason: str, output: str) -> PlanBuilderError:
        return ScanFailed(reason, output)


def ordered_process(timeout: float = 30.0) -> OrderedProcess:
    """
    :return: A bridge process of the stand-in interpreter whose modules are installed.
    """
    return OrderedProcess(
        interpreter=str(INTERPRETER),
        timeout=timeout,
        modules=CramModules(
            finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
        ),
    )


def answer(process: OrderedProcess, behaviour: Behaviour = Behaviour.ANSWER) -> dict:
    """
    :return: The answer of the process to an order of the behaviour.
    """
    return process.answer(BehaviourOrder(behaviour=behaviour))


def process_id_in(failure: PlanBuilderError) -> int:
    """
    :return: The id of the process whose output the failure shows.
    """
    return int(re.search(r"process (\d+)", failure.message()).group(1))


def is_running(process_id: int) -> bool:
    """
    :return: Whether a process of the id still runs.
    """
    return Path(f"/proc/{process_id}").exists()


# %% answering


def test_each_order_is_answered_by_a_process_of_its_own():
    process = ordered_process()

    first = answer(process)
    second = answer(process)

    assert first["pid"] != second["pid"]


def test_the_process_of_the_next_order_is_started_before_the_order():
    process = ordered_process()
    answer(process)
    waiting = process.next_process.waiting.process

    second = answer(process)

    assert second["pid"] == waiting.pid


# %% failing


def test_a_process_not_answering_in_time_is_killed_and_the_next_order_gets_another():
    process = ordered_process(timeout=0.5)

    with pytest.raises(ScanFailed) as failure:
        answer(process, Behaviour.HANG)
    hung = process_id_in(failure.value)
    after = answer(process)

    assert "no answer within 0.5 s" in failure.value.message()
    assert not is_running(hung)
    assert after["pid"] != hung


def test_a_process_failing_after_its_answer_fails_its_order():
    process = ordered_process()

    with pytest.raises(ScanFailed) as failure:
        answer(process, Behaviour.CRASH)

    assert f"process {process_id_in(failure.value)}" in failure.value.message()


def test_a_process_that_ended_while_waiting_is_replaced_by_a_new_one():
    process = ordered_process()
    answer(process)
    waiting = process.next_process.waiting.process
    waiting.kill()
    waiting.wait()

    after = answer(process)

    assert after["pid"] != waiting.pid


def test_a_failed_process_is_reported_by_its_final_error_apart_from_its_output():
    process = ordered_process()

    with pytest.raises(ScanFailed) as failure:
        answer(process, Behaviour.RAISE)

    assert failure.value.reason == (
        "RoboKudo did not answer within 30 s Suggestion: read its log."
    )
    assert "a warning before the failure" in failure.value.output
    assert "Traceback (most recent call last):" in failure.value.output
    assert failure.value.diagnostic_output() == failure.value.output


# %% the reason of a failure


def test_the_reason_of_a_failure_is_the_message_of_the_final_exception():
    output = ProcessOutput(
        "A module compiled with NumPy 1.x cannot run in NumPy 2\n"
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in <module>\n'
        "ImportError: numpy\n"
        "\n"
        "During handling of the above exception, another exception occurred:\n"
        "\n"
        "Traceback (most recent call last):\n"
        '  File "scanning.py", line 102, in scan\n'
        "    grounded = perception.look_for(query)\n"
        "               ^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        "coraplex.exceptions.PerceptionSourceDidNotAnswer: RoboKudo did not answer\n"
        "Suggestion: read its log.\n"
    )

    assert output.final_error(length_limit=300) == (
        "RoboKudo did not answer Suggestion: read its log."
    )


def test_the_reason_of_a_failure_without_a_traceback_is_its_last_line():
    output = ProcessOutput("process 7\nwaiting for the perception\n\n")

    assert output.final_error(length_limit=300) == "waiting for the perception"


def test_the_reason_of_a_failure_is_cut_to_its_length_limit():
    output = ProcessOutput("x" * 500)

    assert len(output.final_error(length_limit=50)) == 50
