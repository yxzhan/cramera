"""
The robot profile a deployment configures: the profile itself, the plan templates its
file names, and how the profile's skills keep saved plans current.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from typing_extensions import List, Mapping, Optional

from cramera_plan_builder.exceptions import RunModeNotOffered
from cramera_protocol.plan_builder import (
    DemoDocument,
    PlanDocument,
    PlannedObject,
    PlanStep,
    PlanTemplate,
    RunMode,
)
from cramera_protocol.robot_profile import (
    CameraMount,
    ExecutorKind,
    RobotConnection,
    RobotProfile,
    RobotProfileVariable,
)
from cramera_protocol.validation import ValidationOrder

# %% configured profile


@dataclass(frozen=True)
class ConfiguredProfile:
    """
    The robot profile a deployment names, with the templates its file refers to.
    """

    profile: Optional[RobotProfile] = None
    """
    The profile, or None when the deployment names none.
    """

    templates: List[PlanTemplate] = field(default_factory=list)
    """
    The plans the profile offers to start from, in the profile's order.
    """

    simulated_objects: Optional[List[PlannedObject]] = None
    """
    The objects of the simulated scene the robot's camera sees, or None when the robot's
    own perception sees the scene.
    """

    connection: RobotConnection = RobotConnection.SIMULATED
    """
    Whether the deployment is attached to the real robot.
    """

    unprofiled_label: str = "This deployment"
    """
    How a deployment without a profile is named where it offers no run.
    """

    @classmethod
    def of_environment(cls, environment: Mapping[str, str]) -> ConfiguredProfile:
        """
        :param environment: The process environment, which may name the profile's file
            and whether the real robot is attached.
        :return: The profile it names with its templates, or no profile.
        """
        path = environment.get(RobotProfileVariable.PROFILE)
        connection = RobotConnection(
            environment.get(RobotProfileVariable.CONNECTION, RobotConnection.SIMULATED)
        )
        return cls.of_file(Path(path).expanduser(), connection) if path else cls()

    @classmethod
    def of_file(
        cls, path: Path, connection: RobotConnection = RobotConnection.SIMULATED
    ) -> ConfiguredProfile:
        """
        :param path: A profile's JSON file.
        :param connection: Whether the deployment is attached to the real robot.
        :return: The profile with the templates and the simulated scene it names, read
            relative to the file; attached to the real robot, the robot perceives the
            scene itself, else no run mode executes on the real robot.
        """
        profile = RobotProfile.from_file(path)
        if connection is RobotConnection.SIMULATED:
            profile = replace(
                profile,
                executors={
                    mode: executor
                    for mode, executor in profile.executors.items()
                    if executor is not ExecutorKind.REAL
                },
            )
        return cls(
            profile=profile,
            templates=[
                PlanTemplate(
                    identifier=Path(template).stem,
                    plan=cls.plan_file(path.parent / template),
                )
                for template in profile.templates
            ],
            simulated_objects=(
                None
                if profile.simulated_scene is None or connection is RobotConnection.REAL
                else cls.plan_file(path.parent / profile.simulated_scene).objects
            ),
            connection=connection,
        )

    def validation_order(self, demo: DemoDocument) -> ValidationOrder:
        """
        :param demo: A checked plan and its environment.
        :return: Its validation in the scene the robot's camera sees, starting where the
            real robot stands when the deployment is attached to it.
        """
        return ValidationOrder(
            demo=demo,
            simulated_objects=self.simulated_objects,
            reads_robot_posture=self.connection is RobotConnection.REAL,
        )

    def camera_mount(self) -> Optional[CameraMount]:
        """
        :return: How the profile's robot has its camera mounted; None without a
            profile or when it does not say.
        """
        return None if self.profile is None else self.profile.camera_mount

    @staticmethod
    def plan_file(path: Path) -> PlanDocument:
        """
        :param path: A plan's JSON file.
        :return: The plan it holds.
        """
        return PlanDocument.from_json(json.loads(path.read_text("utf-8")))

    def executor_for(self, mode: RunMode) -> Optional[ExecutorKind]:
        """
        :param mode: How a plan is to be run.
        :return: How a run of the mode executes the plan: as the profile says, else
            the mode's default; None for a mode that performs nothing.
        :raises RunModeNotOffered: When neither the profile nor the mode's default
            names an executor for a mode that performs the plan.
        """
        if not mode.performs_plan:
            return None
        executor = (
            mode.default_executor
            if self.profile is None
            else self.profile.executor_for(mode, mode.default_executor)
        )
        if executor is None:
            raise RunModeNotOffered(
                mode.value,
                self.unprofiled_label if self.profile is None else self.profile.label,
            )
        return executor

    def moves_real_robot(self, mode: RunMode) -> bool:
        """
        :param mode: How a plan is to be run.
        :return: Whether a run of the mode moves the real robot, as the profile says.
        """
        return (
            self.profile is not None
            and self.profile.executor_for(mode, mode.default_executor)
            is ExecutorKind.REAL
        )

    def find_unoffered_steps(self, plan: PlanDocument) -> List[PlanStep]:
        """
        :param plan: A plan.
        :return: The performing robot's steps naming a skill the profile does not offer;
            none without a profile.
        """
        robot = plan.performing_robot()
        if self.profile is None or robot is None:
            return []
        return [
            step
            for step in robot.steps
            if step.skill is not None and self.profile.skill_named(step.skill) is None
        ]

    def resolved(self, plan: PlanDocument) -> PlanDocument:
        """
        :param plan: A plan whose steps may name skills of the profile.
        :return: The plan with every step of a known skill naming that skill's current
            action, and stamped with the profile's platform version; a plan unchanged
            without a profile.
        """
        if self.profile is None:
            return plan
        return replace(
            plan,
            platform_version=self.profile.platform_version,
            robots=[
                replace(robot, steps=[self.resolved_step(step) for step in robot.steps])
                for robot in plan.robots
            ],
        )

    def resolved_step(self, step: PlanStep) -> PlanStep:
        """
        :param step: A step of a plan.
        :return: The step naming the action its skill maps to now; a step without a
            skill, or of a skill the profile no longer has, unchanged.
        """
        skill = None if step.skill is None else self.profile.skill_named(step.skill)
        if skill is None:
            return step
        return replace(step, action=skill.action)
