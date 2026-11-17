"""
Checking plans against the builder's limits and its catalog, the steps' values against
the schemas of their actions, and keeping plans in the plan directory.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from typing_extensions import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Sized,
    Type,
    Union,
)

from cramera_plan_builder.exceptions import (
    DuplicateIdentifier,
    InvalidIdentifier,
    InvalidPlanName,
    InvalidPlanText,
    InvalidStepValue,
    MissingStepParameter,
    MissingValue,
    PlanTooLarge,
    PlanWithoutRobot,
    TooManyEntries,
    UnavailableStepAction,
    UnknownPlan,
    UnknownReference,
    UnknownStepAction,
    UnknownStepField,
    UnreadablePlan,
    UnsafeStoragePath,
    UnsupportedByCatalog,
    ValueOutOfRange,
)
from cramera_protocol.detection import (
    DetectedObjectReference,
    DetectionRegion,
    DetectionRegionKey,
    RegionKind,
)
from cramera_protocol.document import JsonValue
from cramera_protocol.exceptions import ProtocolError
from cramera_protocol.geometry import Pose
from cramera_protocol.plan_builder import (
    ActionField,
    ActionFieldKind,
    ActionSchema,
    Destination,
    DestinationKey,
    PlacementMode,
    PlanBuilderCatalog,
    PlanBuilderLimits,
    PlanDocument,
    PlanKey,
    PlanName,
    PlannedObject,
    PlannedObjectKey,
    PlannedRobot,
    PlannedRobotKey,
    PlanRecord,
    PlanStep,
    PlanStepKey,
    PlanSummary,
    RobotModel,
    StepConstraintKey,
)

NAME_IDENTITY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
"""
What a robot, object or step identity looks like: it names bodies and namespaces in the
built world.
"""

PLAN_IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,96}")
"""
What a saved plan's identity looks like.
"""

CONTROL_CHARACTER_LIMIT = 32
"""
Characters below this code point are control characters, which texts must not hold.
"""

PLAN_INDENTATION = 2
"""
The indentation of a saved plan's JSON, so it stays readable in an editor.
"""

LOCATION_SEPARATOR = "."
"""
Joins the keys of a location in a plan, for error messages.
"""


class PlanFile(StrEnum):
    """
    The files of the plan directory.
    """

    SUFFIX = ".json"
    """
    The suffix of a saved plan, named after its identity.
    """

    TEMPORARY_PREFIX = ".plan-"
    """
    The prefix of a plan being written, which is hidden until it is complete.
    """


class ExpectedValue(StrEnum):
    """
    What a step's value must be, by the kind of its field, for error messages.
    """

    IDENTITY = "the identity of one of the plan's objects"
    """
    A body field's value.
    """

    REGION = "a region of the scene to look in"
    """
    A region field's value.
    """

    NAME = "the name of an annotated entity"
    """
    An annotation field's value.
    """

    MEMBER = "the name of a member of the enumeration"
    """
    An enumeration field's value.
    """

    POSE = "a pose of seven numbers"
    """
    A pose field's value.
    """

    DESTINATION = "a pose, surface, zone, object or marker to put the object down at"
    """
    The value of a field that accepts a surface.
    """

    NUMBER = "a finite number"
    """
    A number field's value.
    """

    INTEGER = "a whole number"
    """
    An integer field's value.
    """

    BOOLEAN = "true or false"
    """
    A boolean field's value.
    """

    TEXT = "a short text"
    """
    A text field's value.
    """

    NOTHING = "left out, since it is derived from the arm and the object"
    """
    The value of a derived field, which a step does not give.
    """


# %% limits


@dataclass(frozen=True)
class PlanCheck:
    """
    Checks that a plan is complete and stays within the builder's limits.
    """

    limits: PlanBuilderLimits
    """
    The limits.
    """

    def check(self, plan: PlanDocument) -> None:
        """
        :param plan: A plan as a browser sent it.
        :raises InvalidPlanName: For a name that is not a :class:`PlanName`.
        :raises InvalidPlanText: For an empty or overly long text or one with control
            characters.
        :raises InvalidIdentifier: For an identity of other characters.
        :raises DuplicateIdentifier: For two robots, objects or steps of one identity.
        :raises TooManyEntries: For more robots, objects, steps or constraints than fit.
        :raises ValueOutOfRange: For a number that is not finite or beyond its limit.
        :raises UnknownReference: For a selected robot the plan lacks.
        :raises PlanTooLarge: For a plan whose JSON is too large.
        """
        if not PlanName(plan.name).is_valid():
            raise InvalidPlanName(plan.name)
        if plan.task:
            self.text(PlanKey.TASK, plan.task)
        self.count(PlanKey.ROBOTS, plan.robots, self.limits.maximum_robots)
        self.count(PlanKey.OBJECTS, plan.objects, self.limits.maximum_objects)
        self.unique(PlanKey.ROBOTS, (robot.identifier for robot in plan.robots))
        self.unique(PlanKey.OBJECTS, (item.identifier for item in plan.objects))
        for planned_object in plan.objects:
            self.planned_object(planned_object)
        for robot in plan.robots:
            self.robot(robot)
        robots = {robot.identifier for robot in plan.robots}
        if plan.selected_robot is not None and plan.selected_robot not in robots:
            raise UnknownReference(PlanKey.SELECTED_ROBOT, plan.selected_robot)
        size = len(json.dumps(plan.to_json(), allow_nan=False).encode("utf-8"))
        if size > self.limits.maximum_document_bytes:
            raise PlanTooLarge(size, self.limits.maximum_document_bytes)

    def robot(self, robot: PlannedRobot) -> None:
        """
        :param robot: One robot of a plan.
        """
        location = self.location(PlanKey.ROBOTS, robot.identifier)
        self.identity(PlanKey.ROBOTS, robot.identifier)
        self.text(self.location(location, PlannedRobotKey.LABEL), robot.label)
        self.pose(self.location(location, PlannedRobotKey.POSE), robot.pose)
        for joint, value in robot.joint_positions.items():
            joint_location = self.location(
                location, PlannedRobotKey.JOINT_POSITIONS, joint
            )
            self.text(joint_location, joint)
            self.number(joint_location, value)
        steps_location = self.location(location, PlannedRobotKey.STEPS)
        self.count(steps_location, robot.steps, self.limits.maximum_steps)
        self.unique(steps_location, (step.identifier for step in robot.steps))
        for step in robot.steps:
            self.step(self.location(steps_location, step.identifier), step)

    def step(self, location: str, step: PlanStep) -> None:
        """
        :param location: Where the step sits.
        :param step: One step of a robot's plan; its values are checked against its
            action's schema by :class:`StepValueCheck`.
        """
        self.identity(location, step.identifier)
        self.text(self.location(location, PlanStepKey.ACTION), step.action)
        constraints_location = self.location(location, PlanStepKey.CONSTRAINTS)
        self.count(
            constraints_location, step.constraints, self.limits.maximum_constraints
        )
        for constraint in step.constraints:
            self.text(
                self.location(constraints_location, StepConstraintKey.TEXT),
                constraint.text,
            )

    @staticmethod
    def complete(plan: PlanDocument) -> None:
        """
        :param plan: A plan whose limits were checked.
        :raises PlanWithoutRobot: For a plan without a robot, which a saved plan may be
            but a demo may not.
        """
        if not plan.robots:
            raise PlanWithoutRobot(plan.name)

    def planned_object(self, planned_object: PlannedObject) -> None:
        """
        :param planned_object: One object of a plan.
        """
        location = self.location(PlanKey.OBJECTS, planned_object.identifier)
        self.identity(PlanKey.OBJECTS, planned_object.identifier)
        self.text(self.location(location, PlannedObjectKey.MODEL), planned_object.model)
        self.pose(self.location(location, PlannedObjectKey.POSE), planned_object.pose)

    @staticmethod
    def identity(location: str, identifier: str) -> None:
        """
        :param location: Where the identity sits.
        :param identifier: A robot, object or step identity.
        :raises InvalidIdentifier: Unless it starts with a letter and holds letters,
            digits and ``_`` only.
        """
        if NAME_IDENTITY.fullmatch(identifier) is None:
            raise InvalidIdentifier(location, identifier)

    def unique(self, location: str, identifiers: Iterable[str]) -> None:
        """
        :param location: Where the identities sit.
        :param identifiers: The identities of entries of one list.
        :raises DuplicateIdentifier: When two of them are equal.
        """
        seen = set()
        for identifier in identifiers:
            if identifier in seen:
                raise DuplicateIdentifier(location, identifier)
            seen.add(identifier)

    def text(self, location: str, text: str) -> None:
        """
        :param location: Where the text sits.
        :param text: A name, label, task or constraint.
        :raises InvalidPlanText: When it is blank, too long or holds control
            characters.
        """
        if (
            not text.strip()
            or len(text) > self.limits.maximum_text_length
            or any(ord(character) < CONTROL_CHARACTER_LIMIT for character in text)
        ):
            raise InvalidPlanText(location, text)

    def pose(self, location: str, pose: Pose) -> None:
        """
        :param location: Where the pose sits.
        :param pose: A position and orientation.
        """
        for index, value in enumerate(pose.to_json()):
            self.number(self.location(location, str(index)), value)

    def number(self, location: str, value: float) -> None:
        """
        :param location: Where the number sits.
        :param value: A coordinate or joint value.
        :raises ValueOutOfRange: When it is not finite or beyond the limit.
        """
        maximum = self.limits.maximum_coordinate
        if not math.isfinite(value) or abs(value) > maximum:
            raise ValueOutOfRange(location, value, maximum)

    @staticmethod
    def count(location: str, entries: Sized, limit: int) -> None:
        """
        :param location: Where the entries sit.
        :param entries: A list of a plan.
        :param limit: How many entries fit.
        :raises TooManyEntries: When it holds more.
        """
        if len(entries) > limit:
            raise TooManyEntries(location, len(entries), limit)

    @staticmethod
    def location(*steps: str) -> str:
        """
        :param steps: Keys from the plan's root.
        :return: The location they name, for error messages.
        """
        return LOCATION_SEPARATOR.join(steps)


# %% step values


@dataclass(frozen=True)
class StepValueCheck:
    """
    Checks a step against the schema of its action: that the action is offered and
    available, that each value fits its field and the robot's choices, and that every
    required field is given.
    """

    catalog: PlanBuilderCatalog
    """
    The catalog, with the action schemas and the surface types.
    """

    robot: RobotModel
    """
    The model of the robot whose step it is.
    """

    objects: FrozenSet[str]
    """
    The identities of the plan's objects, and the references to the objects the earlier
    steps of the robot find.
    """

    plan_check: PlanCheck
    """
    Checks texts and numbers against the builder's limits.
    """

    step_number: int
    """
    The step's position in its robot's plan, counted from one.
    """

    step_title: str
    """
    The step's title as the page shows it.
    """

    def check(self, location: str, step: PlanStep) -> None:
        """
        :param location: Where the step sits.
        :param step: One step of the robot's plan.
        :raises UnknownStepAction: For an action the installed workspace does not offer.
        :raises UnavailableStepAction: For an action whose fields have no form control.
        :raises UnknownStepField: For a value of a field the action does not have.
        :raises InvalidStepValue: For a value that does not fit its field.
        :raises UnsupportedByCatalog: For an enumeration member that does not exist or
            the robot does not offer, or a surface type the catalog does not offer.
        :raises UnknownReference: For a body that is none of the plan's objects.
        :raises MissingStepParameter: For a required field without a value.
        """
        schema = self.schema(PlanCheck.location(location, PlanStepKey.ACTION), step)
        values_location = PlanCheck.location(location, PlanStepKey.VALUES)
        for name, value in step.values.items():
            offered = schema.field_named(name)
            field_location = PlanCheck.location(values_location, name)
            if offered is None:
                raise UnknownStepField(field_location, name, schema.name)
            self.value(field_location, offered, value)
        for offered in schema.fields:
            if (
                offered.required
                and offered.kind is not ActionFieldKind.GRASP
                and offered.name not in step.values
            ):
                raise self.missing(
                    values_location, offered.name, offered, MissingValue.VALUE
                )

    def looks_for_objects(self, location: str, step: PlanStep) -> bool:
        """
        :param location: Where the step sits.
        :param step: A step that fits its action's schema.
        :return: Whether its action looks for objects in a region, so later steps can
            name the object it finds.
        """
        schema = self.schema(PlanCheck.location(location, PlanStepKey.ACTION), step)
        return any(offered.kind is ActionFieldKind.REGION for offered in schema.fields)

    def schema(self, location: str, step: PlanStep) -> ActionSchema:
        """
        :param location: Where the step's action sits.
        :param step: A step.
        :return: The schema of its action.
        """
        for schema in self.catalog.actions:
            if schema.name == step.action:
                if not schema.available:
                    raise UnavailableStepAction(
                        location, step.action, schema.unavailable_reason
                    )
                return schema
        raise UnknownStepAction(location, step.action)

    def value(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: The field it is the value of.
        :param value: A JSON value.
        """
        checks: Dict[ActionFieldKind, Callable[[str, ActionField, Any], None]] = {
            ActionFieldKind.BODY: self.body,
            ActionFieldKind.ANNOTATION: self.annotation,
            ActionFieldKind.ENUM: self.member,
            ActionFieldKind.POSE: self.pose,
            ActionFieldKind.POSE_OR_SURFACE: self.destination,
            ActionFieldKind.NUMBER: self.number,
            ActionFieldKind.INTEGER: self.integer,
            ActionFieldKind.BOOLEAN: self.boolean,
            ActionFieldKind.TEXT: self.text,
            ActionFieldKind.GRASP: self.derived,
            ActionFieldKind.REGION: self.region,
        }
        checks[offered.kind](location, offered, value)

    def body(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A body field.
        :param value: Its value, an object's identity or a reference to the object an
            earlier step finds.
        """
        if not isinstance(value, str):
            raise InvalidStepValue(location, value, ExpectedValue.IDENTITY)
        if value not in self.objects:
            raise UnknownReference(location, value)

    def annotation(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: An annotation field.
        :param value: Its value, an annotation's name.
        """
        if not isinstance(value, str):
            raise InvalidStepValue(location, value, ExpectedValue.NAME)
        self.plan_check.text(location, value)

    def member(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: An enumeration field.
        :param value: Its value, a member's name the robot offers.
        """
        if not isinstance(value, str):
            raise InvalidStepValue(location, value, ExpectedValue.MEMBER)
        choices = self.robot.choices.get(offered.enum_type, offered.members)
        if value not in offered.members or value not in choices:
            raise UnsupportedByCatalog(location, value, choices)

    def pose(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A pose field.
        :param value: Its value: a pose, or a destination when the field accepts a
            surface.
        """
        if offered.accepts_surface:
            self.destination(location, offered, value)
            return
        self.plan_check.pose(
            location, self.read(location, Pose, value, ExpectedValue.POSE)
        )

    def destination(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A field accepting a surface.
        :param value: Its value, a destination naming its pose or surface type.
        """
        destination = self.read(location, Destination, value, ExpectedValue.DESTINATION)
        if destination.placement in (PlacementMode.POSE, PlacementMode.MARKER):
            if destination.pose is None:
                raise self.missing(
                    location, DestinationKey.POSE, offered, MissingValue.POSE
                )
            self.plan_check.pose(
                PlanCheck.location(location, DestinationKey.POSE), destination.pose
            )
            return
        if destination.placement is PlacementMode.ZONE:
            self.zone(
                PlanCheck.location(location, DestinationKey.ZONE),
                offered,
                destination.zone,
            )
            return
        if destination.placement is PlacementMode.ON_TOP_OF:
            object_location = PlanCheck.location(location, DestinationKey.OBJECT)
            if destination.object is None:
                raise self.missing(
                    location, DestinationKey.OBJECT, offered, MissingValue.OBJECT
                )
            if destination.object not in self.objects:
                raise UnknownReference(object_location, destination.object)
            return
        if destination.surface_type is None:
            raise self.missing(
                location, DestinationKey.SURFACE_TYPE, offered, MissingValue.SURFACE
            )
        if destination.surface_type not in self.catalog.surface_types:
            raise UnsupportedByCatalog(
                PlanCheck.location(location, DestinationKey.SURFACE_TYPE),
                destination.surface_type,
                self.catalog.surface_types,
            )

    def region(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A region field.
        :param value: Its value, a region of the scene.
        """
        region = self.read(location, DetectionRegion, value, ExpectedValue.REGION)
        if region.kind is RegionKind.ZONE:
            self.zone(
                PlanCheck.location(location, DetectionRegionKey.ZONE),
                offered,
                region.zone,
            )
        if region.kind is RegionKind.MARKER:
            if region.pose is None:
                raise self.missing(
                    location, DetectionRegionKey.POSE, offered, MissingValue.POSE
                )
            self.plan_check.pose(
                PlanCheck.location(location, DetectionRegionKey.POSE), region.pose
            )

    def zone(
        self, location: str, offered: ActionField, identifier: Optional[str]
    ) -> None:
        """
        :param location: Where the zone's identifier sits.
        :param offered: The field naming the zone.
        :param identifier: The identifier of a zone of the robot profile, or None.
        :raises MissingStepParameter: When no zone is named.
        :raises UnsupportedByCatalog: When the robot profile names no such zone.
        """
        if identifier is None:
            raise self.missing(
                location, DestinationKey.ZONE, offered, MissingValue.ZONE
            )
        profile = self.catalog.profile
        zones = [] if profile is None else [zone.identifier for zone in profile.zones]
        if identifier not in zones:
            raise UnsupportedByCatalog(location, identifier, zones)

    def missing(
        self, location: str, parameter: str, offered: ActionField, needed: MissingValue
    ) -> MissingStepParameter:
        """
        :param location: Where the value is missing.
        :param parameter: The name of the missing field or key.
        :param offered: The field the value is missing in.
        :param needed: What the field lacks.
        :return: The refusal naming the step and the field as the page shows them.
        """
        return MissingStepParameter(
            location=location,
            parameter=parameter,
            step_number=self.step_number,
            step_title=self.step_title,
            field_label=offered.label,
            needed=needed,
        )

    @staticmethod
    def read(
        location: str,
        document: Type[JsonValue],
        value: Any,
        expected: ExpectedValue,
    ) -> Any:
        """
        A step's values are JSON the browser sent; this is the boundary where a value
        that is no such document becomes the builder's refusal.

        :param location: Where the value sits.
        :param document: The protocol class the value describes.
        :param value: A JSON value.
        :param expected: What it must be.
        :return: The value it describes.
        :raises InvalidStepValue: When it describes none.
        """
        try:
            return document.from_json(value)
        except ProtocolError as error:
            raise InvalidStepValue(location, value, expected) from error

    @staticmethod
    def number(location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A number field.
        :param value: Its value, a finite number.
        """
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise InvalidStepValue(location, value, ExpectedValue.NUMBER)

    @staticmethod
    def integer(location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: An integer field.
        :param value: Its value, a whole number.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidStepValue(location, value, ExpectedValue.INTEGER)

    @staticmethod
    def boolean(location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A boolean field.
        :param value: Its value, true or false.
        """
        if not isinstance(value, bool):
            raise InvalidStepValue(location, value, ExpectedValue.BOOLEAN)

    def text(self, location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A text field.
        :param value: Its value, a short text.
        """
        if not isinstance(value, str):
            raise InvalidStepValue(location, value, ExpectedValue.TEXT)
        self.plan_check.text(location, value)

    @staticmethod
    def derived(location: str, offered: ActionField, value: Any) -> None:
        """
        :param location: Where the value sits.
        :param offered: A derived field.
        :param value: A value, which a derived field never takes.
        """
        raise InvalidStepValue(location, value, ExpectedValue.NOTHING)


# %% catalog


@dataclass(frozen=True)
class CatalogCheck:
    """
    Checks that a plan uses only what the catalog offers.
    """

    catalog: PlanBuilderCatalog
    """
    The catalog.
    """

    limits: PlanBuilderLimits
    """
    The limits texts and numbers of step values stay within.
    """

    def check(self, plan: PlanDocument) -> None:
        """
        :param plan: A plan whose limits were checked.
        :raises UnsupportedByCatalog: For an environment, robot model or object model
            the catalog does not offer; without the CRAM workspace only the environment
            is checked, since the catalog then offers no robots, objects and actions.
        :raises PlanBuilderError: For a step that does not fit its action's schema, see
            :meth:`StepValueCheck.check`; a step may name the object an earlier step of
            its robot that looks for objects finds.
        """
        self.check_scene(plan)
        if self.catalog.missing:
            return
        models = {model.name: model for model in self.catalog.robots}
        planned = frozenset(item.identifier for item in plan.objects)
        for robot in plan.robots:
            location = PlanCheck.location(PlanKey.ROBOTS, robot.identifier)
            objects = planned
            for position, step in enumerate(robot.steps):
                values = StepValueCheck(
                    catalog=self.catalog,
                    robot=models[robot.model],
                    objects=objects,
                    plan_check=PlanCheck(self.limits),
                    step_number=position + 1,
                    step_title=self.step_title(step),
                )
                location_of_step = PlanCheck.location(
                    location, PlannedRobotKey.STEPS, step.identifier
                )
                values.check(location_of_step, step)
                if values.looks_for_objects(location_of_step, step):
                    objects = objects | {DetectedObjectReference(step.identifier).text}

    def check_scene(self, plan: PlanDocument) -> None:
        """
        Check what the plan's scene is built of, leaving its steps unchecked, as a scan
        of the scene needs it while the steps may still lack the objects it finds.

        :param plan: A plan whose limits were checked.
        :raises UnsupportedByCatalog: For an environment, robot model or object model
            the catalog does not offer; without the CRAM workspace only the environment
            is checked.
        """
        environments = [entry.name for entry in self.catalog.environments]
        if plan.environment is not None:
            self.offered(PlanKey.ENVIRONMENT, plan.environment, environments)
        if self.catalog.missing:
            return
        objects = [model.identifier for model in self.catalog.objects]
        for planned_object in plan.objects:
            self.offered(
                PlanCheck.location(
                    PlanKey.OBJECTS, planned_object.identifier, PlannedObjectKey.MODEL
                ),
                planned_object.model,
                objects,
            )
        models = [model.name for model in self.catalog.robots]
        for robot in plan.robots:
            self.offered(
                PlanCheck.location(
                    PlanKey.ROBOTS, robot.identifier, PlannedRobotKey.MODEL
                ),
                robot.model,
                models,
            )

    def step_title(self, step: PlanStep) -> str:
        """
        :param step: A step of a plan.
        :return: Its title as the page shows it: the label of its skill, else of its
            action, else the action's name.
        """
        profile = self.catalog.profile
        skill = (
            None
            if profile is None or step.skill is None
            else profile.skill_named(step.skill)
        )
        if skill is not None:
            return skill.label
        for schema in self.catalog.actions:
            if schema.name == step.action:
                return schema.label
        return step.action

    @staticmethod
    def offered(location: str, value: str, offered: List[str]) -> None:
        """
        :param location: Where the value sits.
        :param value: A value of the plan.
        :param offered: What the catalog offers there.
        :raises UnsupportedByCatalog: When the value is not offered.
        """
        if value not in offered:
            raise UnsupportedByCatalog(location, value, offered)


# %% storage


@dataclass(frozen=True)
class PlanStore:
    """
    The saved plans, one JSON file per plan, written whole or not at all.
    """

    directory: Path
    """
    The plan directory.
    """

    def summaries(self) -> List[PlanSummary]:
        """
        :return: Every readable saved plan, sorted by identity; a damaged plan does not
            hide the others.
        """
        if not self.directory.is_dir():
            return []
        summaries = []
        for path in sorted(self.directory.glob(f"*{PlanFile.SUFFIX}")):
            if path.is_symlink() or PLAN_IDENTITY.fullmatch(path.stem) is None:
                continue
            read = PlanFileReader(path).read()
            if isinstance(read, UnreadablePlan):
                continue
            summaries.append(
                PlanSummary(
                    identifier=path.stem,
                    name=read.name,
                    updated_at=datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat(),
                )
            )
        return summaries

    def load(self, identifier: str) -> PlanRecord:
        """
        :param identifier: A plan's identity.
        :return: The saved plan.
        :raises InvalidIdentifier: For a malformed identity.
        :raises UnknownPlan: When no plan has the identity.
        :raises UnreadablePlan: When the plan's file is damaged.
        """
        path = self.path(identifier)
        if not path.is_file():
            raise UnknownPlan(identifier)
        read = PlanFileReader(path).read()
        if isinstance(read, UnreadablePlan):
            raise read
        return PlanRecord(identifier=identifier, document=read)

    def save(self, record: PlanRecord) -> PlanRecord:
        """
        :param record: A checked plan, with the identity of the plan it replaces or
            without one for a new plan.
        :return: The saved plan with its identity.
        :raises InvalidIdentifier: For a malformed identity.
        :raises UnsafeStoragePath: When the plan directory or file is a symbolic link.
        """
        identifier = record.identifier or uuid.uuid4().hex
        target = self.path(identifier)
        if self.directory.is_symlink():
            raise UnsafeStoragePath(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.directory,
            prefix=PlanFile.TEMPORARY_PREFIX,
            delete=False,
        ) as temporary:
            json.dump(
                record.document.to_json(),
                temporary,
                indent=PLAN_INDENTATION,
                allow_nan=False,
            )
            temporary.write("\n")
        os.replace(temporary.name, target)
        return PlanRecord(identifier=identifier, document=record.document)

    def path(self, identifier: str) -> Path:
        """
        :param identifier: A plan's identity.
        :return: The plan's file.
        :raises InvalidIdentifier: For a malformed identity.
        :raises UnsafeStoragePath: When the file is a symbolic link.
        """
        if PLAN_IDENTITY.fullmatch(identifier) is None:
            raise InvalidIdentifier(PlanFile.SUFFIX, identifier)
        path = self.directory / f"{identifier}{PlanFile.SUFFIX}"
        if path.is_symlink():
            raise UnsafeStoragePath(path)
        return path


@dataclass(frozen=True)
class PlanFileReader:
    """
    Reads one saved plan file.
    """

    path: Path
    """
    The plan's file, named after its identity.
    """

    def read(self) -> Union[PlanDocument, UnreadablePlan]:
        """
        A saved plan is written by the store, so a file that is no plan is damaged; this
        is the one place where its reading errors become data, so a damaged plan does
        not hide the others.

        :return: The plan, or why it cannot be read.
        """
        try:
            return PlanDocument.from_json(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (ValueError, ProtocolError) as error:
            return UnreadablePlan(self.path.stem, str(error))
