"""
Requests, plans and configurations the plan builder refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path

from typing_extensions import Any, List

from cramera.exceptions import ViewerError

# %% base


@dataclass
class PlanBuilderError(ViewerError):
    """
    A request or configuration the plan builder cannot serve.
    """


# %% request bodies


@dataclass
class ContentTooLarge(PlanBuilderError):
    """
    A request body larger than the builder accepts.
    """

    size: int
    """
    The body's size in bytes.
    """

    limit: int
    """
    The largest accepted size in bytes.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    def message(self) -> str:
        return (
            f"The request holds {self.size} bytes; at most {self.limit} are accepted."
        )


@dataclass
class MalformedRequestBody(PlanBuilderError):
    """
    A request body that is no UTF-8 JSON text.
    """

    reason: str
    """
    Why the decoder refused it.
    """

    def message(self) -> str:
        return f"The request body is no JSON text: {self.reason}"


# %% plans


@dataclass
class InvalidPlanText(PlanBuilderError):
    """
    A name, label, task or constraint that is blank, too long or holds control
    characters.
    """

    location: str
    """
    Where the text sits in the plan.
    """

    text: str
    """
    The text.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} must be a short text without control characters, "
            f"not {self.text!r}."
        )


@dataclass
class InvalidPlanName(PlanBuilderError):
    """
    A plan name that is not a valid plan name, see
    :class:`~cramera_protocol.plan_builder.PlanName`.
    """

    name: str
    """
    The name.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.name!r} cannot be a plan's name: use 1 to 64 letters, digits, "
            f"spaces, '_' or '-', with no space at the start or end."
        )


@dataclass
class InvalidIdentifier(PlanBuilderError):
    """
    A plan, robot, object or step identity of other characters than a plan allows.
    """

    location: str
    """
    Where the identity sits in the plan.
    """

    identifier: str
    """
    The identity.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} {self.identifier!r} must start with a letter and hold "
            f"only letters, digits and '_'."
        )


@dataclass
class DuplicateIdentifier(PlanBuilderError):
    """
    Two robots, objects or steps of a plan with one identity.
    """

    location: str
    """
    Where the identities sit in the plan.
    """

    identifier: str
    """
    The identity they share.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"Two entries of {self.location} are named {self.identifier!r}."


@dataclass
class TooManyEntries(PlanBuilderError):
    """
    More robots, objects, steps or constraints than a plan holds.
    """

    location: str
    """
    Where the entries sit in the plan.
    """

    count: int
    """
    How many entries there are.
    """

    limit: int
    """
    How many entries fit.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"{self.location} holds {self.count} entries; at most {self.limit} fit."


@dataclass
class ValueOutOfRange(PlanBuilderError):
    """
    A coordinate or joint value that is not finite or beyond the limit.
    """

    location: str
    """
    Where the number sits in the plan.
    """

    value: float
    """
    The number.
    """

    limit: float
    """
    The largest allowed magnitude.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} is {self.value}; it must be finite and at most "
            f"{self.limit} in magnitude."
        )


@dataclass
class UnknownReference(PlanBuilderError):
    """
    A plan entry naming a robot or object the plan does not hold.
    """

    location: str
    """
    Where the reference sits in the plan.
    """

    reference: str
    """
    The name that was referred to.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} names {self.reference!r}, which the plan does not hold."
        )


class MissingValue(StrEnum):
    """
    What a step's field lacks, as a person reads it.
    """

    VALUE = "a value"
    """
    A required field without any value.
    """

    POSE = "a pose"
    """
    A destination or region at a pose or marker that names no pose.
    """

    ZONE = "a zone"
    """
    A destination or region in a zone that names no zone.
    """

    OBJECT = "an object"
    """
    A destination on top of an object that names no object.
    """

    SURFACE = "a surface"
    """
    A destination on a surface that names no surface type.
    """


@dataclass
class MissingStepParameter(PlanBuilderError):
    """
    A step without a value its action needs.
    """

    location: str
    """
    Where the value is missing in the plan.
    """

    parameter: str
    """
    The name of the missing field or key.
    """

    step_number: int
    """
    The step's position in its robot's plan, counted from one, as the page numbers it.
    """

    step_title: str
    """
    The step's title as the page shows it: its skill's label, else its action's.
    """

    field_label: str
    """
    The label of the field the value is missing in, as the page shows it.
    """

    needed: MissingValue
    """
    What the field lacks.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"Step {self.step_number} ({self.step_title}) needs {self.needed} for "
            f"“{self.field_label}”."
        )


