"""
Validating a plan: the report of how each step fared, which passes only when no step
failed or went unchecked, and the run mode that starts only for a validated plan.
"""

from __future__ import annotations

import pytest

from cramera_protocol.browser import BROWSER_ENUMERATIONS
from cramera_protocol.plan_builder import (
    DemoDocument,
    PlanDocument,
    RunMode,
    RunRequest,
)
from cramera_protocol.validation import (
    StepFailureReason,
    StepValidation,
    StepVerdict,
    ValidationOrder,
    ValidationReport,
    ValidationRequest,
)

# %% reports


def test_a_report_and_its_order_survive_their_json():
    report = ValidationReport(
        steps=[
            StepValidation(step="step_1", verdict=StepVerdict.VALID, arm="left"),
            StepValidation(
                step="step_2",
                verdict=StepVerdict.FAILED,
                reason=StepFailureReason.UNREACHABLE,
                detail="stopped approaching its goal",
            ),
        ],
        seconds=2.5,
    )
    order = ValidationOrder(demo=DemoDocument(plan=PlanDocument(name="plan")))
    request = ValidationRequest(plan=PlanDocument(name="plan"))
    assert ValidationReport.from_json(report.to_json()) == report
    assert ValidationOrder.from_json(order.to_json()) == order
    assert ValidationRequest.from_json(request.to_json()) == request


@pytest.mark.parametrize(
    "verdicts, passed",
    [
        ([StepVerdict.VALID, StepVerdict.ASSUMED], True),
        ([StepVerdict.VALID, StepVerdict.FAILED], False),
        ([StepVerdict.FAILED, StepVerdict.UNCHECKED], False),
    ],
)
def test_a_report_passes_once_every_step_succeeded_on_seen_or_assumed_objects(
    verdicts, passed
):
    report = ValidationReport(
        steps=[
            StepValidation(step=f"step_{number}", verdict=verdict)
            for number, verdict in enumerate(verdicts)
        ]
    )
    assert report.passed is passed


# %% the robot's run


def test_only_the_robots_run_needs_a_validated_plan():
    assert [mode for mode in RunMode if mode.needs_validation] == [RunMode.ROBOT]


def test_the_robots_run_has_no_executor_of_its_own():
    assert RunMode.ROBOT.default_executor is None
    assert RunMode.ROBOT.performs_plan


def test_a_run_request_keeps_the_validation_unless_it_overrides_it():
    request = RunRequest(plan=PlanDocument(name="plan"), mode=RunMode.ROBOT)
    overriding = RunRequest(
        plan=PlanDocument(name="plan"), mode=RunMode.ROBOT, override_validation=True
    )
    assert RunRequest.from_json(request.to_json()).override_validation is False
    assert RunRequest.from_json(overriding.to_json()) == overriding


def test_the_browser_reads_the_names_of_a_validation():
    assert {StepVerdict, StepFailureReason} <= set(BROWSER_ENUMERATIONS)
