"""
A plan's steps as the native coraplex actions they name: each value converted by the
kind and type of its field, an arm into the robot's part, an object into its body, its
annotation or the grasp of it closest to the robot, a gripper and its state into the
gripper's configuration, a region into its box, a kind of object into the annotation its
objects carry, the constraints' switches turned on, and the action constructed with
keyword arguments.

A step leaving its arm to be chosen becomes a statement grounded to the arm that
reaches.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import Any, Callable, Dict, Iterable, List, Optional

from coraplex.querying.grasp_closeness import GraspsByCloseness
from coraplex.robot_plans.actions.base import Action
from cramph.composites import StatementNode
from cramera_bridge.names import ENUM_NAMES
from cramera_bridge.performing import TaskContext
from cramera_bridge.plans.actions import (
    DiscoveredAction,
    DiscoveredActions,
    GripperConfigurationPart,
)
from cramera_bridge.plans.arms import ArmField, Arms, RobotArms
from cramera_bridge.plans.object_types import ObjectType
from cramera_bridge.plans.placing import PlacingTargets
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.site import RobotSite
from cramera_bridge.plans.surfaces import SurfaceTargets
from cramera_bridge.plans.world import OBJECT_TYPE_ANNOTATIONS, PlannedGraspable
from cramera_bridge.exceptions import (
    AnnotationNotInWorld,
    ObjectNotGraspable,
    PlaceTaken,
    PlacementNotFound,
)
from cramera_bridge.geometry import NumericPose
from cramera_protocol.detection import DetectionRegion
from cramera_protocol.geometry import Pose as PlannedPose
from cramera_protocol.plan_builder import (
    ActionField,
    ActionFieldKind,
    Destination,
    PlacementMode,
    PlanStep,
)
from krrood.entity_query_language.factories import a
from semantic_digital_twin.grasping.grasp_candidates import GraspCandidate
from semantic_digital_twin.semantic_annotations.mixins import HasGraspCandidates
from semantic_digital_twin.world_description.geometry import VolumetricBoundingBox
from krrood_spatial_math.spatial_types import Pose
from semantic_digital_twin.world_description.world_entity import (
    Body,
    SemanticAnnotation,
)

# %% one step


@dataclass(frozen=True)
class StepArguments:
    """
    The keyword arguments of one step's action, converted from the step's values.
    """

    context: TaskContext
    """
    The world and the robot that performs the step.
    """

    bodies: Dict[str, Body]
    """
    The plan's objects by their identity.
    """

    action: DiscoveredAction
    """
    The step's action with its schema and field types.
    """

    step: PlanStep
    """
    The step.
    """

    site: RobotSite = field(default_factory=RobotSite)
    """
    Where the plan stands the robot, and the robot profile's zones.
    """

    def arguments(self) -> Dict[str, Any]:
        """
        :return: A value for every field the step gives, a gripper configuration for
            the gripper and state it names, and True for every switch its constraints
            turn on; fields the step leaves out keep the action's defaults.
        """
        converters: Dict[ActionFieldKind, Callable[[ActionField, Any], Any]] = {
            ActionFieldKind.BODY: self.body,
            ActionFieldKind.ANNOTATION: self.annotation,
            ActionFieldKind.ENUM: self.member,
            ActionFieldKind.POSE: self.pose,
            ActionFieldKind.POSE_OR_SURFACE: self.poses,
            ActionFieldKind.NUMBER: self.plain,
            ActionFieldKind.INTEGER: self.plain,
            ActionFieldKind.BOOLEAN: self.plain,
            ActionFieldKind.TEXT: self.plain,
            ActionFieldKind.REGION: self.region,
        }
        schema = self.action.schema
        parts = self.action.configuration_parts()
        converted = {
            offered.name: converters[offered.kind](
                offered, self.step.values[offered.name]
            )
            for offered in schema.fields
            if offered.name in self.step.values
        }
        arguments = {
            self.action.field_name(identifier): value
            for identifier, value in converted.items()
            if identifier not in parts
        }
        for name, configured in self.action.configurations.items():
            if all(identifier in converted for identifier in configured.values()):
                arguments[name] = converted[
                    configured[GripperConfigurationPart.GRIPPER]
                ].default_configuration(
                    converted[configured[GripperConfigurationPart.MOTION]]
                )
        for constraint in self.step.constraints:
            if constraint.switch is not None and self.is_switch(constraint.switch):
                arguments[self.action.field_name(constraint.switch)] = True
        return arguments

    def find_open_arm_field(self) -> Optional[str]:
        """
        :return: The identifier of the field taking one arm, or one arm's end effector,
            whose arm the step leaves to be chosen; None when it chooses every arm
            itself.
        """
        for offered in self.action.schema.fields:
            field_type = ArmField(self.action.field_types.get(offered.name))
            if (
                offered.name in self.step.values
                and field_type.part() is not None
                and not field_type.takes_several()
                and ENUM_NAMES.member(Arms, self.step.values[offered.name])
                is Arms.AUTOMATIC
            ):
                return offered.name
        return None

    def acted_on(self) -> Optional[Body]:
        """
        :return: The first object of the plan the step names, or None.
        """
        return next(
            (
                self.bodies[self.step.values[offered.name]]
                for offered in self.action.schema.fields
                if offered.kind is ActionFieldKind.BODY
                and self.step.values.get(offered.name) in self.bodies
            ),
            None,
        )

    def is_switch(self, name: str) -> bool:
        """
        :param name: A field name a constraint turns on.
        :return: Whether the action has a boolean field of that name.
        """
        offered = self.action.schema.field_named(name)
        return offered is not None and offered.kind is ActionFieldKind.BOOLEAN

    def body(
        self, offered: ActionField, value: str
    ) -> Body | HasGraspCandidates | GraspCandidate:
        """
        :param offered: A field taking an object of the plan.
        :param value: An object's identity.
        :return: The object as the field's type takes it: its body, its annotation, or
            the grasp of it closest to the robot.
        """
        body = self.bodies[value]
        field_type = self.action.field_types[offered.name]
        if field_type is GraspCandidate:
            return self.grasp(value, PlannedGraspable.of(body))
        if field_type is HasGraspCandidates:
            return PlannedGraspable.of(body)
        return body

    def annotation(self, offered: ActionField, value: str) -> SemanticAnnotation:
        """
        :param offered: An annotation field.
        :param value: The name of an annotation of the field's class.
        :return: The world's annotation of that class and name.
        :raises AnnotationNotInWorld: When the world has none.
        """
        annotation_class = self.action.field_types[offered.name]
        for candidate in self.context.world.get_semantic_annotations_by_type(
            annotation_class
        ):
            if str(candidate.name) == value:
                return candidate
        raise AnnotationNotInWorld(offered.annotation, value)

    def member(self, offered: ActionField, value: str) -> Any:
        """
        :param offered: An enumeration field, or one taking the robot's arms.
        :param value: A member's identifier, or the name a plan saved it under before.
        :return: The member, the robot's parts the arms it names stand for, or the
            annotation the objects of the kind it names carry.
        """
        field_type = self.action.field_types[offered.name]
        if ArmField(field_type).part() is not None:
            arms = ENUM_NAMES.member(Arms, value)
            return RobotArms(self.context.robot, near=self.acted_on()).value(
                arms, field_type
            )
        if field_type is ObjectType:
            return OBJECT_TYPE_ANNOTATIONS[ENUM_NAMES.member(ObjectType, value)]
        return ENUM_NAMES.member(field_type, value)

    def region(self, offered: ActionField, value: Any) -> VolumetricBoundingBox:
        """
        :param offered: A region field.
        :param value: A :class:`DetectionRegion`.
        :return: The box the region spans around the robot that performs the step.
        """
        return RegionBoxes(self.context.robot, site=self.site).box(
            DetectionRegion.from_json(value)
        )

    def pose(self, offered: ActionField, value: Any) -> Pose:
        """
        :param offered: A pose field.
        :param value: A pose, or a destination when the field accepts a surface.
        :return: The pose, or the best rated spot of the surface now.
        """
        if not offered.accepts_surface:
            return self.native(PlannedPose.from_json(value))
        destination = Destination.from_json(value)
        if destination.placement is PlacementMode.POSE:
            return self.native(destination.pose)
        if destination.is_grounded_late:
            return self.placing().pose(destination)
        return self.surface(destination).first()

    def poses(self, offered: ActionField, value: Any) -> Pose | Iterable[Pose]:
        """
        :param offered: A field taking a pose or poses to try.
        :param value: A destination.
        :return: The pose, or the spots of the surface, sampled when the step runs.
        """
        destination = Destination.from_json(value)
        if destination.placement is PlacementMode.POSE:
            return self.native(destination.pose)
        if destination.is_grounded_late:
            return self.placing().pose(destination)
        return self.surface(destination)

    @staticmethod
    def plain(offered: ActionField, value: Any) -> Any:
        """
        :param offered: A number, integer, boolean or text field.
        :param value: Its value.
        :return: The value, which JSON already holds as the native type.
        """
        return value

    def surface(self, destination: Destination) -> SurfaceTargets:
        """
        :param destination: A destination on a surface.
        :return: The spots of the surface for the step's object.
        """
        return SurfaceTargets(
            world=self.context.world,
            body=self.bodies[self.object_identifier()],
            surface_type=destination.surface_type,
            surface_name=destination.surface_name,
        )

    def placing(self) -> PlacingTargets:
        """
        :return: Where the step's object goes in the world as it is now.
        """
        return PlacingTargets(
            world=self.context.world,
            robot=self.context.robot,
            site=self.site,
            bodies=self.bodies,
            object_identifier=self.object_identifier(),
        )

    def late_destination_fields(self) -> List[ActionField]:
        """
        :return: The step's fields accepting a surface whose destination is only known
            once the plan is grounded: in a zone, on top of an object or at a marker.
        """
        return [
            offered
            for offered in self.action.schema.fields
            if offered.accepts_surface
            and offered.name in self.step.values
            and Destination.from_json(self.step.values[offered.name]).is_grounded_late
        ]

    def objects_expected_in_place(self) -> List[str]:
        """
        :return: The identities of the objects the step expects where the steps before
            it left them: those it acts on, unless it puts its object down, which it
            holds then, and the object it puts it on top of.
        """
        destinations = [
            Destination.from_json(self.step.values[offered.name])
            for offered in self.action.schema.fields
            if offered.accepts_surface and offered.name in self.step.values
        ]
        if not destinations:
            return [
                self.step.values[offered.name]
                for offered in self.action.schema.fields
                if offered.kind is ActionFieldKind.BODY
                and offered.name in self.step.values
            ]
        return [
            destination.object
            for destination in destinations
            if destination.placement is PlacementMode.ON_TOP_OF
        ]

    def resolved_destinations(self) -> Dict[str, Any]:
        """
        :return: The value of each field whose destination is only known once the plan
            is grounded, as the pose it resolves to in the world as it is now.
        :raises PlacementNotFound: When a destination resolves to no pose, which
            :meth:`placement_failure` tells beforehand.
        """
        resolved = {}
        for offered in self.late_destination_fields():
            pose = self.placing().pose(
                Destination.from_json(self.step.values[offered.name])
            )
            resolved[offered.name] = Destination(
                placement=PlacementMode.POSE,
                pose=NumericPose.of_matrix(pose.to_np()).published(),
            ).to_json()
        return resolved

    def placement_failure(self) -> Optional[PlacementNotFound]:
        """
        :return: Why the first field whose destination is only known once the plan is
            grounded resolves to no pose in the world as it is now, or why the pose a
            field names is taken by another object; None when each destination resolves
            to a free pose.
        """
        placing = self.placing()
        for offered in self.late_destination_fields():
            destination = Destination.from_json(self.step.values[offered.name])
            if placing.find_pose(destination) is None:
                return placing.not_found(destination)
        for offered in self.pose_destination_fields():
            pose = Destination.from_json(self.step.values[offered.name]).pose
            taker = placing.find_taker(
                Pose.from_xyz_quaternion(
                    *pose.to_json(), reference_frame=self.context.world.root
                )
            )
            if taker is not None:
                return PlaceTaken(pose.x, pose.y, placing.object_identifier, taker)
        return None

    def pose_destination_fields(self) -> List[ActionField]:
        """
        :return: The step's fields accepting a surface whose destination is a pose the
            plan names.
        """
        return [
            offered
            for offered in self.action.schema.fields
            if offered.accepts_surface
            and offered.name in self.step.values
            and Destination.from_json(self.step.values[offered.name]).placement
            is PlacementMode.POSE
        ]

    def object_identifier(self) -> Optional[str]:
        """
        :return: The identity of the object the step acts on: the value of its action's
            first body field.
        """
        for offered in self.action.schema.fields:
            if offered.kind is ActionFieldKind.BODY:
                return self.step.values.get(offered.name)
        return None

    def grasp(self, identifier: str, graspable: HasGraspCandidates) -> GraspCandidate:
        """
        :param identifier: The object's identity.
        :param graspable: Its annotation.
        :return: The grasp of the object closest to where the robot stands, approached
            most directly from the robot's side.
        :raises ObjectNotGraspable: When the object offers no grasp.
        """
        closest = GraspsByCloseness(
            graspable, self.context.robot.root.global_pose, number_of_grasps=1
        )()
        if not closest:
            raise ObjectNotGraspable(identifier)
        return closest[0]

    def native(self, planned: PlannedPose) -> Pose:
        """
        :param planned: A pose of the plan, in world coordinates.
        :return: The native pose in the world's root frame.
        """
        return Pose.from_xyz_quaternion(
            *planned.to_json(), reference_frame=self.context.world.root
        )


# %% actions


@dataclass(frozen=True)
class StepActions:
    """
    Builds the native action of each step against the world and robot of a context.
    """

    context: TaskContext
    """
    The world and the robot that performs the steps.
    """

    bodies: Dict[str, Body]
    """
    The plan's objects by their identity.
    """

    actions: DiscoveredActions
    """
    The workspace's actions, which steps name by class name.
    """

    site: RobotSite = field(default_factory=RobotSite)
    """
    Where the plan stands the robot, and the robot profile's zones.
    """

    def action(self, step: PlanStep) -> Action | StatementNode:
        """
        :param step: A step the plan builder checked against its action's schema.
        :return: The native action the step stands for; for a step leaving its arm to
            be chosen, the statement of its action with an arm of the robot open.
        :raises UnknownAction: When the workspace has no action of the step's name.
        """
        discovered = self.actions.named(step.action)
        step_arguments = StepArguments(
            context=self.context,
            bodies=self.bodies,
            action=discovered,
            step=step,
            site=self.site,
        )
        arguments = step_arguments.arguments()
        if step_arguments.find_open_arm_field() is not None:
            return StatementNode(statement=a(discovered.action_class)(**arguments))
        return discovered.action_class(**arguments)