@dataclass
class UnknownStepAction(PlanBuilderError):
    """
    A step naming an action the installed CRAM workspace does not offer.
    """

    location: str
    """
    Where the step's action sits in the plan.
    """

    action: str
    """
    The action's class name.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} is {self.action!r}, an action the installed CRAM "
            f"workspace does not offer."
        )


@dataclass
class UnavailableStepAction(PlanBuilderError):
    """
    A step naming an action the builder cannot fill in, since a required field has no
    form control.
    """

    location: str
    """
    Where the step's action sits in the plan.
    """

    action: str
    """
    The action's class name.
    """

    reason: str
    """
    Why the action is unavailable.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} is {self.action!r}, which is unavailable: {self.reason}."
        )


@dataclass
class UnknownStepField(PlanBuilderError):
    """
    A step giving a value of a field its action does not have.
    """

    location: str
    """
    Where the value sits in the plan.
    """

    name: str
    """
    The field's name.
    """

    action: str
    """
    The action's class name.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.location} names {self.name!r}, a field {self.action} does not have."
        )


@dataclass
class InvalidStepValue(PlanBuilderError):
    """
    A step's value that does not fit the kind of its field.
    """

    location: str
    """
    Where the value sits in the plan.
    """

    value: Any
    """
    The value.
    """

    expected: str
    """
    What the value must be.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"{self.location} is {self.value!r}; it must be {self.expected}."


@dataclass
class PlanWithoutRobot(PlanBuilderError):
    """
    A plan whose demo is asked for while it holds no robot to perform it.
    """

    name: str
    """
    The plan's name.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"The plan {self.name!r} holds no robot to perform it."


@dataclass
class UnsupportedByCatalog(PlanBuilderError):
    """
    A plan entry the catalog does not offer: an unknown robot, object model,
    environment, surface type or zone, or an enumeration member the robot does not have.
    """

    location: str
    """
    Where the entry sits in the plan.
    """

    value: str
    """
    The value the catalog does not offer.
    """

    offered: List[str]
    """
    What the catalog offers there.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"{self.location} is {self.value!r}, which is not one of {self.offered}."


@dataclass
class PlanTooLarge(PlanBuilderError):
    """
    A plan whose JSON is larger than the builder keeps.
    """

    size: int
    """
    The plan's size in bytes.
    """

    limit: int
    """
    The largest size in bytes.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    def message(self) -> str:
        return f"The plan holds {self.size} bytes; at most {self.limit} are kept."


@dataclass
class UnknownPlan(PlanBuilderError):
    """
    A plan identity no saved plan has.
    """

    identifier: str
    """
    The identity.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"No saved plan is named {self.identifier!r}."


@dataclass
class UnreadablePlan(PlanBuilderError):
    """
    A saved plan whose file is damaged.
    """

    identifier: str
    """
    The plan's identity.
    """

    reason: str
    """
    Why it cannot be read.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def message(self) -> str:
        return f"The saved plan {self.identifier!r} cannot be read: {self.reason}"


@dataclass
class RunModeNotOffered(PlanBuilderError):
    """
    A run mode the robot profile offers no executor for.
    """

    mode: str
    """
    The run mode's value.
    """

    robot: str
    """
    The label of the profile's robot.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"{self.robot} offers no {self.mode!r} run."


@dataclass
class UnsafeStoragePath(PlanBuilderError):
    """
    A storage directory or file that is a symbolic link, which could lead writes
    elsewhere.
    """

    path: Path
    """
    The path.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def message(self) -> str:
        return f"{str(self.path)!r} is a symbolic link; the plan builder does not follow it."


# %% constraints


@dataclass
class ConstraintSubjectMissing(PlanBuilderError):
    """
    A constraint whose monitor needs an object while neither the sentence nor its step
    names one.
    """

    text: str
    """
    The constraint as it was written.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.text!r} is about no object: name one of the plan's objects or "
            f"attach it to a step that acts on one."
        )


