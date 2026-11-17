"""
The plan builder's documents: the plans a person composes from robot actions, the
catalog of robots, environments and objects a plan uses, the constraints written in
plain language, and the demos generated from a plan and run as child processes.

A plan names its robots, its objects and their poses, and each robot's steps; a step
names a native action by its class name and gives the action's fields as JSON values.
The generated demo builds the world and the native actions from it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType

from typing_extensions import Any, Dict, List, Optional, Self

from cramera_protocol.bundle.index import SceneIndexEntry, SceneName
from cramera_protocol.document import JsonDocument, ValueLocation, document_field
from cramera_protocol.geometry import Bounds, Pose, Vector3
from cramera_protocol.live_messages import (
    ConstraintMonitorType,
    MonitorSpecification,
    SurfaceDescription,
)
from cramera_protocol.robot_profile import (
    CameraMount,
    ExecutorKind,
    PerceptionSettings,
    RobotProfile,
    SafetyLimits,
    Zone,
)
from cramera_protocol.version import CURRENT_VERSION, ProtocolVersion

PLAN_FORMAT_VERSION = ProtocolVersion(major=1, minor=4)
"""
The version plans are written with: steps name an action, its fields, enumeration
members and surface types, and robots a model, by the protocol's identifiers; a step
may name the robot profile's skill it was made from, and the plan the platform version
it was saved with.

