"""
A robot profile: how the plan builder is set up for one robot at one site, which robot
plans start with, the environment it stands in, the skills people compose plans from and
the plans they start from.

A deployment names its profile with :attr:`RobotProfileVariable.PROFILE`; without one
the plan builder offers every robot and action of the CRAM workspace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from typing_extensions import Any, Dict, List, Optional, Self

from cramera_protocol.document import JsonDocument, document_field
from cramera_protocol.geometry import AxisAlignedBox, FloorRectangle
from cramera_protocol.version import ProtocolVersion

PROFILE_FORMAT_VERSION = ProtocolVersion(major=1, minor=0)
"""
The version profiles are written with.
"""


class RobotProfileVariable(StrEnum):
    """
    The environment variables a deployment configures the profile with.
    """

    PROFILE = "CRAMERA_ROBOT_PROFILE"
    """
    The path of the robot profile's JSON file.
    """

    CONNECTION = "CRAMERA_ROBOT_CONNECTION"
    """
    Whether the deployment is attached to the real robot, a :class:`RobotConnection`
    value; simulated when it is not set.
    """


class RobotConnection(StrEnum):
    """
    What a deployment's robot is: the real one, or only its simulation.
    """

    SIMULATED = "simulated"
    """
    No real robot is attached: scans and validations look at the profile's simulated
    scene, and no run executes on the real robot.
    """

    REAL = "real"
    """
    The real robot is attached: scans and validations ask its own perception, a
    validation starts from the joint positions it reports, and the robot run mode
    executes on it.
    """


class ExecutorKind(StrEnum):
    """
    How a run of a plan executes its motions, which picks the plan executor the
    generated program runs the plan with.

    A profile names :attr:`REAL` for the run mode that runs on the robot.
    """

    KINEMATIC = "kinematic"
    """
    Moves the world kinematically, as fast as the controller computes the motions.
    """

    PHYSICS = "physics"
    """
    Runs the motions against a physics simulation of the world (MuJoCo), where an object
    is held by friction alone.
    """

    PERCEIVED_PHYSICS = "perceived_physics"
    """
    Runs the motions in physics as :attr:`PHYSICS` does, and has a RoboKudo pipeline
    look through the robot's simulated camera for the objects the plan detects, as the
    real robot's perception does.
    """

    REAL = "real"
    """
    Moves the real robot: Giskard commands its controllers, the plan's world follows the
    joint positions it reports, and RoboKudo looks through its camera.
    """


class CameraMount(StrEnum):
    """
    How a robot's camera is mounted, which decides how a step keeps an object in view.
    """

    FIXED = "fixed"
    """
    The camera cannot be turned, so keeping an object in view is observed.
    """

    HEAD = "head"
    """
    The camera turns with a head, so keeping an object in view is a gaze the robot
    actuates.
    """


class SkillKey(StrEnum):
    """
    The keys of a skill.
    """

    IDENTIFIER = "id"
    """
    The skill's stable identity, which plans name.
    """

    LABEL = "label"
    """
    The skill's name in the palette.
    """

    ACTION = "action"
    """
    The native action the skill carries out.
    """

    VALUES = "values"
    """
    The values the skill sets on its action's fields.
    """

    FIELDS = "fields"
    """
    The action's fields a person edits.
    """

    DOCUMENTATION = "documentation"
    """
    What the skill does, in a sentence.
    """


@dataclass(kw_only=True)
class Skill(JsonDocument):
    """
    A capability the robot offers people, carried out by a native action with some of
    its fields set.

    A plan names the skill by its identifier, so the platform can map the skill to
    another action without breaking saved plans.
    """

    identifier: str = document_field(SkillKey.IDENTIFIER)
    """
    The skill's stable identity, which a step keeps across platform versions.
    """

    label: str = document_field(SkillKey.LABEL)
    """
    The skill's name in the palette and on its steps.
    """

    action: str = document_field(SkillKey.ACTION)
    """
    The identifier of the native action that carries the skill out, as the catalog's
    action schemas name it.
    """

    values: Dict[str, Any] = document_field(SkillKey.VALUES, default_factory=dict)
    """
    The JSON values a new step of the skill starts with, by field name.
    """

    fields: Optional[List[str]] = document_field(SkillKey.FIELDS, default=None)
    """
    The names of the action's fields a person edits on a step of the skill; the others
    keep their values.

    None shows every field.
    """

    documentation: str = document_field(SkillKey.DOCUMENTATION, default="")
    """
    What the skill does, shown with its palette entry.
    """


class ZoneKey(StrEnum):
    """
    The keys of a named zone.
    """

    IDENTIFIER = "id"
    """
    The zone's stable identity, which plans name.
    """

    LABEL = "label"
    """
    The zone's name shown to people.
    """

    AREA = "area"
    """
    The rectangle the zone covers.
    """


@dataclass(kw_only=True)
class Zone(JsonDocument):
    """
    A named rectangle on the table the robot works at, such as a tray, which a step puts
    an object into or looks for objects in.
    """

    identifier: str = document_field(ZoneKey.IDENTIFIER)
    """
    The zone's stable identity, which a step keeps across platform versions.
    """

    label: str = document_field(ZoneKey.LABEL)
    """
    The zone's name in the step's choices and in the 3D view.
    """

    area: FloorRectangle = document_field(ZoneKey.AREA)
    """
    The rectangle the zone covers, in metres, in the frame of the pose a plan stands the
    robot at; it lies on the top of the robot's table.
    """


class ArmWorkspaceKey(StrEnum):
    """
    The keys of the workspace of an arm.
    """

    ARM = "arm"
    """
    The arm, as a step names it.
    """

    LABEL = "label"
    """
    The workspace's name shown to people.
    """

    BOX = "box"
    """
    The box the arm's tool stays inside.
    """


@dataclass(kw_only=True)
class ArmWorkspace(JsonDocument):
    """
    The box an arm's tool stays inside while the robot moves; leaving it stops the run.
    """

    arm: str = document_field(ArmWorkspaceKey.ARM)
    """
    The arm, by the name a step gives its side, such as ``left``.
    """

    label: str = document_field(ArmWorkspaceKey.LABEL)
    """
    The workspace's name, which a safety stop on leaving it shows.
    """

    box: AxisAlignedBox = document_field(ArmWorkspaceKey.BOX)
    """
    The box, in metres, in the frame of the pose a plan stands the robot at.
    """


class SafetyLimitsKey(StrEnum):
    """
    The keys of the safety limits of a robot.
    """

    WORKSPACES = "workspaces"
    """
    The workspace of each arm.
    """

    MAXIMUM_TOOL_VELOCITY = "maximumToolVelocity"
    """
    The speed no arm's tool may exceed.
    """

    MAXIMUM_REAL_ROBOT_TOOL_VELOCITY = "maximumRealRobotToolVelocity"
    """
    The speed no arm's tool may exceed in a run on the real robot.
    """


@dataclass(kw_only=True)
class SafetyLimits(JsonDocument):
    """
    The safety limits every motion of a robot's runs is held to; breaking one stops the
    run, which nothing resumes.

    The robot's own safety controller stays authoritative. The velocity no arm joint
    may exceed is the robot's own, set by its description.
    """

    workspaces: List[ArmWorkspace] = document_field(
        SafetyLimitsKey.WORKSPACES, default_factory=list
    )
    """
    The box each arm's tool stays inside; an arm without one is not held to a box.
    """

    maximum_tool_velocity: Optional[float] = document_field(
        SafetyLimitsKey.MAXIMUM_TOOL_VELOCITY, default=None
    )
    """
    The speed, in metres per second, no arm's tool may exceed; None sets none.
    """

    maximum_real_robot_tool_velocity: Optional[float] = document_field(
        SafetyLimitsKey.MAXIMUM_REAL_ROBOT_TOOL_VELOCITY, default=None
    )
    """
    The speed, in metres per second, no arm's tool may exceed in a run on the real
    robot; None holds it to :attr:`maximum_tool_velocity`.
    """


class PerceptionSettingsKey(StrEnum):
    """
    The keys of the settings of a robot's perception.
    """

    ANSWER_TIMEOUT = "answerTimeout"
    """
    How long the robot's perception may take to answer a query.
    """

    RUNTIME_CHECK = "runtimeCheck"
    """
    How a run treats what it perceives of a step's objects before the step moves.
    """


class RuntimeCheck(StrEnum):
    """
    How a run treats what it perceives of the objects a step expects in place, around
    where the plan was grounded with them, before the step moves. Neither touches the
    safety limits, the collision avoidance or the validation before a run.
    """

    STRICT = "strict"
    """
    An object found more than 2 cm from where the plan was grounded with it, or not
    found there, is detected again on the table and the step grounded anew where it
    lies; an object found nowhere, or a perception that does not answer, fails the
    attempt.
    """

    LENIENT = "lenient"
    """
    For first tests on a real robot: an object found anywhere around where the plan was
    grounded with it is acted on where it was found; one found elsewhere on the table
    grounds the step anew as :attr:`STRICT` does; one found nowhere, or a perception
    that does not answer, leaves the step acting where the plan was grounded with the
    object, which the run log says. A robot whose camera sees the table without turning
    does not detect the object once more before the final approach of a grasp.
    """


@dataclass(kw_only=True)
class PerceptionSettings(JsonDocument):
    """
    How a robot's perception pipeline, answering in a process of its own, is asked.
    """

    answer_timeout: float = document_field(
        PerceptionSettingsKey.ANSWER_TIMEOUT, default=30.0
    )
    """
    How long, in seconds, a scan, a validation or a run waits for the pipeline to
    answer a query before the query fails.
    """

    runtime_check: RuntimeCheck = document_field(
        PerceptionSettingsKey.RUNTIME_CHECK, default=RuntimeCheck.STRICT
    )
    """
    How a run treats what it perceives of a step's objects before the step moves.
    """


class RobotProfileKey(StrEnum):
    """
    The keys of a robot profile.
    """

    FORMAT_VERSION = "formatVersion"
    """
    The format version the profile is written in.
    """

    ROBOT = "robot"
    """
    The robot model plans start with.
    """

    LABEL = "label"
    """
    The name the site gives the robot.
    """

    ENVIRONMENT = "environment"
    """
    The environment bundle the robot stands in.
    """

    PLATFORM_VERSION = "platformVersion"
    """
    The version of the platform the profile belongs to.
    """

    SKILLS = "skills"
    """
    The skills people compose plans from.
    """

    TEMPLATES = "templates"
    """
    The files of the plans people start from.
    """

    EXECUTORS = "executors"
    """
    How each run mode the robot offers executes its plan.
    """

    SIMULATED_SCENE = "simulatedScene"
    """
    The file of the objects a simulated robot's camera sees.
    """

    ZONES = "zones"
    """
    The named zones on the robot's table.
    """

    CAMERA_MOUNT = "cameraMount"
    """
    How the robot's camera is mounted.
    """

    SAFETY = "safety"
    """
    The safety limits the robot's runs are held to.
    """

    PERCEPTION = "perception"
    """
    How the robot's perception is asked.
    """


@dataclass(kw_only=True)
class RobotProfile(JsonDocument):
    """
    The plan builder's setup for one robot.
    """

    robot: str = document_field(RobotProfileKey.ROBOT)
    """
    The identifier of the robot model a new plan starts with, as the catalog lists it.
    """

    label: str = document_field(RobotProfileKey.LABEL)
    """
    The name shown for the robot.
    """

    environment: Optional[str] = document_field(
        RobotProfileKey.ENVIRONMENT, default=None
    )
    """
    The environment bundle a new plan is set in, or None for an empty floor.
    """

    platform_version: Optional[str] = document_field(
        RobotProfileKey.PLATFORM_VERSION, default=None
    )
    """
    The version of the platform the profile belongs to, which plans saved with it
    record; None outside a platform.
    """

    skills: List[Skill] = document_field(RobotProfileKey.SKILLS, default_factory=list)
    """
    The skills the palette offers, in order; empty offers every native action.
    """

    templates: List[str] = document_field(
        RobotProfileKey.TEMPLATES, default_factory=list
    )
    """
    The plan files a new plan can start from, relative to the profile's file.
    """

    executors: Dict[str, ExecutorKind] = document_field(
        RobotProfileKey.EXECUTORS, default_factory=dict
    )
    """
    How a run of each mode the robot offers executes, by the run mode's value; empty
    offers every run mode with its default executor.
    """

    simulated_scene: Optional[str] = document_field(
        RobotProfileKey.SIMULATED_SCENE, default=None
    )
    """
    The plan file whose objects stand in the simulated scene the robot's camera is
    rendered from while no real robot perceives, relative to the profile's file; None
    asks the robot's own perception.
    """

    zones: List[Zone] = document_field(RobotProfileKey.ZONES, default_factory=list)
    """
    The named zones on the robot's table that steps put objects into or look in.
    """

    camera_mount: Optional[CameraMount] = document_field(
        RobotProfileKey.CAMERA_MOUNT, default=None
    )
    """
    How the robot's camera is mounted, which decides the constraints keeping an object
    in view it is offered; None offers every one.
    """

    safety: Optional[SafetyLimits] = document_field(
        RobotProfileKey.SAFETY, default=None
    )
    """
    The safety limits every motion of the robot's runs is held to; None holds them to
    none.
    """

    perception: PerceptionSettings = document_field(
        RobotProfileKey.PERCEPTION, default_factory=PerceptionSettings
    )
    """
    How the robot's perception is asked.
    """

    format_version: ProtocolVersion = document_field(
        RobotProfileKey.FORMAT_VERSION, default=PROFILE_FORMAT_VERSION
    )
    """
    The format version the profile is written in.
    """

    def __post_init__(self) -> None:
        self.format_version.require_readable_by(PROFILE_FORMAT_VERSION)

    def skill_named(self, identifier: str) -> Optional[Skill]:
        """
        :param identifier: A skill's identifier.
        :return: The profile's skill of that identifier, or None when it has none.
        """
        for skill in self.skills:
            if skill.identifier == identifier:
                return skill
        return None

    def zone_named(self, identifier: str) -> Optional[Zone]:
        """
        :param identifier: A zone's identifier.
        :return: The profile's zone of that identifier, or None when it has none.
        """
        for zone in self.zones:
            if zone.identifier == identifier:
                return zone
        return None

    def executor_for(
        self, mode: str, default: Optional[ExecutorKind]
    ) -> Optional[ExecutorKind]:
        """
        :param mode: The value of a run mode that performs the plan.
        :param default: How the mode executes unless the profile says otherwise.
        :return: How the profile executes a run of that mode: the executor it names,
            the default when it names none at all, or None when it names others only.
        """
        if not self.executors:
            return default
        return self.executors.get(mode)

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """
        :param path: A profile's JSON file.
        :return: The profile it holds.
        """
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))