@dataclass
class ConstraintReferenceMissing(PlanBuilderError):
    """
    A constraint whose monitor measures against a second object the sentence does not
    name.
    """

    text: str
    """
    The constraint as it was written.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"{self.text!r} names no second object to keep a distance from: name two "
            f"of the plan's objects."
        )


# %% environments


@dataclass
class NotAUrdfFile(PlanBuilderError):
    """
    A path given to load as an environment that names no URDF or xacro file.
    """

    path: str
    """
    The path as it was given.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"{self.path!r} is no URDF or xacro file on the viewer's machine."


@dataclass
class NotAFolder(PlanBuilderError):
    """
    A path the URDF picker is asked to list that names no folder.
    """

    path: str
    """
    The path as it was given.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"{self.path!r} is no folder on the viewer's machine."


@dataclass
class FolderNotReadable(PlanBuilderError):
    """
    A folder the viewer may not list.
    """

    path: str
    """
    The folder.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.FORBIDDEN

    def message(self) -> str:
        return f"The viewer may not list {self.path!r}."


# %% CRAM


@dataclass
class CramUnavailable(PlanBuilderError):
    """
    A request that needs the CRAM workspace, which this interpreter cannot import.
    """

    missing: List[str]
    """
    The modules that cannot be found.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.SERVICE_UNAVAILABLE

    def message(self) -> str:
        return (
            f"The CRAM workspace is not importable here; missing modules: "
            f"{', '.join(self.missing)}."
        )


# %% runs


@dataclass
class DemoNotStarted(PlanBuilderError):
    """
    A demo whose process could not be started.
    """

    path: Path
    """
    The demo file.
    """

    reason: str
    """
    Why the operating system refused.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def message(self) -> str:
        return f"The demo {str(self.path)!r} could not be started: {self.reason}"


# %% bridge processes


@dataclass
class BridgeProcessFailed(PlanBuilderError):
    """
    A request whose bridge process ended without an answer.
    """

    reason: str
    """
    Why it failed in one short line: the message of the exception the process ended
    with, or its last printed line.
    """

    output: str
    """
    The newest output of the process, warnings and traceback included.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.BAD_GATEWAY

    def diagnostic_output(self) -> str:
        return self.output


# %% scans


@dataclass
class ScanFailed(BridgeProcessFailed):
    """
    A scan of the scene whose process ended without a scan.
    """

    def message(self) -> str:
        return f"The scene could not be scanned: {self.reason}"


# %% validation


@dataclass
class ValidationFailed(BridgeProcessFailed):
    """
    A validation of a plan whose process ended without a report.
    """

    def message(self) -> str:
        return f"The plan could not be validated: {self.reason}"


@dataclass
class PlanNotValidated(PlanBuilderError):
    """
    A run that needs the plan's passed validation, of a plan whose latest version did
    not pass one.
    """

    mode: str
    """
    The run mode's value.
    """

    real: bool = False
    """
    Whether the run moves the real robot.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.CONFLICT

    def message(self) -> str:
        if self.real:
            return (
                "A run on the real robot needs the plan to pass its validation first: "
                "press Validate, then run it."
            )
        return (
            f"A {self.mode!r} run needs the plan to pass its validation first; validate "
            f"the plan as it stands now."
        )


@dataclass
class SafetyStopNotAcknowledged(PlanBuilderError):
    """
    A run or a validation asked for while nobody acknowledged the safety stop that
    cancelled the demo started last.
    """

    constraint: str
    """
    The broken safety limit.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.CONFLICT

    def message(self) -> str:
        return (
            f"Safety constraint violated: {self.constraint}. Acknowledge the stop "
            f"before the plan runs or is validated again."
        )


@dataclass
class PlanRunning(PlanBuilderError):
    """
    A validation asked for while a demo performs a plan, or one during which such a demo
    started: its report would let the plan run again while it runs.
    """

    mode: str
    """
    The running demo's run mode value.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.CONFLICT

    def message(self) -> str:
        return (
            f"A {self.mode!r} run is going on; validate the plan again once it is "
            f"over or stopped."
        )


@dataclass
class RunRecordSaving(PlanBuilderError):
    """
    A run or a validation asked for while the demo started last still saves the record
    of its run, which a new demo would cut short.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.CONFLICT

    def message(self) -> str:
        return (
            "The last run's record is still being saved; validate or run the plan "
            "again once it is saved."
        )


# %% reach


@dataclass
class ReachNotMeasured(BridgeProcessFailed):
    """
    A measure of what a robot reaches whose process ended without one.
    """

    def message(self) -> str:
        return f"What the robot reaches could not be measured: {self.reason}"