Plans of an earlier version are migrated when they are read: those before
:data:`CLASS_NAME_FORMAT_VERSION` name a step kind, which becomes an action; those of
that version name the planner's classes, and those of
:data:`ACTION_IDENTIFIER_FORMAT_VERSION` its member and annotation names, which
:class:`SavedNames` translates; those of :data:`IDENTIFIER_FORMAT_VERSION` only take
the current version.
"""

IDENTIFIER_FORMAT_VERSION = ProtocolVersion(major=1, minor=3)
"""
The version whose plans name everything by the protocol's identifiers but neither a
step's skill nor the platform version.
"""

ACTION_IDENTIFIER_FORMAT_VERSION = ProtocolVersion(major=1, minor=2)
"""
The version whose plans name actions and robot models by identifiers but enumeration
members and surface types by the planner's names.
"""

CLASS_NAME_FORMAT_VERSION = ProtocolVersion(major=1, minor=1)
"""
The version whose plans name actions and robot models by the planner's class names.
"""

# %% plugin


class PlanBuilderName(StrEnum):
    """
    The names under which the plan builder is installed.
    """

    PLUGIN = "plan-builder"
    """
    The plugin's name, which is also its route segment and its side panel's identity.
    """


class PlanBuilderFile(StrEnum):
    """
    The browser files of the plan builder, relative to its web directory.
    """

    MODULE = "index.js"
    """
    The module the viewer registers, which links to the builder page.
    """

    PAGE = "builder.html"
    """
    The builder page, which needs the whole window.
    """

    STEPS_STYLESHEET = "steps.css"
    """
    The look of plan step cards, shared by the builder page and the viewer's panel.
    """


class PlanBuilderRoute(StrEnum):
    """
    The plan builder's routes, below ``/api/plugins/plan-builder/``.
    """

    CATALOG = "catalog"
    """
    The robots, environments, objects, saved plans and limits the builder offers.
    """

    PLANS = "plans"
    """
    The saved plans; a plan record posted here is saved.
    """

    PLAN = "plans/"
    """
    The prefix of one saved plan: ``plans/<identifier>``.
    """

    CONSTRAINT = "constraint"
    """
    Compiles a constraint written in plain language into the monitor it means.
    """

    DEMO = "demo"
    """
    The demo generated from a posted plan, without writing it.
    """

    DEMOS = "demos"
    """
    Writes the demo generated from a posted plan into the output directory.
    """

    RUN = "run"
    """
    The state of the demo process; a run request posted here starts a demo.
    """

    RUN_STOP = "run/stop"
    """
    Stops the demo process.
    """

    RUN_ACKNOWLEDGE = "run/acknowledge"
    """
    Acknowledges the safety stop of the demo started last, after which a demo may start
    again.
    """

    SCAN = "scan"
    """
    Looks for the objects in a region of the scene; a posted scan request answers with
    the plan's objects after the scan.
    """

    VALIDATE = "validate"
    """
    Tries every step of a posted plan in a copy of the twin without moving anything; a
    posted validation request answers with the validation's report.
    """

    REACH = "reach"
    """
    What a robot model reaches and the table it works at; a posted reach request answers
    with the robot's reach.
    """

    OBJECT_FILES = "objects/"
    """
    The prefix of the object models' files: ``objects/<file>``.
    """

    ROBOT_FILES = "robots/"
    """
    The prefix of the robot models' files: ``robots/<robot>/<robot>.urdf`` describes a
    robot's model at rest, and names its meshes relative to itself.
    """

    ENVIRONMENTS = "environments"
    """
    Writes a URDF or xacro file of this machine as an environment bundle.
    """

    ENVIRONMENT_ANNOTATIONS = "environment-annotations"
    """
    The semantic annotations of an environment, which annotation fields offer; the
    :class:`EnvironmentAnnotationsParameter` values name the environment.
    """

    URDF_FOLDER = "urdf-folder"
    """
    Lists a folder of this machine: its folders and its URDF and xacro files; the
    :class:`UrdfFolderParameter` values name the folder.
    """

    POSTURE = "posture"
    """
    The joint positions the real robot's drivers report last, mirrored while the
    builder is attached to the real robot.
    """


class PlanBuilderPageParameter(StrEnum):
    """
    The query parameters of the builder page, :attr:`PlanBuilderFile.PAGE`.
    """

    PLAN = "plan"
    """
    The identity of a saved plan the page opens.
    """

    ENVIRONMENT = "environment"
    """
    The name of an environment bundle a new plan starts in.
    """


class PlanBuilderVariable(StrEnum):
    """
    The environment variables the plan builder reads.
    """

    OUTPUT = "CRAMERA_PLAN_BUILDER_OUTPUT"
    """
    The directory generated demos are written to, below cramera's data directory by
    default.
    """

    PORT = "CRAMERA_PLAN_BUILDER_PORT"
    """
    The port started demos publish their world on, the live publisher's port by default.
    """


# %% destinations


class PlacementMode(StrEnum):
    """
    How a step that puts an object down names where.
    """

    POSE = "pose"
    """
    At an exact pose.
    """

    SURFACE = "surface"
    """
    On or in an annotated surface of the environment, at a free spot found when the step
    runs.
    """

    ZONE = "zone"
    """
    Into a named zone of the robot profile, at the free spot within reach nearest its
    centre, found when the plan is grounded.
    """

    ON_TOP_OF = "on_top_of"
    """
    On top of another object, wherever that object stands when the plan is grounded.
    """

    MARKER = "marker"
    """
    At a marker dragged across the scene: its position and heading, resting on the
    surface the marker snapped to.
    """


class DestinationKey(StrEnum):
    """
    The keys of where a step puts an object down.
    """

    PLACEMENT = "placement"
    """
    Whether the object goes to a pose or onto a surface.
    """

    POSE = "pose"
    """
    The pose.
    """

    SURFACE_TYPE = "surfaceType"
    """
    The class of the surface.
    """

    SURFACE_NAME = "surfaceName"
    """
    The surface.
    """

    ZONE = "zone"
    """
    The named zone.
    """

    OBJECT = "object"
    """
    The object the step puts its object on top of.
    """


@dataclass(kw_only=True)
class Destination(JsonDocument):
    """
    Where a step puts an object down: at a pose, or on or in a surface of the
    environment; the value of a field that accepts a surface.
    """

    placement: PlacementMode = document_field(
        DestinationKey.PLACEMENT, default=PlacementMode.POSE
    )
    """
    Whether the object goes to :attr:`pose` or onto a surface.
    """

    pose: Optional[Pose] = document_field(DestinationKey.POSE, default=None)
    """
    The pose in world coordinates the object is put down at; at a marker, the marker's
    pose, resting on the surface below it.
    """

    surface_type: Optional[str] = document_field(
        DestinationKey.SURFACE_TYPE, default=None
    )
    """
    The class name of the surface annotation the object is put on or in.
    """

    surface_name: Optional[str] = document_field(
        DestinationKey.SURFACE_NAME, default=None
    )
    """
    The name of one surface annotation, or None for any surface of the type.
    """

    zone: Optional[str] = document_field(DestinationKey.ZONE, default=None)
    """
    The identifier of the robot profile's zone the object is put into.
    """

    object: Optional[str] = document_field(DestinationKey.OBJECT, default=None)
    """
    The identity of the object, or the reference to the object a detection finds, that
    the step puts its object on top of.
    """

    @property
    def is_grounded_late(self) -> bool:
        """
        Whether the pose the object goes to is only known once the plan is grounded: in
        a zone, on top of an object or at a marker.
        """
        return self.placement in (
            PlacementMode.ZONE,
            PlacementMode.ON_TOP_OF,
            PlacementMode.MARKER,
        )


# %% constraints


class StepConstraintKey(StrEnum):
    """
    The keys of a constraint attached to a step.
    """

    TEXT = "text"
    """
    The constraint as it was written.
    """

    MONITOR_TYPE = "monitorType"
    """
    The monitor the sentence means.
    """

    MONITOR = "monitor"
    """
    The monitor observed while the step runs.
    """

    SWITCH = "switch"
    """
    The switch of the step's action the constraint turns on.
    """


@dataclass(kw_only=True)
class StepConstraint(JsonDocument):
    """
    A constraint written in plain language and what it compiled to: a monitor the live
    publisher observes, a switch of the step's own action, both or neither.
    """

    text: str = document_field(StepConstraintKey.TEXT)
    """
    The constraint as it was written.
    """

    monitor_type: Optional[ConstraintMonitorType] = document_field(
        StepConstraintKey.MONITOR_TYPE, default=None
    )
    """
    The monitor the sentence means, or None when no rule matches it.
    """

    monitor: Optional[MonitorSpecification] = document_field(
        StepConstraintKey.MONITOR, default=None
    )
    """
    The monitor the live publisher observes while the step runs, or None when the
    sentence names nothing to observe.
    """

    switch: Optional[str] = document_field(StepConstraintKey.SWITCH, default=None)
    """
    The boolean field of the step's action the constraint turns on, or None.
    """


class ConstraintTextKey(StrEnum):
    """
    The keys of a request compiling a constraint.
    """

    TEXT = "text"
    """
    The constraint as it was written.
    """

    STEP_ACTION = "stepAction"
    """
    The action of the step the constraint is attached to.
    """

    STEP_OBJECT = "stepObject"
    """
    The object the step acts on.
    """

    OBJECTS = "objects"
    """
    The names of the plan's objects.
    """


@dataclass(kw_only=True)
class ConstraintText(JsonDocument):
    """
    A constraint to compile, with what the plan tells about its subject.
    """

    text: str = document_field(ConstraintTextKey.TEXT)
    """
    The constraint as it was written.
    """

    step_action: Optional[str] = document_field(
        ConstraintTextKey.STEP_ACTION, default=None
    )
    """
    The class name of the action of the step it is attached to, or None for a constraint
    not attached yet.
    """

    step_object: Optional[str] = document_field(
        ConstraintTextKey.STEP_OBJECT, default=None
    )
    """
    The identifier of the object the step acts on, which the constraint is about when
    the sentence names no object.
    """

    objects: List[str] = document_field(ConstraintTextKey.OBJECTS, default_factory=list)
    """
    The identifiers of the plan's objects, which a sentence can name.
    """


class UrdfEnvironmentKey(StrEnum):
    """
    The keys of a request loading a URDF as an environment.
    """

    PATH = "path"
    """
    The URDF or xacro file, as a path on the viewer's machine.
    """


@dataclass(kw_only=True)
class UrdfEnvironment(JsonDocument):
    """
    A URDF or xacro file of the viewer's machine to load as an environment.
    """

    path: str = document_field(UrdfEnvironmentKey.PATH)
    """
    The file, absolute or starting with ``~``.
    """


class EnvironmentAnnotationsParameter(StrEnum):
    """
    The query parameters of :attr:`PlanBuilderRoute.ENVIRONMENT_ANNOTATIONS`.
    """

    ENVIRONMENT = "environment"
    """
    The environment bundle's name.
    """


class EnvironmentAnnotationsKey(StrEnum):
    """
    The keys of an environment's semantic annotations.
    """

    ANNOTATIONS = "annotations"
    """
    The annotations, each its class's identifier and its name.
    """


@dataclass(kw_only=True)
class EnvironmentAnnotations(JsonDocument):
    """
    The semantic annotations of an environment as a plan run finds them, so an
    annotation field offers their names; none without the CRAM workspace.
    """

    annotations: List[SurfaceDescription] = document_field(
        EnvironmentAnnotationsKey.ANNOTATIONS, default_factory=list
    )
    """
    The annotations, sorted by class and name.
    """


class UrdfFolderParameter(StrEnum):
    """
    The query parameters of :attr:`PlanBuilderRoute.URDF_FOLDER`.
    """

    PATH = "path"
    """
    The folder to list; the home folder without it.
    """


class UrdfFolderKey(StrEnum):
    """
    The keys of a folder listed to pick a URDF from.
    """

    PATH = "path"
    """
    The folder, as an absolute path.
    """

    PARENT = "parent"
    """
    The folder above it, or null at the root.
    """

    FOLDERS = "folders"
    """
    The names of the folders in it.
    """

    FILES = "files"
    """
    The names of the URDF and xacro files in it.
    """


@dataclass(kw_only=True)
class UrdfFolder(JsonDocument):
    """
    A folder of the viewer's machine as the URDF picker shows it; hidden entries are
    left out.
    """

    path: str = document_field(UrdfFolderKey.PATH)
    """
    The folder, as an absolute path.
    """

    parent: Optional[str] = document_field(UrdfFolderKey.PARENT, default=None)
    """
    The folder above it, or None at the root.
    """

    folders: List[str] = document_field(UrdfFolderKey.FOLDERS, default_factory=list)
    """
    The names of the folders in it, sorted.
    """

    files: List[str] = document_field(UrdfFolderKey.FILES, default_factory=list)
    """
    The names of the URDF and xacro files in it, sorted.
    """


class LoadedEnvironmentKey(StrEnum):
    """
    The keys of the answer to loading a URDF as an environment.
    """

    ENTRY = "entry"
    """
    The written bundle as the catalog lists it.
    """

    MISSING_MESHES = "missingMeshes"
    """
    The mesh references that resolved to no file.
    """


@dataclass(kw_only=True)
class LoadedEnvironment(JsonDocument):
    """
    An environment bundle written from a URDF or xacro file.
    """

    entry: SceneIndexEntry = document_field(LoadedEnvironmentKey.ENTRY)
    """
    The bundle as the catalog's environments list it.
    """

    missing_meshes: List[str] = document_field(
        LoadedEnvironmentKey.MISSING_MESHES, default_factory=list
    )
    """
    The mesh references of the file that resolved to no file, left out of the bundle.
    """


class RuleValue(StrEnum):
    """
    Values of a rule's monitor parameters that stand for something the sentence or the
    plan supplies.
    """

    OBJECT = "@object"
    """
    The body name of the object the constraint is about.
    """

    LENGTH = "@length"
    """
    The length the sentence gives, in metres, or the rule's default length.
    """

    REFERENCE = "@reference"
    """
    The body name of a second object the sentence names, which the first is measured
    against.
    """


class ConstraintRuleKey(StrEnum):
    """
    The keys of one rule of the constraint rule book.
    """

    MONITOR_TYPE = "monitorType"
    """
    The monitor a matching sentence means.
    """

    PHRASING = "phrasing"
    """
    The regular expression a sentence matches.
    """

    EXAMPLES = "examples"
    """
    Wordings the rule understands, for people.
    """

    SENTENCE = "sentence"
    """
    A whole constraint the rule understands, offered to start from.
    """

    EFFECT = "effect"
    """
    What the monitor checks, for people.
    """

    PARAMETERS = "parameters"
    """
    The monitor's parameters.
    """

    DEFAULT_LENGTH = "defaultLength"
    """
    The length the monitor uses when the sentence gives none.
    """

    OBSERVED = "observed"
    """
    Whether the live publisher can observe the monitor.
    """

    SWITCH = "switch"
    """
    The boolean field of the step's action the constraint turns on.
    """

    CAMERA_MOUNT = "cameraMount"
    """
    The camera mount of the robots the rule is offered to.
    """


@dataclass(kw_only=True)
class ConstraintRule(JsonDocument):
    """
    One wording of a constraint and the monitor it means.
    """

    monitor_type: ConstraintMonitorType = document_field(ConstraintRuleKey.MONITOR_TYPE)
    """
    The monitor a matching sentence means.
    """

    phrasing: str = document_field(ConstraintRuleKey.PHRASING)
    """
    The regular expression a lowercase sentence is searched for.
    """

    examples: str = document_field(ConstraintRuleKey.EXAMPLES)
    """
    Wordings the rule understands, for the builder's explanation.
    """

    sentence: str = document_field(ConstraintRuleKey.SENTENCE)
    """
    A whole constraint the rule understands, which the builder offers to start from.
    """

    effect: str = document_field(ConstraintRuleKey.EFFECT)
    """
    What the monitor checks, for the builder's explanation.
    """

    parameters: Dict[str, Any] = document_field(
        ConstraintRuleKey.PARAMETERS, default_factory=dict
    )
    """
    The monitor's parameters by
    :class:`~cramera_protocol.live_messages.MonitorParameterKey`; a :class:`RuleValue`
    stands for what the sentence or the plan supplies.
    """

    default_length: Optional[float] = document_field(
        ConstraintRuleKey.DEFAULT_LENGTH, default=None
    )
    """
    The length in metres :attr:`RuleValue.LENGTH` stands for when the sentence gives
    none.
    """

    observed: bool = document_field(ConstraintRuleKey.OBSERVED, default=True)
    """
    Whether the live publisher can observe the monitor from the parameters alone.
    """

    switch: Optional[str] = document_field(ConstraintRuleKey.SWITCH, default=None)
    """
    The name of the boolean field the constraint turns on; it applies to a step whose
    action has a boolean field of that name.
    """

    camera_mount: Optional[CameraMount] = document_field(
        ConstraintRuleKey.CAMERA_MOUNT, default=None
    )
    """
    The camera mount of the robots the rule is offered to; None offers it to every
    robot.
    """

    def is_offered_to(self, camera_mount: Optional[CameraMount]) -> bool:
        """
        :param camera_mount: How a robot's camera is mounted, None when it is unknown.
        :return: Whether the rule is offered to that robot: a rule naming no mount to
            every robot, one naming a mount to a robot of that mount or of an unknown
            one.
        """
        return (
            self.camera_mount is None
            or camera_mount is None
            or self.camera_mount is camera_mount
        )


class ConstraintRuleBookKey(StrEnum):
    """
    The keys of the constraint rule book.
    """

    RULES = "rules"
    """
    The rules, in the order they are tried.
    """

    UNITS = "units"
    """
    The length units a sentence may use.
    """

    LENGTH = "length"
    """
    The regular expression finding a number and its unit.
    """


@dataclass(kw_only=True)
class ConstraintRuleBook(JsonDocument):
    """
    How sentences become monitors: rules tried in order, and how lengths are read.
    """

    rules: List[ConstraintRule] = document_field(
        ConstraintRuleBookKey.RULES, default_factory=list
    )
    """
    The rules, in the order they are tried; the first match wins.
    """

    units: Dict[str, float] = document_field(
        ConstraintRuleBookKey.UNITS, default_factory=dict
    )
    """
    How many metres one of each unit is, by the unit's spelling.
    """

    length: str = document_field(ConstraintRuleBookKey.LENGTH)
    """
    The regular expression whose first group is a number and second group a unit of
    :attr:`units`.
    """


# %% plan


class PlanStepKey(StrEnum):
    """
    The keys of one step of a robot's plan.
    """

    IDENTIFIER = "id"
    """
    The step's identity within the plan.
    """

    SKILL = "skill"
    """
    The robot profile's skill the step was added as.
    """

    ACTION = "action"
    """
    The native action the step is.
    """

    VALUES = "values"
    """
    The values of the action's fields.
    """

    CONSTRAINTS = "constraints"
    """
    The constraints attached to the step.
    """


@dataclass(kw_only=True)
class PlanStep(JsonDocument):
    """
    One native action of a robot's plan with the values of its fields; a step written in
    the format before :data:`CLASS_NAME_FORMAT_VERSION`, naming a step kind, is read as
    the action that kind stood for.
    """

    identifier: str = document_field(PlanStepKey.IDENTIFIER)
    """
    The step's identity within the plan.
    """

    action: str = document_field(PlanStepKey.ACTION)
    """
    The native action's identifier, as the catalog's action schemas name it.
    """

    skill: Optional[str] = document_field(PlanStepKey.SKILL, default=None)
    """
    The stable identifier of the robot profile's skill the step was added as, which
    names its action again when the profile maps the skill to another one; None for a
    step added as a native action.
    """

    values: Dict[str, Any] = document_field(PlanStepKey.VALUES, default_factory=dict)
    """
    The JSON values of the action's fields by field name: an object's identity, an
    annotation's name, an enumeration member's name, a pose, a :class:`Destination`, a
    number, a boolean or a text, as the field's :class:`ActionFieldKind` asks; fields
    left out keep the action's default.
    """

    constraints: List[StepConstraint] = document_field(
        PlanStepKey.CONSTRAINTS, default_factory=list
    )
    """
    The constraints attached to the step.
    """

    @classmethod
    def read_at(cls, data: Any, location: ValueLocation) -> Self:
        """
        Read a step, migrating one that names a step kind; see
        :meth:`JsonDocument.read_at`.
        """
        if isinstance(data, dict) and LegacyStepKey.KIND in data:
            return LegacyPlanStep.read_at(data, location).migrated()
        return super().read_at(data, location)


# %% steps of earlier plans


class LegacyStepKind(StrEnum):
    """
    The step kinds plans named before :data:`CLASS_NAME_FORMAT_VERSION`; read only to
    migrate such plans.
    """

    PARK_ARMS = "park_arms"
    """
    Parked arms.
    """

    MOVE_TORSO = "move_torso"
    """
    Moved the torso to a named height.
    """

    NAVIGATE = "navigate"
    """
    Drove the base to a pose.
    """

    TRANSPORT = "transport"
    """
    Carried an object to a pose or a surface.
    """

    PICK_UP = "pick_up"
    """
    Picked an object up.
    """

    PLACE = "place"
    """
    Put a held object down at a pose or on a surface.
    """


class MigratedAction(StrEnum):
    """
    The native actions the step kinds of earlier plans stood for, named as plans of
    :data:`CLASS_NAME_FORMAT_VERSION` named them, by the planner's class names; frozen,
    since :class:`SavedNames` translates them further.
    """

    PARK_ARMS = "ParkArmsAction"
    """
    Parks arms.
    """

    MOVE_TORSO = "MoveTorsoAction"
    """
    Moves the torso.
    """

    NAVIGATE = "NavigateAction"
    """
    Drives the base.
    """

    TRANSPORT = "TransportAction"
    """
    Carries an object.
    """

    PICK_UP = "PickUpAction"
    """
    Picks an object up.
    """

    PLACE = "PlaceAction"
    """
    Puts a held object down.
    """


class MigratedActionField(StrEnum):
    """
    The fields of the migrated actions the parameters of earlier steps become.
    """

    ARM = "arm"
    """
    The arm.
    """

    TORSO_STATE = "torso_state"
    """
    The torso height.
    """

    OBJECT = "object_designator"
    """
    The object acted on.
    """

    TARGET = "target_location"
    """
    Where the base drives to or the object is put down.
    """


MIGRATED_ACTIONS = MappingProxyType(
    {
        LegacyStepKind.PARK_ARMS: MigratedAction.PARK_ARMS,
        LegacyStepKind.MOVE_TORSO: MigratedAction.MOVE_TORSO,
        LegacyStepKind.NAVIGATE: MigratedAction.NAVIGATE,
        LegacyStepKind.TRANSPORT: MigratedAction.TRANSPORT,
        LegacyStepKind.PICK_UP: MigratedAction.PICK_UP,
        LegacyStepKind.PLACE: MigratedAction.PLACE,
    }
)
"""
The native action each step kind of earlier plans stood for.
"""

DESTINATION_KINDS = frozenset({LegacyStepKind.TRANSPORT, LegacyStepKind.PLACE})
"""
The step kinds of earlier plans whose target was a :class:`Destination`.
"""


class LegacyStepKey(StrEnum):
    """
    The keys of a step of a plan written before :data:`CLASS_NAME_FORMAT_VERSION`.
    """

    IDENTIFIER = "id"
    """
    The step's identity.
    """

    KIND = "kind"
    """
    The step kind, whose presence marks such a step.
    """

    ARM = "arm"
    """
    The arm.
    """

    TORSO_STATE = "torsoState"
    """
    The torso height.
    """

    OBJECT = "object"
    """
    The object acted on.
    """

    TARGET = "target"
    """
    The pose driven to or put down at.
    """

    PLACEMENT = "placement"
    """
    Whether the object went to the pose or onto a surface.
    """

    SURFACE_TYPE = "surfaceType"
    """
    The class of the surface.
    """

    SURFACE_NAME = "surfaceName"
    """
    The surface.
    """

    CONSTRAINTS = "constraints"
    """
    The constraints.
    """


@dataclass(kw_only=True)
class LegacyPlanStep(JsonDocument):
    """
    A step of a plan written before :data:`CLASS_NAME_FORMAT_VERSION`: a step kind with
    kind-specific parameters, read only to migrate it.
    """

    identifier: str = document_field(LegacyStepKey.IDENTIFIER)
    """
    The step's identity.
    """

    kind: LegacyStepKind = document_field(LegacyStepKey.KIND)
    """
    The step kind.
    """

    arm: Optional[str] = document_field(LegacyStepKey.ARM, default=None)
    """
    The name of the arm choice.
    """

    torso_state: Optional[str] = document_field(LegacyStepKey.TORSO_STATE, default=None)
    """
    The name of the torso state.
    """

    object_identifier: Optional[str] = document_field(
        LegacyStepKey.OBJECT, default=None
    )
    """
    The identity of the object acted on.
    """

    target: Optional[Pose] = document_field(LegacyStepKey.TARGET, default=None)
    """
    The pose driven to or put down at.
    """

    placement: PlacementMode = document_field(
        LegacyStepKey.PLACEMENT, default=PlacementMode.POSE
    )
    """
    Whether the object went to :attr:`target` or onto a surface.
    """

    surface_type: Optional[str] = document_field(
        LegacyStepKey.SURFACE_TYPE, default=None
    )
    """
    The class name of the surface annotation.
    """

    surface_name: Optional[str] = document_field(
        LegacyStepKey.SURFACE_NAME, default=None
    )
    """
    The name of one surface annotation.
    """

    constraints: List[StepConstraint] = document_field(
        LegacyStepKey.CONSTRAINTS, default_factory=list
    )
    """
    The constraints, whose switches already named the action's fields.
    """

    def migrated(self) -> PlanStep:
        """
        :return: The step as the native action its kind stood for, with the parameters
            it gave as the action's values.
        """
        values: Dict[str, Any] = {}
        given = {
            MigratedActionField.ARM: self.arm,
            MigratedActionField.TORSO_STATE: self.torso_state,
            MigratedActionField.OBJECT: self.object_identifier,
        }
        for field_name, value in given.items():
            if value is not None:
                values[field_name.value] = value
        target = self.target_value()
        if target is not None:
            values[MigratedActionField.TARGET.value] = target
        return PlanStep(
            identifier=self.identifier,
            action=MIGRATED_ACTIONS[self.kind].value,
            values=values,
            constraints=self.constraints,
        )

    def target_value(self) -> Any:
        """
        :return: The JSON value of the migrated target: a :class:`Destination` for a
            step putting an object down, a pose for a navigation, or None when the step
            gave none.
        """
        if self.kind in DESTINATION_KINDS:
            if self.target is None and self.surface_type is None:
                return None
            return Destination(
                placement=self.placement,
                pose=self.target,
                surface_type=self.surface_type,
                surface_name=self.surface_name,
            ).to_json()
        if self.target is None:
            return None
        return self.target.to_json()


class PlannedRobotKey(StrEnum):
    """
    The keys of one robot of a plan.
    """

    IDENTIFIER = "id"
    """
    The robot's identity within the plan.
    """

    MODEL = "model"
    """
    The robot's annotation class.
    """

    LABEL = "label"
    """
    The robot's display name.
    """

    POSE = "pose"
    """
    Where the robot starts.
    """

    JOINT_POSITIONS = "jointPositions"
    """
    The robot's start posture.
    """

    STEPS = "steps"
    """
    The robot's plan.
    """


@dataclass(kw_only=True)
class PlannedRobot(JsonDocument):
    """
    One robot of a plan: its model, where it starts and what it does.
    """

    identifier: str = document_field(PlannedRobotKey.IDENTIFIER)
    """
    The robot's identity within the plan; it also namespaces the robot's names in the
    built world, so it starts with a letter and holds letters, digits and ``_``.
    """

    model: str = document_field(PlannedRobotKey.MODEL)
    """
    The robot model's identifier, as the catalog lists it.
    """

    label: str = document_field(PlannedRobotKey.LABEL)
    """
    The robot's display name.
    """

    pose: Pose = document_field(PlannedRobotKey.POSE)
    """
    Where the robot's base starts, in world coordinates.
    """

    joint_positions: Dict[str, float] = document_field(
        PlannedRobotKey.JOINT_POSITIONS, default_factory=dict
    )
    """
    Start values of the robot's joints by their full names.
    """

    steps: List[PlanStep] = document_field(PlannedRobotKey.STEPS, default_factory=list)
    """
    The robot's plan, in the order it runs.
    """


class PlannedObjectKey(StrEnum):
    """
    The keys of one object of a plan.
    """

    IDENTIFIER = "id"
    """
    The object's name in the built world.
    """

    MODEL = "model"
    """
    The object model it is a copy of.
    """

    POSE = "pose"
    """
    Where the object starts.
    """

    COLOR = "color"
    """
    The object's colour.
    """


@dataclass(kw_only=True)
class PlannedObject(JsonDocument):
    """
    One free object of a plan, a copy of an object model at a start pose.
    """

    identifier: str = document_field(PlannedObjectKey.IDENTIFIER)
    """
    The name of the object's body in the built world, which steps and constraints name.
    """

    model: str = document_field(PlannedObjectKey.MODEL)
    """
    The identifier of the object model, as the catalog lists it.
    """

    pose: Pose = document_field(PlannedObjectKey.POSE)
    """
    Where the object starts, in world coordinates.
    """

    color: Optional[str] = document_field(PlannedObjectKey.COLOR, default=None)
    """
    The object's colour as a CSS hexadecimal colour, or None for the model's own.
    """


class PlanKey(StrEnum):
    """
    The keys of a plan.
    """

    FORMAT_VERSION = "formatVersion"
    """
    The format version the plan is written in.
    """

    PLATFORM_VERSION = "platformVersion"
    """
    The version of the platform the plan was saved with.
    """

    NAME = "name"
    """
    The plan's name.
    """

    TASK = "task"
    """
    What the plan does.
    """

    ENVIRONMENT = "environment"
    """
    The environment bundle the plan runs in.
    """

    ROBOTS = "robots"
    """
    The robots.
    """

    SELECTED_ROBOT = "selectedRobot"
    """
    The robot whose plan runs.
    """

    OBJECTS = "objects"
    """
    The free objects.
    """


PLAN_NAME_CHARACTERS = re.compile(r"[A-Za-z0-9_-]+(?: +[A-Za-z0-9_-]+)*")
"""
A plan's name: words of letters, digits, ``_`` or ``-``, separated by spaces.
"""

FILE_NAME_SPACE = "_"
"""
What a space of a plan's name becomes in the names of its demo and recording.
"""


@dataclass(frozen=True)
class PlanName:
    """
    A plan's name as a person gives it, and the file name its demo and recording take.
    """

    text: str
    """
    The name.
    """

    def is_valid(self) -> bool:
        """
        :return: Whether it is words of letters, digits, ``_`` or ``-`` separated by
            spaces, whose file name is a scene name.
        """
        return PLAN_NAME_CHARACTERS.fullmatch(
            self.text
        ) is not None and SceneName.is_valid(self.file_name())

    def file_name(self) -> str:
        """
        :return: The name with each space as :data:`FILE_NAME_SPACE`.
        """
        return self.text.replace(" ", FILE_NAME_SPACE)


@dataclass(kw_only=True)
class PlanDocument(JsonDocument):
    """
    A composed plan: the environment, the robots with their steps, and the free objects;
    the selected robot's steps run when the generated demo runs.
    """

    name: str = document_field(PlanKey.NAME)
    """
    The plan's name; the generated demo's file and recording are named after its
    :meth:`PlanName.file_name`.
    """

    format_version: ProtocolVersion = document_field(
        PlanKey.FORMAT_VERSION, default=PLAN_FORMAT_VERSION
    )
    """
    The format version the plan is written in.

    A plan before :data:`CLASS_NAME_FORMAT_VERSION` is migrated to that version when it
    is read; a plan of that version is translated by :class:`SavedNames` and then
    carries :data:`PLAN_FORMAT_VERSION`.
    """

    task: str = document_field(PlanKey.TASK, default="")
    """
    What the plan does, as the scene picker labels its recording.
    """

    environment: Optional[str] = document_field(PlanKey.ENVIRONMENT, default=None)
    """
    The name of the environment bundle, or None for an empty floor.
    """

    robots: List[PlannedRobot] = document_field(PlanKey.ROBOTS, default_factory=list)
    """
    The robots, in the order they were added.
    """

    selected_robot: Optional[str] = document_field(PlanKey.SELECTED_ROBOT, default=None)
    """
    The identifier of the robot whose plan runs.
    """

    objects: List[PlannedObject] = document_field(PlanKey.OBJECTS, default_factory=list)
    """
    The free objects, in the order they were added.
    """

    platform_version: Optional[str] = document_field(
        PlanKey.PLATFORM_VERSION, default=None
    )
    """
    The version of the platform whose robot profile the plan was last saved with, for
    migrating it when a later platform maps its skills differently; None for a plan
    saved without a profile.
    """

    def __post_init__(self) -> None:
        self.format_version.require_readable_by(CURRENT_VERSION)

    def file_name(self) -> str:
        """
        :return: The name the plan's demo file and recording take, see
            :meth:`PlanName.file_name`.
        """
        return PlanName(self.name).file_name()

    def performing_robot(self) -> Optional[PlannedRobot]:
        """
        :return: The robot whose steps run: the selected one, else the first; None for
            a plan without robots.
        """
        for robot in self.robots:
            if robot.identifier == self.selected_robot:
                return robot
        return self.robots[0] if self.robots else None

    @classmethod
    def read_at(cls, data: Any, location: ValueLocation) -> Self:
        """
        Read a plan, migrating one of an earlier format version; see
        :meth:`JsonDocument.read_at`.
        """
        plan = super().read_at(data, location)
        if plan.format_version < CLASS_NAME_FORMAT_VERSION or cls.names_step_kinds(
            data
        ):
            plan.format_version = CLASS_NAME_FORMAT_VERSION
        return plan

    @staticmethod
    def names_step_kinds(data: Any) -> bool:
        """
        :param data: A plan's JSON object.
        :return: Whether a step names a step kind, which a plan without a format version
            may do and which migrates to the planner's class names.
        """
        robots = data.get(PlanKey.ROBOTS, []) if isinstance(data, dict) else []
        return any(
            isinstance(step, dict) and LegacyStepKey.KIND in step
            for robot in robots
            if isinstance(robot, dict)
            for step in robot.get(PlannedRobotKey.STEPS, [])
        )


# %% saved plans


class PlanSummaryKey(StrEnum):
    """
    The keys of a saved plan in the plan list.
    """

    IDENTIFIER = "id"
    """
    The plan's identity.
    """

    NAME = "name"
    """
    The plan's name.
    """

    UPDATED_AT = "updatedAt"
    """
    When the plan was last saved.
    """


@dataclass(kw_only=True)
class PlanSummary(JsonDocument):
    """
    A saved plan as the plan list offers it.
    """

    identifier: str = document_field(PlanSummaryKey.IDENTIFIER)
    """
    The plan's identity.
    """

    name: str = document_field(PlanSummaryKey.NAME)
    """
    The plan's name.
    """

    updated_at: str = document_field(PlanSummaryKey.UPDATED_AT)
    """
    When the plan was last saved, as an ISO 8601 date and time in UTC.
    """


class PlanRecordKey(StrEnum):
    """
    The keys of a plan with its identity.
    """

    IDENTIFIER = "id"
    """
    The plan's identity.
    """

    DOCUMENT = "document"
    """
    The plan.
    """


@dataclass(kw_only=True)
class PlanRecord(JsonDocument):
    """
    A plan with its identity: what is posted to be saved and what a saved plan is
    answered with.
    """

    document: PlanDocument = document_field(PlanRecordKey.DOCUMENT)
    """
    The plan.
    """

    identifier: Optional[str] = document_field(PlanRecordKey.IDENTIFIER, default=None)
    """
    The plan's identity; None in a request to save a new plan.
    """


class PlanListKey(StrEnum):
    """
    The keys of the plan list.
    """

    PLANS = "plans"
    """
    The saved plans.
    """


@dataclass(kw_only=True)
class PlanList(JsonDocument):
    """
    The saved plans.
    """

    plans: List[PlanSummary] = document_field(PlanListKey.PLANS, default_factory=list)
    """
    The saved plans, sorted by identity.
    """


# %% limits


class PlanBuilderLimitsKey(StrEnum):
    """
    The keys of the plan builder's limits.
    """

    MAXIMUM_ROBOTS = "maximumRobots"
    """
    How many robots a plan holds at most.
    """

    MAXIMUM_OBJECTS = "maximumObjects"
    """
    How many objects a plan holds at most.
    """

    MAXIMUM_STEPS = "maximumSteps"
    """
    How many steps a robot's plan holds at most.
    """

    MAXIMUM_CONSTRAINTS = "maximumConstraints"
    """
    How many constraints a step holds at most.
    """

    MAXIMUM_TEXT_LENGTH = "maximumTextLength"
    """
    How long a name, a task or a constraint is at most.
    """

    MAXIMUM_DOCUMENT_BYTES = "maximumDocumentBytes"
    """
    How large a posted plan is at most.
    """

    MAXIMUM_COORDINATE = "maximumCoordinate"
    """
    The largest magnitude of a coordinate or joint value.
    """

    MAXIMUM_LOG_BYTES = "maximumLogBytes"
    """
    How much of a demo's output is kept.
    """


KIBIBYTE = 1024
"""
The number of bytes in a kibibyte.
"""


@dataclass(kw_only=True)
class PlanBuilderLimits(JsonDocument):
    """
    The bounds every plan and demo stays within; the browser checks against the same
    values the server enforces.
    """

    maximum_robots: int = document_field(PlanBuilderLimitsKey.MAXIMUM_ROBOTS, default=8)
    """
    How many robots a plan holds at most.
    """

    maximum_objects: int = document_field(
        PlanBuilderLimitsKey.MAXIMUM_OBJECTS, default=64
    )
    """
    How many objects a plan holds at most.
    """

    maximum_steps: int = document_field(PlanBuilderLimitsKey.MAXIMUM_STEPS, default=64)
    """
    How many steps a robot's plan holds at most.
    """

    maximum_constraints: int = document_field(
        PlanBuilderLimitsKey.MAXIMUM_CONSTRAINTS, default=16
    )
    """
    How many constraints a step holds at most.
    """

    maximum_text_length: int = document_field(
        PlanBuilderLimitsKey.MAXIMUM_TEXT_LENGTH, default=200
    )
    """
    How many characters a name, a label, a task or a constraint has at most.
    """

    maximum_document_bytes: int = document_field(
        PlanBuilderLimitsKey.MAXIMUM_DOCUMENT_BYTES, default=512 * KIBIBYTE
    )
    """
    How large a posted plan's JSON is at most, in bytes.
    """

    maximum_coordinate: float = document_field(
        PlanBuilderLimitsKey.MAXIMUM_COORDINATE, default=1000.0
    )
    """
    The largest magnitude of a coordinate in metres or a joint value.
    """

    maximum_log_bytes: int = document_field(
        PlanBuilderLimitsKey.MAXIMUM_LOG_BYTES, default=64 * KIBIBYTE
    )
    """
    How many of the newest bytes of a demo's output are kept.
    """


# %% actions


class ActionFieldKind(StrEnum):
    """
    What a field of a native action holds, which decides how the builder asks for it and
    how the demo converts it.
    """

    BODY = "body"
    """
    An object of the plan, named by its identity.
    """

    ANNOTATION = "annotation"
    """
    A world entity carrying a given semantic annotation class, named by the annotation's
    name.
    """

    ENUM = "enum"
    """
    A member of an enumeration, named by the member's name.
    """

    POSE = "pose"
    """
    A pose in world coordinates.
    """

    POSE_OR_SURFACE = "poseOrSurface"
    """
    A pose, or the free spots of a surface sampled when the step runs.
    """

    NUMBER = "number"
    """
    A real number.
    """

    INTEGER = "integer"
    """
    A whole number.
    """

    BOOLEAN = "boolean"
    """
    A switch that is on or off.
    """

    TEXT = "text"
    """
    A text.
    """

    GRASP = "grasp"
    """
    A grasp, never entered: the demo derives it from the action's arm and object.
    """

    REGION = "region"
    """
    A region of the scene objects are looked for in, a
    :class:`~cramera_protocol.detection.DetectionRegion`.
    """


class ActionGroup(StrEnum):
    """
    The packages of native actions the builder groups its palette by.
    """

    CORE = "core"
    """
    The actions the robot performs directly.
    """

    COMPOSITE = "composite"
    """
    The actions made of other actions.
    """


class ActionFieldKey(StrEnum):
    """
    The keys of one field of an action schema.
    """

    NAME = "name"
    """
    The field's identifier.
    """

    SAVED_MEMBERS = "savedMembers"
    """
    The member identifiers by the names plans saved them under before.
    """

    LABEL = "label"
    """
    The field's display name.
    """

    KIND = "kind"
    """
    What the field holds.
    """

    REQUIRED = "required"
    """
    Whether a step must give the field.
    """

    DEFAULT = "default"
    """
    The value the action takes when a step leaves the field out.
    """

    ENUM_TYPE = "enumType"
    """
    The enumeration of an enumeration field.
    """

    MEMBERS = "members"
    """
    The members of the enumeration.
    """

    ANNOTATION = "annotation"
    """
    The annotation class of an annotation field.
    """

    ACCEPTS_SURFACE = "acceptsSurface"
    """
    Whether a pose field also takes a surface.
    """

    DERIVED_FROM = "derivedFrom"
    """
    The fields a derived field is computed from.
    """

    DOCUMENTATION = "documentation"
    """
    What the field means.
    """


@dataclass(kw_only=True)
class ActionField(JsonDocument):
    """
    One keyword argument of a native action the builder offers, with what it holds.
    """

    name: str = document_field(ActionFieldKey.NAME)
    """
    The field's identifier, which a step's values are keyed by and which stays when the
    planner renames the field: its name when the bridge pinned the action.
    """

    label: str = document_field(ActionFieldKey.LABEL)
    """
    The display name.
    """

    kind: ActionFieldKind = document_field(ActionFieldKey.KIND)
    """
    What the field holds.
    """

    required: bool = document_field(ActionFieldKey.REQUIRED)
    """
    Whether a step must give the field before its demo runs.
    """

    default: Any = document_field(ActionFieldKey.DEFAULT, default=None)
    """
    The JSON form of the value the action takes when a step leaves the field out, or
    None when it has none or none JSON can hold.
    """

    enum_type: Optional[str] = document_field(ActionFieldKey.ENUM_TYPE, default=None)
    """
    The identifier of the enumeration of an :attr:`ActionFieldKind.ENUM` field, such as
    ``torso_state``.
    """

    members: List[str] = document_field(ActionFieldKey.MEMBERS, default_factory=list)
    """
    The identifiers of the enumeration's members, in their order, such as ``high``.
    """

    saved_members: Dict[str, str] = document_field(
        ActionFieldKey.SAVED_MEMBERS, default_factory=dict
    )
    """
    The identifier of each member by the name plans of
    :data:`ACTION_IDENTIFIER_FORMAT_VERSION` and earlier saved it under, the planner's
    member name then, such as ``HIGH``.
    """

    annotation: Optional[str] = document_field(ActionFieldKey.ANNOTATION, default=None)
    """
    The identifier of the semantic annotation class an
    :attr:`ActionFieldKind.ANNOTATION` field names an entity of, such as ``handle``.
    """

    accepts_surface: bool = document_field(
        ActionFieldKey.ACCEPTS_SURFACE, default=False
    )
    """
    Whether the field takes a surface besides a pose; its value is then a
    :class:`Destination`.
    """

    derived_from: List[str] = document_field(
        ActionFieldKey.DERIVED_FROM, default_factory=list
    )
    """
    The names of the fields an :attr:`ActionFieldKind.GRASP` field is derived from: the
    arm and the object.
    """

    documentation: str = document_field(ActionFieldKey.DOCUMENTATION, default="")
    """
    What the field means, as its attribute docstring says, or the empty text.
    """


class HiddenActionFieldKey(StrEnum):
    """
    The keys of a field the builder leaves at its default.
    """

    NAME = "name"
    """
    The field's keyword argument.
    """

    TYPE = "type"
    """
    The field's type.
    """


@dataclass(kw_only=True)
class HiddenActionField(JsonDocument):
    """
    A field with a default whose type the builder has no form control for; the action
    keeps its default.
    """

    name: str = document_field(HiddenActionFieldKey.NAME)
    """
    The keyword argument.
    """

    type_name: str = document_field(HiddenActionFieldKey.TYPE)
    """
    The field's type as text, or why it cannot be resolved.
    """


class ActionSchemaKey(StrEnum):
    """
    The keys of one action schema.
    """

    NAME = "name"
    """
    The action's identifier.
    """

    MODULE = "module"
    """
    The module defining the class.
    """

    LABEL = "label"
    """
    The action's display name.
    """

    SAVED_AS = "savedAs"
    """
    The names plans saved the action under before the protocol named it.
    """

    GROUP = "group"
    """
    The package the action belongs to.
    """

    DOCUMENTATION = "documentation"
    """
    What the action does.
    """

    FIELDS = "fields"
    """
    The fields a step gives.
    """

    HIDDEN = "hidden"
    """
    The fields left at their default.
    """

    AVAILABLE = "available"
    """
    Whether a step can be this action.
    """

    UNAVAILABLE_REASON = "unavailableReason"
    """
    Why a step cannot be this action.
    """


@dataclass(kw_only=True)
class ActionSchema(JsonDocument):
    """
    A native action of the CRAM workspace as the builder offers it: its fields, read
    from the action's dataclass fields, and whether every required one has a form
    control.
    """

    name: str = document_field(ActionSchemaKey.NAME)
    """
    The action's identifier, which plans name it by and which stays when the planner
    renames its class, such as ``pick_up``.
    """

    module: str = document_field(ActionSchemaKey.MODULE)
    """
    The module defining the class.
    """

    label: str = document_field(ActionSchemaKey.LABEL)
    """
    The display name: the words of the class name without the ``Action`` suffix.
    """

    group: str = document_field(ActionSchemaKey.GROUP)
    """
    The package below the actions package the module lies in, mostly an
    :class:`ActionGroup` value.
    """

    documentation: str = document_field(ActionSchemaKey.DOCUMENTATION, default="")
    """
    The first paragraph of the class's docstring, or the empty text.
    """

    fields: List[ActionField] = document_field(
        ActionSchemaKey.FIELDS, default_factory=list
    )
    """
    The fields a step gives, required ones first, in the order the class declares them.
    """

    hidden: List[HiddenActionField] = document_field(
        ActionSchemaKey.HIDDEN, default_factory=list
    )
    """
    The fields with a default the builder has no form control for.
    """

    available: bool = document_field(ActionSchemaKey.AVAILABLE, default=True)
    """
    Whether a step can be this action.
    """

    unavailable_reason: Optional[str] = document_field(
        ActionSchemaKey.UNAVAILABLE_REASON, default=None
    )
    """
    Why a step cannot be this action, or None for an available one.
    """

    saved_as: List[str] = document_field(ActionSchemaKey.SAVED_AS, default_factory=list)
    """
    The names plans of format 1.1 and earlier saved the action under, the planner's
    class names then, so such plans are translated to :attr:`name` when read.
    """

    def field_named(self, name: str) -> Optional[ActionField]:
        """
        :param name: A keyword argument.
        :return: The field of that name, or None.
        """
        for candidate in self.fields:
            if candidate.name == name:
                return candidate
        return None


class FailedActionModuleKey(StrEnum):
    """
    The keys of an action module that cannot be imported.
    """

    MODULE = "module"
    """
    The module's name.
    """

    ERROR = "error"
    """
    What importing it raised.
    """


@dataclass(kw_only=True)
class FailedActionModule(JsonDocument):
    """
    A module of the actions package that cannot be imported, so its actions are not
    offered.
    """

    module: str = document_field(FailedActionModuleKey.MODULE)
    """
    The module's name.
    """

    error: str = document_field(FailedActionModuleKey.ERROR)
    """
    The class and message of the error importing it raised.
    """


class ActionCatalogKey(StrEnum):
    """
    The keys of the discovered actions.
    """

    ACTIONS = "actions"
    """
    The action schemas.
    """

    FAILED_MODULES = "failedModules"
    """
    The action modules that cannot be imported.
    """


@dataclass(kw_only=True)
class ActionCatalog(JsonDocument):
    """
    The native actions found in the CRAM workspace's actions package.
    """

    actions: List[ActionSchema] = document_field(
        ActionCatalogKey.ACTIONS, default_factory=list
    )
    """
    The action schemas, core actions first, then by label.
    """

    failed_modules: List[FailedActionModule] = document_field(
        ActionCatalogKey.FAILED_MODULES, default_factory=list
    )
    """
    The modules that cannot be imported, sorted by name.
    """


# %% catalog


class RobotModelKey(StrEnum):
    """
    The keys of one robot model of the catalog.
    """

    NAME = "name"
    """
    The robot's identifier.
    """

    LABEL = "label"
    """
    The robot's display name.
    """

    MODULE = "module"
    """
    The module defining the class.
    """

    SAVED_AS = "savedAs"
    """
    The names plans saved the robot under before the protocol named it.
    """

    CHOICES = "choices"
    """
    The members of the enumerations whose choices depend on the robot.
    """

    STANDING_HEIGHT = "standingHeight"
    """
    How high the robot's root stands above the floor when it stands on it.
    """


@dataclass(kw_only=True)
class RobotModel(JsonDocument):
    """
    A robot annotation of the CRAM workspace and what its parts let it do.
    """

    name: str = document_field(RobotModelKey.NAME)
    """
    The robot's identifier, which plans name it by and which stays when the planner
    renames its annotation class, such as ``pr2``.
    """

    label: str = document_field(RobotModelKey.LABEL, default="")
    """
    The robot's display name, such as ``PR2``; empty in catalogs written before, which
    show the identifier.
    """

    module: str = document_field(RobotModelKey.MODULE)
    """
    The module defining the annotation class.
    """

    saved_as: List[str] = document_field(RobotModelKey.SAVED_AS, default_factory=list)
    """
    The names plans of format 1.1 and earlier saved the robot under, the class names of
    its annotation then, so such plans are translated to :attr:`name` when read.
    """

    choices: Dict[str, List[str]] = document_field(
        RobotModelKey.CHOICES, default_factory=dict
    )
    """
    The identifiers of the members the robot's parts allow, by the identifier of their
    enumeration: the arm choices of its arms, the torso states when it has a torso and
    none otherwise. An enumeration not listed offers all its members.
    """

    standing_height: float = document_field(RobotModelKey.STANDING_HEIGHT, default=0.0)
    """
    How high the robot's root stands above the floor, in metres, when its joints are at
    zero and its feet or base rest on the floor, so a new robot is placed that high; 0
    for robots whose root is on the floor, more for one whose root is its pelvis.
    """


class ObjectModelKey(StrEnum):
    """
    The keys of one object model of the catalog.
    """

    IDENTIFIER = "id"
    """
    The model's file name.
    """

    NAME = "name"
    """
    The model's display name.
    """


@dataclass(kw_only=True)
class ObjectModel(JsonDocument):
    """
    A mesh a plan can place copies of as free objects.
    """

    identifier: str = document_field(ObjectModelKey.IDENTIFIER)
    """
    The mesh's file name, served below :attr:`PlanBuilderRoute.OBJECT_FILES`.
    """

    name: str = document_field(ObjectModelKey.NAME)
    """
    The model's display name, the file name without its suffix.
    """


class PlanTemplateKey(StrEnum):
    """
    The keys of a plan template.
    """

    IDENTIFIER = "id"
    """
    The template's identity within its robot profile.
    """

    PLAN = "plan"
    """
    The plan a new plan copies.
    """


@dataclass(kw_only=True)
class PlanTemplate(JsonDocument):
    """
    A plan a robot profile offers to start from, whose values a person then adjusts.
    """

    identifier: str = document_field(PlanTemplateKey.IDENTIFIER)
    """
    The template's identity within its robot profile.
    """

    plan: PlanDocument = document_field(PlanTemplateKey.PLAN)
    """
    The plan a new plan copies; its name is the template's name.
    """


class PlanBuilderCatalogKey(StrEnum):
    """
    The keys of the plan builder's catalog.
    """

    SAVED_SURFACE_TYPES = "savedSurfaceTypes"
    """
    The surface type identifiers by the names plans saved them under before.
    """

    ROBOTS = "robots"
    """
    The robot models.
    """

    ENVIRONMENTS = "environments"
    """
    The environment bundles.
    """

    OBJECTS = "objects"
    """
    The object models.
    """

    SURFACE_TYPES = "surfaceTypes"
    """
    The classes of surfaces objects can be put on or in.
    """

    ACTIONS = "actions"
    """
    The native actions a step can be.
    """

    FAILED_ACTION_MODULES = "failedActionModules"
    """
    The action modules that cannot be imported.
    """

    CONSTRAINT_RULES = "constraintRules"
    """
    The wordings constraints understand.
    """

    PLANS = "plans"
    """
    The saved plans.
    """

    LIMITS = "limits"
    """
    The bounds plans and demos stay within.
    """

    LIVE_URL = "liveUrl"
    """
    Where started demos publish their world.
    """

    MISSING = "missing"
    """
    The CRAM modules that cannot be found.
    """

    PROFILE = "profile"
    """
    The robot profile the deployment configured.
    """

    TEMPLATES = "templates"
    """
    The plans the robot profile offers to start from.
    """


@dataclass(kw_only=True)
class PlanBuilderCatalog(JsonDocument):
    """
    What the plan builder offers.
    """

    live_url: str = document_field(PlanBuilderCatalogKey.LIVE_URL)
    """
    The base URL of the live publisher started demos publish on.
    """

    robots: List[RobotModel] = document_field(
        PlanBuilderCatalogKey.ROBOTS, default_factory=list
    )
    """
    The robot annotations of the CRAM workspace, sorted by name; empty without CRAM.
    """

    environments: List[SceneIndexEntry] = document_field(
        PlanBuilderCatalogKey.ENVIRONMENTS, default_factory=list
    )
    """
    The bundles without a robot, as the viewer's scene index lists them.
    """

    objects: List[ObjectModel] = document_field(
        PlanBuilderCatalogKey.OBJECTS, default_factory=list
    )
    """
    The object meshes of the CRAM workspace, sorted by identifier.
    """

    surface_types: List[str] = document_field(
        PlanBuilderCatalogKey.SURFACE_TYPES, default_factory=list
    )
    """
    The identifiers of the annotation classes with a supporting surface, such as
    ``table``, sorted.
    """

    saved_surface_types: Dict[str, str] = document_field(
        PlanBuilderCatalogKey.SAVED_SURFACE_TYPES, default_factory=dict
    )
    """
    The identifier of each surface type by the name plans of
    :data:`ACTION_IDENTIFIER_FORMAT_VERSION` and earlier saved it under, the
    annotation's class name then, such as ``Table``.
    """

    actions: List[ActionSchema] = document_field(
        PlanBuilderCatalogKey.ACTIONS, default_factory=list
    )
    """
    The native actions of the CRAM workspace, available or not; empty without CRAM.
    """

    failed_action_modules: List[FailedActionModule] = document_field(
        PlanBuilderCatalogKey.FAILED_ACTION_MODULES, default_factory=list
    )
    """
    The modules of the actions package that cannot be imported.
    """

    constraint_rules: List[ConstraintRule] = document_field(
        PlanBuilderCatalogKey.CONSTRAINT_RULES, default_factory=list
    )
    """
    The rules constraints are compiled with, in the order they are tried.
    """

    plans: List[PlanSummary] = document_field(
        PlanBuilderCatalogKey.PLANS, default_factory=list
    )
    """
    The saved plans.
    """

    limits: PlanBuilderLimits = document_field(
        PlanBuilderCatalogKey.LIMITS, default_factory=PlanBuilderLimits
    )
    """
    The bounds plans and demos stay within.
    """

    missing: List[str] = document_field(
        PlanBuilderCatalogKey.MISSING, default_factory=list
    )
    """
    The CRAM modules that cannot be found; robots, objects, surfaces and actions stay
    empty while one is missing.
    """

    profile: Optional[RobotProfile] = document_field(
        PlanBuilderCatalogKey.PROFILE, default=None
    )
    """
    The robot profile new plans start from, or None when the deployment configured none.
    """

    templates: List[PlanTemplate] = document_field(
        PlanBuilderCatalogKey.TEMPLATES, default_factory=list
    )
    """
    The plans the robot profile offers to start from, in the profile's order.
    """


# %% demos


class DemoDocumentKey(StrEnum):
    """
    The keys of what a generated demo runs.
    """

    PLAN = "plan"
    """
    The plan.
    """

    ENVIRONMENT_DIRECTORY = "environmentDirectory"
    """
    The environment bundle's directory.
    """

    ZONES = "zones"
    """
    The named zones of the robot profile.
    """

    SAFETY = "safety"
    """
    The safety limits of the robot profile.
    """

    PERCEPTION = "perception"
    """
    How the robot profile's perception is asked.
    """


@dataclass(kw_only=True)
class DemoDocument(JsonDocument):
    """
    What a generated demo runs: the plan, with where its environment bundle was found
    when the demo was generated, so the demo finds it in any scenes directory.
    """

    plan: PlanDocument = document_field(DemoDocumentKey.PLAN)
    """
    The plan.
    """

    environment_directory: Optional[str] = document_field(
        DemoDocumentKey.ENVIRONMENT_DIRECTORY, default=None
    )
    """
    The absolute path of the plan's environment bundle, or None for an empty floor.
    """

    zones: List[Zone] = document_field(DemoDocumentKey.ZONES, default_factory=list)
    """
    The robot profile's named zones the plan's steps may name.
    """

    safety: Optional[SafetyLimits] = document_field(
        DemoDocumentKey.SAFETY, default=None
    )
    """
    The robot profile's safety limits every motion of the run is held to, or None.
    """

    perception: PerceptionSettings = document_field(
        DemoDocumentKey.PERCEPTION, default_factory=PerceptionSettings
    )
    """
    How the robot profile's perception is asked.
    """


class DemoOption(StrEnum):
    """
    The options of a generated demo's command line, which the plan builder starts it
    with and cramera-bridge's plan demo command reads.
    """

    STAGE = "--stage"
    """
    Only build the world and publish it until the process is stopped.
    """

    PORT = "--port"
    """
    The port the world is published on.
    """

    LIFELINE = "--lifeline"
    """
    End when standard input closes, which happens when the process that started the demo
    ends.
    """

    EXECUTOR = "--executor"
    """
    How the plan's motions are executed, an :class:`ExecutorKind` value; kinematically
    when it is left out.
    """


class DemoSourceKey(StrEnum):
    """
    The keys of a generated demo.
    """

    FILE_NAME = "fileName"
    """
    The demo's file name.
    """

    SOURCE = "source"
    """
    The demo's Python source.
    """


@dataclass(kw_only=True)
class DemoSource(JsonDocument):
    """
    The demo generated from a plan.
    """

    file_name: str = document_field(DemoSourceKey.FILE_NAME)
    """
    The file name the demo is written under.
    """

    source: str = document_field(DemoSourceKey.SOURCE)
    """
    The demo's Python source.
    """


class SavedDemoKey(StrEnum):
    """
    The keys of a written demo.
    """

    PATH = "path"
    """
    Where the demo was written.
    """

    COMMAND = "command"
    """
    How to run it.
    """


@dataclass(kw_only=True)
class SavedDemo(JsonDocument):
    """
    A demo written into the output directory.
    """

    path: str = document_field(SavedDemoKey.PATH)
    """
    The demo file's absolute path.
    """

    command: str = document_field(SavedDemoKey.COMMAND)
    """
    The command that runs the demo.
    """


# %% runs


class RunMode(StrEnum):
    """
    What a started demo does.
    """

    STAGE = "stage"
    """
    Builds the world and publishes it until it is stopped, so objects can be placed in
    it.
    """

    PERFORM = "perform"
    """
    Performs the selected robot's plan, records it and saves the recording.
    """

    SIMULATE = "simulate"
    """
    Performs the selected robot's plan in a physics simulation, records it and saves the
    recording.
    """

    ROBOT = "robot"
    """
    Performs the selected robot's plan on the real robot, once the plan passed its
    validation; it has no executor unless the robot profile names one.
    """

    @property
    def default_executor(self) -> Optional[ExecutorKind]:
        """
        How a run of this mode executes unless the robot profile says otherwise; None
        for a mode that performs nothing and for one only a profile can execute.
        """
        return DEFAULT_EXECUTORS.get(self)

    @property
    def performs_plan(self) -> bool:
        """
        Whether a run of this mode performs the plan, rather than only staging its
        world.
        """
        return self is not RunMode.STAGE

    @property
    def needs_validation(self) -> bool:
        """
        Whether a run of this mode starts only for a plan that passed its validation.
        """
        return self is RunMode.ROBOT


DEFAULT_EXECUTORS = MappingProxyType(
    {RunMode.PERFORM: ExecutorKind.KINEMATIC, RunMode.SIMULATE: ExecutorKind.PHYSICS}
)
"""
How each run mode that performs the plan executes it unless a robot profile names
another executor.
"""


class ArmHaltLine(StrEnum):
    """
    The lines the plan builder's halt of the real robot's arms prints: that it is
    ready, and, once the plan builder closed its input after the stopped run and its
    Giskard ended, whether it commanded every arm zero velocities and every arm then
    reported standing still.
    """

    READY = "arm halt ready"
    """
    Printed once the halt reaches the controllers' command topics.
    """

    HALTED = (
        "arms halted: zero velocities commanded, every joint reports standing still"
    )
    """
    Printed once every arm was commanded zero velocities and reported standing still.
    """

    NOT_HALTED = "arms NOT confirmed halted"
    """
    Printed when an arm's controller was not found, or an arm did not report standing
    still in time.
    """


class RunState(StrEnum):
    """
    The state of the demo process.
    """

    IDLE = "idle"
    """
    No demo was started.
    """

    RUNNING = "running"
    """
    The demo runs.
    """

    SAVING = "saving"
    """
    The demo's run is over and its robot released; the demo still saves the run's
    record.
    """

    FINISHED = "finished"
    """
    The demo ended on its own with success.
    """

    FAILED = "failed"
    """
    The demo ended on its own with an error.
    """

    SAVE_FAILED = "save_failed"
    """
    The demo's run was over and its robot released, but saving the run's record failed.
    """

    STOPPED = "stopped"
    """
    The demo was stopped; a run on the real robot only once its Giskard ended, both
    arms were commanded zero velocities and reported standing still.
    """

    HALT_UNCONFIRMED = "halt_unconfirmed"
    """
    A run on the real robot was stopped, but the plan builder could not confirm in time
    that its Giskard ended, both arms were commanded zero velocities and reported
    standing still; the robot may still move.
    """

    SAFETY_STOPPED = "safety_stopped"
    """
    The demo was cancelled because a motion broke a safety limit; nothing resumes it.
    """

    REFUSED = "refused"
    """
    A run on the real robot ended before anything moved, as its check of the robot
    found something missing; the plan keeps its validation.
    """


class RunRequestKey(StrEnum):
    """
    The keys of a request starting a demo.
    """

    PLAN = "plan"
    """
    The plan whose demo starts.
    """

    MODE = "mode"
    """
    What the demo does.
    """

    OVERRIDE_VALIDATION = "overrideValidation"
    """
    Whether the run starts without the plan's passed validation.
    """


@dataclass(kw_only=True)
class RunRequest(JsonDocument):
    """
    Starts the demo of a plan, replacing a demo that still runs.
    """

    plan: PlanDocument = document_field(RunRequestKey.PLAN)
    """
    The plan whose demo is generated and started.
    """

    mode: RunMode = document_field(RunRequestKey.MODE, default=RunMode.STAGE)
    """
    What the demo does.
    """

    override_validation: bool = document_field(
        RunRequestKey.OVERRIDE_VALIDATION, default=False
    )
    """
    Whether a run of a mode needing a passed validation starts without one, as developer
    mode allows.
    """


class RunOutputMarker(StrEnum):
    """
    What a line of a demo's output starts with when it tells the plan builder something.
    """

    SAFETY_STOP = "[safety stop]"
    """
    A motion broke a safety limit, which the rest of the line names.
    """

    RUN_EVENT = "[run event]"
    """
    Something happened while the plan ran, which the rest of the line holds as the JSON
    of a :class:`RunEvent`.
    """


# %% run events


class StepState(StrEnum):
    """
    Where a step of a running plan stands.
    """

    PENDING = "pending"
    """
    The step has not started yet.
    """

    RUNNING = "running"
    """
    An attempt at the step runs.
    """

    RETRYING = "retrying"
    """
    An attempt at the step failed and the step is attempted again.
    """

    DONE = "done"
    """
    The step succeeded.
    """

    FAILED = "failed"
    """
    The step failed for good: it used up its attempts or a safety limit stopped it.
    """


class RunEventKind(StrEnum):
    """
    What a run event tells.
    """

    STEP = "step"
    """
    A step changed its state.
    """

    MONITOR = "monitor"
    """
    What a monitor observes changed.
    """

    PERCEIVED = "perceived"
    """
    A step perceived an object, and the color it shows.
    """

    SAFETY_STOP = "safetyStop"
    """
    A motion broke a safety limit, and the run ends.
    """

    RECORD_SAVING = "recordSaving"
    """
    The run is over and its robot released; the run's record is being saved.
    """

    REGROUNDED = "regrounded"
    """
    An object a step expects in place was found elsewhere before the step moved
    anything, and the step and the ones after it were grounded anew where it lies.
    """

    CONTINUED_UNSEEN = "continuedUnseen"
    """
    An object a step expects in place was not seen, or the robot's perception did not
    answer, before the step moved anything, and the step acts where the plan was
    grounded with the object, as a lenient runtime check allows; :attr:`RunEvent.reason`
    says what was not seen or did not answer.
    """

    REFUSED = "refused"
    """
    The check of the real robot before the run found something missing, so the run
    ends before it started Giskard or commanded anything; :attr:`RunEvent.reason` says
    what is missing and what to do.
    """

    PERCEPTION = "perception"
    """
    A perception query of a step was answered, or went unanswered;
    :attr:`RunEvent.perception` says what was asked, what came back and which object
    the step took.
    """


class RunMonitor(StrEnum):
    """
    A monitor of a run whose observations the run reports.
    """

    LOOK_AT = "lookAt"
    """
    Whether the robot's camera keeps a step's object in view.
    """

    SAFETY = "safety"
    """
    Whether every motion keeps to the robot profile's safety limits.
    """


class MonitorObservation(StrEnum):
    """
    What a run's monitor observes.
    """

    HOLDS = "holds"
    """
    What the monitor watches holds: the object is in view, the limits are kept.
    """

    VIOLATED = "violated"
    """
    What the monitor watches is violated.
    """


class RunEventKey(StrEnum):
    """
    The keys of a run event.
    """

    KIND = "kind"
    """
    What the event tells.
    """

    STEP = "step"
    """
    The identity of the step it concerns.
    """

    STATE = "state"
    """
    The state the step changed to.
    """

    ATTEMPT = "attempt"
    """
    Which attempt at the step, counting from 1.
    """

    ATTEMPTS = "attempts"
    """
    How many attempts the step has at most.
    """

    ARM = "arm"
    """
    The arm the attempt uses.
    """

    REASON = "reason"
    """
    Why the attempt failed.
    """

    NEXT_ATTEMPT = "nextAttempt"
    """
    How the next attempt differs.
    """

    MONITOR = "monitor"
    """
    The monitor whose observation changed.
    """

    OBSERVATION = "observation"
    """
    What the monitor observes now.
    """

    TARGET = "target"
    """
    The object the event concerns.
    """

    COLOR = "color"
    """
    The color a perceived object shows.
    """

    CONSTRAINT = "constraint"
    """
    The broken safety limit.
    """

    PERCEPTION = "perception"
    """
    What a perception query asked and what came back.
    """


# %% perception reports


class ReportedObjectVerdict(StrEnum):
    """
    Whether a step took an object its perception reported, and why not.
    """

    ACCEPTED = "accepted"
    """
    The step took it, close enough to where the plan expects it.
    """

    TOO_FAR = "tooFar"
    """
    The step took it, but it lies farther from where the step expected it than the
    step allows, so the step stops before moving.
    """

    NOT_CHOSEN = "notChosen"
    """
    It would do, but the step took another object reported before it.
    """

    WRONG_COLOR = "wrongColor"
    """
    It shows none of the colors asked for.
    """

    WRONG_TYPE = "wrongType"
    """
    It is of another type than the one asked for.
    """

    OUTSIDE_BOX = "outsideBox"
    """
    It lies outside the box the step looked in.
    """


class SurfaceDepthKey(StrEnum):
    """
    The keys of how far a reported object lay inside the surface it stands on.
    """

    SURFACE = "surface"
    """
    The surface it stands on.
    """

    DEPTH = "depth"
    """
    How far it lay inside it.
    """


@dataclass(kw_only=True)
class SurfaceDepth(JsonDocument):
    """
    How far a perception put the object looked for inside the supporting surface it
    stands on, such as a cube a few millimetres inside the table, which the object is
    raised by to rest on the surface.
    """

    surface: str = document_field(SurfaceDepthKey.SURFACE)
    """
    The name of the surface, such as the table.
    """

    depth: float = document_field(SurfaceDepthKey.DEPTH)
    """
    How far, in metres, the object's lowest point lay below the surface's top.
    """


class ReportedObjectKey(StrEnum):
    """
    The keys of an object a perception query reported.
    """

    POSITION = "position"
    """
    Where it was seen.
    """

    SIZE = "size"
    """
    The extent of the box around it.
    """

    COLORS = "colors"
    """
    The colors it was seen in.
    """

    TYPE = "type"
    """
    The type it was reported as.
    """

    VERDICT = "verdict"
    """
    Whether the step took it, and why not.
    """

    DISTANCE = "distance"
    """
    How far it lies from where the step expected the object.
    """

    INSIDE_SURFACE = "insideSurface"
    """
    How far it lay inside the surface it stands on.
    """


@dataclass(kw_only=True)
class ReportedObject(JsonDocument):
    """
    One object a perception query reported, and whether the step took it.
    """

    position: Vector3 = document_field(ReportedObjectKey.POSITION)
    """
    Where it was seen, in metres in the world's root.
    """

    size: Optional[Vector3] = document_field(ReportedObjectKey.SIZE, default=None)
    """
    The extent of the box around it in metres, None when none was measured.
    """

    colors: List[str] = document_field(ReportedObjectKey.COLORS, default_factory=list)
    """
    The colors it was seen in, most dominant first, as the perception names them.
    """

    type: str = document_field(ReportedObjectKey.TYPE, default="")
    """
    The type it was reported as, empty for an object the perception did not classify.
    """

    verdict: ReportedObjectVerdict = document_field(ReportedObjectKey.VERDICT)
    """
    Whether the step took it, and why not.
    """

    distance: Optional[float] = document_field(
        ReportedObjectKey.DISTANCE, default=None
    )
    """
    How far it lies, horizontally, from where the step expected the object, see
    :attr:`PerceptionReport.grounded`, in metres; None for a query without such a
    position.
    """

    inside_surface: Optional[SurfaceDepth] = document_field(
        ReportedObjectKey.INSIDE_SURFACE, default=None
    )
    """
    How far it lay inside the surface it stands on, had it been the plan's object
    looked for; None when it lay inside none, or the query looks for none of the plan's
    objects.
    """


class PerceptionReportKey(StrEnum):
    """
    The keys of a perception report.
    """

    TARGET = "target"
    """
    The plan's object looked for.
    """

    LOOKED_FOR = "lookedFor"
    """
    The object looked for as a person names it.
    """

    TYPE = "type"
    """
    The type asked for.
    """

    COLOR = "color"
    """
    The color asked for.
    """

    BOX = "box"
    """
    The box looked in.
    """

    TABLE_WIDE = "tableWide"
    """
    Whether the box is the whole table.
    """

    GROUNDED = "grounded"
    """
    Where the step expected the object before the query, which distances are
    measured from.
    """

    VALIDATED = "validated"
    """
    Where the plan was validated with the object, when the step expected it elsewhere.
    """

    TOLERANCE = "tolerance"
    """
    How far from there the object may lie.
    """

    ANSWERED = "answered"
    """
    Whether the perception answered.
    """

    OBJECTS = "objects"
    """
    The objects reported.
    """

    SUMMARY = "summary"
    """
    The report as a person reads it.
    """


@dataclass(kw_only=True)
class PerceptionReport(JsonDocument):
    """
    One perception query of a step: what was asked, every object that came back, which
    one the step took and why the others not, and the outcome in one sentence.
    """

    target: Optional[str] = document_field(PerceptionReportKey.TARGET, default=None)
    """
    The identity of the plan's object looked for, None when the query names none.
    """

    looked_for: str = document_field(PerceptionReportKey.LOOKED_FOR, default="")
    """
    The object looked for as a person names it, such as "the red cube" or "a red
    object"; :attr:`type` is what the perception checks.
    """

    type: str = document_field(PerceptionReportKey.TYPE)
    """
    The type asked for, as the perception names it.
    """

    color: Optional[str] = document_field(PerceptionReportKey.COLOR, default=None)
    """
    The color asked for, None for any.
    """

    box: Bounds = document_field(PerceptionReportKey.BOX)
    """
    The box looked in, in metres in the world's root.
    """

    table_wide: bool = document_field(PerceptionReportKey.TABLE_WIDE, default=False)
    """
    Whether the box is the whole table rather than one around where the plan was
    validated.
    """

    grounded: Optional[Vector3] = document_field(
        PerceptionReportKey.GROUNDED, default=None
    )
    """
    Where the step expected the object just before the query, which the distances are
    measured from: where the plan was validated, or, when the step was planned again
    since, where the object was last seen; None when the step expects it nowhere in
    particular.
    """

    validated: Optional[Vector3] = document_field(
        PerceptionReportKey.VALIDATED, default=None
    )
    """
    Where the plan was validated with the object, when the step was planned again since
    with the object expected elsewhere, at :attr:`grounded`; None otherwise.
    """

    tolerance: Optional[float] = document_field(
        PerceptionReportKey.TOLERANCE, default=None
    )
    """
    How far, in metres, from :attr:`grounded` the object may lie for the step to go on.
    """

    answered: bool = document_field(PerceptionReportKey.ANSWERED, default=True)
    """
    Whether the perception answered; an unanswered query reports no objects.
    """

    objects: List[ReportedObject] = document_field(
        PerceptionReportKey.OBJECTS, default_factory=list
    )
    """
    Every object the perception reported, in the order it reported them.
    """

    summary: str = document_field(PerceptionReportKey.SUMMARY, default="")
    """
    The outcome as a person reads it, such as "The red cube was seen 19 cm from where
    the plan was validated, outside the 15 cm box looked in."
    """


@dataclass(kw_only=True)
class RunEvent(JsonDocument):
    """
    One thing that happened while a plan ran, as the run view follows it: a step changed
    its state, a monitor its observation, a step perceived an object, a safety limit
    stopped the run, or the run ended and its record is being saved.

    The fields beside :attr:`kind` that do not concern it are None.
    """

    kind: RunEventKind = document_field(RunEventKey.KIND)
    """
    What the event tells.
    """

    step: Optional[str] = document_field(RunEventKey.STEP, default=None)
    """
    The identity of the step it concerns, None for the whole run.
    """

    state: Optional[StepState] = document_field(RunEventKey.STATE, default=None)
    """
    For a step event: the state the step changed to.
    """

    attempt: Optional[int] = document_field(RunEventKey.ATTEMPT, default=None)
    """
    For a step event: which attempt at the step, counting from 1.
    """

    attempts: Optional[int] = document_field(RunEventKey.ATTEMPTS, default=None)
    """
    For a step event: how many attempts the step has at most.
    """

    arm: Optional[str] = document_field(RunEventKey.ARM, default=None)
    """
    For a step event: the arm the step uses, by the protocol's identifier, None when its
    grounding chose none.
    """

    reason: Optional[str] = document_field(RunEventKey.REASON, default=None)
    """
    For a step that is retried or failed, or a refused run: why, as a person reads it.
    """

    next_attempt: Optional[str] = document_field(RunEventKey.NEXT_ATTEMPT, default=None)
    """
    For a step that is retried: how the next attempt differs, as a person reads it.
    """

    monitor: Optional[RunMonitor] = document_field(RunEventKey.MONITOR, default=None)
    """
    For a monitor event: the monitor.
    """

    observation: Optional[MonitorObservation] = document_field(
        RunEventKey.OBSERVATION, default=None
    )
    """
    For a monitor event: what the monitor observes now.
    """

    target: Optional[str] = document_field(RunEventKey.TARGET, default=None)
    """
    The identity of the object a look-at keeps in view or a step perceived.
    """

    color: Optional[str] = document_field(RunEventKey.COLOR, default=None)
    """
    For a perceived object: the color it shows, by the protocol's identifier.
    """

    constraint: Optional[str] = document_field(RunEventKey.CONSTRAINT, default=None)
    """
    For a safety stop: the broken safety limit and how it was broken.
    """

    perception: Optional[PerceptionReport] = document_field(
        RunEventKey.PERCEPTION, default=None
    )
    """
    For a perception event: what the query asked and what came back.
    """

    def to_output_line(self) -> str:
        """
        :return: The line of a demo's output reporting the event.
        """
        return f"{RunOutputMarker.RUN_EVENT} {json.dumps(self.to_json())}"

    @classmethod
    def from_output_line(cls, line: str) -> Optional[Self]:
        """
        :param line: A line of a demo's output, without its line break.
        :return: The event it reports, or None for a line reporting none.
        """
        if not line.startswith(RunOutputMarker.RUN_EVENT):
            return None
        return cls.from_json(
            json.loads(line.removeprefix(RunOutputMarker.RUN_EVENT).strip())
        )


class SafetyStopKey(StrEnum):
    """
    The keys of a safety stop.
    """

    CONSTRAINT = "constraint"
    """
    The broken safety limit.
    """

    ACKNOWLEDGED = "acknowledged"
    """
    Whether a person acknowledged the stop.
    """


@dataclass(kw_only=True)
class SafetyStop(JsonDocument):
    """
    A demo cancelled because a motion broke a safety limit.

    No demo starts until a person acknowledged it, and then only from the plan's
    beginning.
    """

    constraint: str = document_field(SafetyStopKey.CONSTRAINT)
    """
    The broken safety limit and how it was broken, as a person reads it.
    """

    acknowledged: bool = document_field(SafetyStopKey.ACKNOWLEDGED, default=False)
    """
    Whether a person acknowledged the stop.
    """

    def to_output_line(self) -> str:
        """
        :return: The line of a demo's output reporting the stop.
        """
        return f"{RunOutputMarker.SAFETY_STOP} {self.constraint}"

    @classmethod
    def from_output(cls, output: str) -> Optional[Self]:
        """
        :param output: A demo's output.
        :return: The last stop the output reports, unacknowledged, or None.
        """
        reported = [
            line.removeprefix(RunOutputMarker.SAFETY_STOP).strip()
            for line in output.splitlines()
            if line.startswith(RunOutputMarker.SAFETY_STOP)
        ]
        if not reported:
            return None
        return cls(constraint=reported[-1])


class StopAfterMotions(StrEnum):
    """
    What a Stop that came once a run's motions were over did: it halted the arms and
    left the run to save its record and end as it would have.
    """

    HALTED = "halted"
    """
    Every arm was commanded zero velocities and reported standing still.
    """

    HALT_UNCONFIRMED = "halt_unconfirmed"
    """
    The plan builder could not confirm in time that every arm was commanded zero
    velocities and reported standing still; the robot may still move.
    """


class RunStatusKey(StrEnum):
    """
    The keys of the demo process's state.
    """

    STATE = "state"
    """
    The process's state.
    """

    MODE = "mode"
    """
    What the demo does.
    """

    RETURN_CODE = "returnCode"
    """
    The process's exit status.
    """

    LOG = "log"
    """
    The newest output.
    """

    PATH = "path"
    """
    The demo file.
    """

    SAFETY_STOP = "safetyStop"
    """
    The safety stop that cancelled the demo.
    """

    EVENTS = "events"
    """
    What happened while the demo ran.
    """

    STOP_AFTER_MOTIONS = "stopAfterMotions"
    """
    What a Stop that came once the run's motions were over did.
    """

    VALIDATION_HELD = "validationHeld"
    """
    Whether the plan builder holds a passed validation a run may start with.
    """


@dataclass(kw_only=True)
class RunStatus(JsonDocument):
    """
    The state of the demo process and its newest output.
    """

    state: RunState = document_field(RunStatusKey.STATE, default=RunState.IDLE)
    """
    The process's state.
    """

    mode: Optional[RunMode] = document_field(RunStatusKey.MODE, default=None)
    """
    What the demo does, or None before one was started.
    """

    return_code: Optional[int] = document_field(RunStatusKey.RETURN_CODE, default=None)
    """
    The process's exit status, or None while it runs.
    """

    log: str = document_field(RunStatusKey.LOG, default="")
    """
    The newest output of the process, at most
    :attr:`PlanBuilderLimits.maximum_log_bytes` of it.
    """

    path: Optional[str] = document_field(RunStatusKey.PATH, default=None)
    """
    The started demo file.
    """

    safety_stop: Optional[SafetyStop] = document_field(
        RunStatusKey.SAFETY_STOP, default=None
    )
    """
    The safety stop that cancelled the demo, or None.
    """

    events: List[RunEvent] = document_field(RunStatusKey.EVENTS, default_factory=list)
    """
    Every event the demo reported, in order; its lines are not part of :attr:`log`.
    """

    stop_after_motions: Optional[StopAfterMotions] = document_field(
        RunStatusKey.STOP_AFTER_MOTIONS, default=None
    )
    """
    What a Stop did that came once the run's motions were over, which leaves the run's
    record saving and its state as it would have been; None when no such Stop came.
    """

    validation_held: Optional[bool] = document_field(
        RunStatusKey.VALIDATION_HELD, default=None
    )
    """
    Whether the plan builder holds a passed validation a run needing one may start
    with, which it keeps in memory only, so a restart loses it; None where the status
    does not say, as the demo process's own does not.
    """


class ArmReportKey(StrEnum):
    """
    The keys of when one arm's driver last reported its joint positions.
    """

    ARM = "arm"
    """
    The arm's name.
    """

    AGE_SECONDS = "ageSeconds"
    """
    Seconds since its driver last reported.
    """

    LIVE = "live"
    """
    Whether its driver reported recently enough for the posture to show the arm.
    """


@dataclass(kw_only=True)
class ArmReport(JsonDocument):
    """
    When one arm's driver last reported its joint positions; one arm's driver can stop
    while the other's goes on.
    """

    arm: str = document_field(ArmReportKey.ARM)
    """
    The arm's name, the namespace its driver publishes its joint states in, such as
    ``left_arm``.
    """

    age_seconds: Optional[float] = document_field(
        ArmReportKey.AGE_SECONDS, default=None
    )
    """
    Seconds since its driver last reported, or None while it never did.
    """

    live: bool = document_field(ArmReportKey.LIVE, default=False)
    """
    Whether its driver reported recently enough that the posture shows where the arm
    stands; false unless the plan builder found it so.
    """


class RobotPostureKey(StrEnum):
    """
    The keys of the posture the real robot's drivers report.
    """

    JOINT_POSITIONS = "jointPositions"
    """
    The position of each joint by its name.
    """

    ARMS = "arms"
    """
    When each arm's driver last reported.
    """

    MIRRORED = "mirrored"
    """
    Whether the plan builder mirrors a real robot's posture.
    """


@dataclass(kw_only=True)
class RobotPosture(JsonDocument):
    """
    The joint positions the real robot's drivers reported last, and how long ago each
    arm's driver did.
    """

    joint_positions: Dict[str, float] = document_field(
        RobotPostureKey.JOINT_POSITIONS, default_factory=dict
    )
    """
    The position of each joint by its name; empty while nothing was reported.
    """

    arms: List[ArmReport] = document_field(RobotPostureKey.ARMS, default_factory=list)
    """
    When each arm's driver last reported; empty while the mirror reported nothing.
    """

    mirrored: bool = document_field(RobotPostureKey.MIRRORED, default=False)
    """
    Whether the plan builder mirrors a real robot's posture; false in simulation, where
    no driver reports.
    """


# %% plans naming the planner's classes


@dataclass(frozen=True)
class SavedNames:
    """
    The identifiers of what plans of earlier format versions named by the planner's
    names, as a catalog lists them: actions and robot models, saved by class name up to
    :data:`CLASS_NAME_FORMAT_VERSION`, and enumeration members and surface types, saved
    by member and class name up to :data:`ACTION_IDENTIFIER_FORMAT_VERSION`.
    """

    actions: Dict[str, str]
    """
    The action identifiers by saved name.
    """

    robots: Dict[str, str]
    """
    The robot identifiers by saved name.
    """

    members: Dict[str, Dict[str, Dict[str, str]]] = field(default_factory=dict)
    """
    The member identifiers by saved name, by action identifier and field identifier.
    """

    surface_types: Dict[str, str] = field(default_factory=dict)
    """
    The surface type identifiers by saved name.
    """

    @classmethod
    def of_catalog(cls, catalog: PlanBuilderCatalog) -> Self:
        """
        :param catalog: A plan builder catalog.
        :return: The names its actions, robots, members and surface types were saved
            under.
        """
        return cls(
            actions={
                saved: schema.name
                for schema in catalog.actions
                for saved in schema.saved_as
            },
            robots={
                saved: robot.name
                for robot in catalog.robots
                for saved in robot.saved_as
            },
            members={
                schema.name: {
                    offered.name: dict(offered.saved_members)
                    for offered in schema.fields
                    if offered.saved_members
                }
                for schema in catalog.actions
            },
            surface_types=dict(catalog.saved_surface_types),
        )

    def translated(self, plan: PlanDocument) -> PlanDocument:
        """
        :param plan: A plan.
        :return: The plan with every saved name replaced by its identifier and carrying
            :data:`PLAN_FORMAT_VERSION`; a plan of that version unchanged. A name the
            catalog does not list stays, for the catalog check to report.
        """
        if plan.format_version >= PLAN_FORMAT_VERSION:
            return plan
        if plan.format_version >= IDENTIFIER_FORMAT_VERSION:
            return replace(plan, format_version=PLAN_FORMAT_VERSION)
        if plan.format_version < ACTION_IDENTIFIER_FORMAT_VERSION:
            plan = replace(
                plan,
                robots=[
                    replace(
                        robot,
                        model=self.robots.get(robot.model, robot.model),
                        steps=[
                            replace(
                                step, action=self.actions.get(step.action, step.action)
                            )
                            for step in robot.steps
                        ],
                    )
                    for robot in plan.robots
                ],
            )
        return replace(
            plan,
            format_version=PLAN_FORMAT_VERSION,
            robots=[
                replace(
                    robot, steps=[self.translated_step(step) for step in robot.steps]
                )
                for robot in plan.robots
            ],
        )

    def translated_step(self, step: PlanStep) -> PlanStep:
        """
        :param step: A step naming its action by identifier.
        :return: The step with the saved names of its members and surface types
            replaced by their identifiers.
        """
        members = self.members.get(step.action, {})
        return replace(
            step,
            values={
                name: self.translated_value(value, members.get(name, {}))
                for name, value in step.values.items()
            },
        )

    def translated_value(self, value: Any, members: Dict[str, str]) -> Any:
        """
        :param value: A step's value.
        :param members: The member identifiers of the value's field by saved name.
        :return: A saved member name as its identifier, a destination with its surface
            type's identifier, anything else as it is.
        """
        if isinstance(value, str):
            return members.get(value, value)
        if isinstance(value, dict) and isinstance(
            value.get(DestinationKey.SURFACE_TYPE), str
        ):
            surface_type = value[DestinationKey.SURFACE_TYPE]
            return {
                **value,
                DestinationKey.SURFACE_TYPE: self.surface_types.get(
                    surface_type, surface_type
                ),
            }
        return value
