"""
Validating a plan before it runs on the robot: every step of the performing robot is
tried in a copy of the twin without moving anything, and the report says of each step
whether it holds, holds on objects only assumed, or fails and why.

A plan runs on the robot only once its latest version passed; a plan edit or a change of
the scene invalidates the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import List, Optional

from cramera_protocol.document import JsonDocument, document_field
from cramera_protocol.plan_builder import DemoDocument, PlanDocument, PlannedObject

# %% verdicts


class StepVerdict(StrEnum):
    """
    How a step fared when the plan was validated.
    """

    VALID = "valid"
    """
    The step succeeded in the copy of the twin.
    """

    ASSUMED = "assumed"
    """
    The step succeeded on objects taken from the twin, since the camera does not see
    them now; they are perceived again when the plan runs.
    """

    FAILED = "failed"
    """
    The step did not succeed.
    """

    UNCHECKED = "unchecked"
    """
    The step was not tried, since a step before it failed.
    """


class StepFailureReason(StrEnum):
    """
    Why a step failed its validation.
    """

    UNREACHABLE = "unreachable"
    """
    The robot cannot reach where the step asks it to.
    """

    COLLISION = "collision"
    """
    The step's motion would bring the robot into collision.
    """

    NOT_PERCEIVED = "not_perceived"
    """
    No object matching the detection was seen, or assumed, in its region.
    """

    AMBIGUOUS = "ambiguous"
    """
    Several objects match the detection and nothing tells them apart.
    """

    SAFETY_LIMIT = "safety_limit"
    """
    The step's motion would break a safety limit of the robot profile: an arm's tool
    would leave its workspace box, or come closer to its edge than a validated plan
    may.
    """

    SKILL_NOT_OFFERED = "skill_not_offered"
    """
    The step names a skill the robot profile does not offer.
    """

    UNSUCCESSFUL = "unsuccessful"
    """
    The step failed for another reason, which its detail names.
    """


class StepValidationKey(StrEnum):
    """
    The keys of one step's validation.
    """

    STEP = "step"
    """
    The step's identity.
    """

    VERDICT = "verdict"
    """
    How the step fared.
    """

    REASON = "reason"
    """
    Why it failed.
    """

    ARM = "arm"
    """
    The arm the step was given.
    """

    DETAIL = "detail"
    """
    What failed it, in the planner's words.
    """


@dataclass(kw_only=True)
class StepValidation(JsonDocument):
    """
    How one step of the performing robot fared when the plan was validated.
    """

    step: str = document_field(StepValidationKey.STEP)
    """
    The identity of the step.
    """

    verdict: StepVerdict = document_field(StepValidationKey.VERDICT)
    """
    How it fared.
    """

    reason: Optional[StepFailureReason] = document_field(
        StepValidationKey.REASON, default=None
    )
    """
    Why it failed, or None for a step that did not fail.
    """

    arm: Optional[str] = document_field(StepValidationKey.ARM, default=None)
    """
    The arm a step that leaves its arm to be chosen automatically is given, as the
    member identifier of the arm enumeration; None for any other step.
    """

    detail: str = document_field(StepValidationKey.DETAIL, default="")
    """
    The planner's own account of the failure, for developers.
    """


# %% report


class ValidationReportKey(StrEnum):
    """
    The keys of a validation's report.
    """

    STEPS = "steps"
    """
    How each step fared.
    """

    SECONDS = "seconds"
    """
    How long the validation took.
    """


@dataclass(kw_only=True)
class ValidationReport(JsonDocument):
    """
    How each step of the performing robot fared when the plan was validated, in the
    order of the steps.
    """

    steps: List[StepValidation] = document_field(
        ValidationReportKey.STEPS, default_factory=list
    )
    """
    How each step fared.
    """

    seconds: float = document_field(ValidationReportKey.SECONDS, default=0.0)
    """
    How long the validation took, in seconds.
    """

    @property
    def passed(self) -> bool:
        """
        Whether every step succeeded, on perceived or on assumed objects.
        """
        return all(
            step.verdict in (StepVerdict.VALID, StepVerdict.ASSUMED)
            for step in self.steps
        )


# %% requests


class ValidationRequestKey(StrEnum):
    """
    The keys of a request to validate a plan.
    """

    PLAN = "plan"
    """
    The plan to validate.
    """


@dataclass(kw_only=True)
class ValidationRequest(JsonDocument):
    """
    Asks the plan builder to validate a plan.
    """

    plan: PlanDocument = document_field(ValidationRequestKey.PLAN)
    """
    The plan, whose selected robot's steps are validated.
    """


class ValidationOrderKey(StrEnum):
    """
    The keys of what the bridge validates.
    """

    DEMO = "demo"
    """
    The plan and its environment.
    """

    SIMULATED_OBJECTS = "simulatedObjects"
    """
    The objects of the simulated scene the camera is rendered from.
    """

    READS_ROBOT_POSTURE = "readsRobotPosture"
    """
    Whether the robot starts from the joint positions the real robot reports.
    """


@dataclass(kw_only=True)
class ValidationOrder(JsonDocument):
    """
    What the bridge validates: a checked plan in its environment, and the scene the
    robot's camera sees.
    """

    demo: DemoDocument = document_field(ValidationOrderKey.DEMO)
    """
    The plan and its environment.
    """

    simulated_objects: Optional[List[PlannedObject]] = document_field(
        ValidationOrderKey.SIMULATED_OBJECTS, default=None
    )
    """
    The objects standing in a simulated scene that the robot's camera is rendered from,
    or None to ask the robot's own perception.
    """

    reads_robot_posture: bool = document_field(
        ValidationOrderKey.READS_ROBOT_POSTURE, default=False
    )
    """
    Whether the robot starts from the joint positions the real robot reports, rather
    than from the posture the plan gives it.
    """
