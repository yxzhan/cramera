"""
Errors cramera-bridge raises.

Errors a request can cause derive from :class:`~cramera.exceptions.ViewerError`, so the
HTTP boundary answers them with their own status.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path

from typing_extensions import List, Optional

from cramera.exceptions import ViewerError

# %% base


@dataclass
class CramPublisherError(ViewerError):
    """
    A request or a world the live publisher cannot serve.
    """


@dataclass
class Conflict(CramPublisherError):
    """
    A request that is valid but cannot be carried out in the publisher's current state.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.CONFLICT


# %% world


@dataclass
class NoWorldAttached(Conflict):
    """
    A request that needs a world before one was attached.
    """

    def message(self) -> str:
        return "No world is attached to the live publisher yet."


@dataclass
class UnknownRobot(CramPublisherError):
    """
    A robot identity no robot of the world carries.
    """

    identifier: str
    """
    The identity that was asked for.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"The world has no robot with the identity {self.identifier!r}."


@dataclass
class UnknownArm(CramPublisherError):
    """
    An arm name no arm of the selected robot carries.
    """

    name: str
    """
    The name that was asked for.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"The selected robot has no arm named {self.name!r}."


@dataclass
class RobotSelectionBusy(Conflict):
    """
    A robot selection while a plan is executing with the selected robot.
    """

    def message(self) -> str:
        return "Wait for the running plan to finish before selecting another robot."


@dataclass
class UnknownObject(CramPublisherError):
    """
    An object key no published object carries.
    """

    key: str
    """
    The key that was asked for.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"No published object has the key {self.key!r}."


@dataclass
class ObjectNotMovable(Conflict):
    """
    A move of an object that is not held by a free connection.
    """

    key: str
    """
    The key of the object.
    """

    connection_type: str
    """
    The class name of the connection holding it.
    """

    def message(self) -> str:
        return (
            f"The object {self.key!r} is held by a {self.connection_type}; only "
            f"objects with six degrees of freedom can be moved."
        )


@dataclass
class UnknownJoint(CramPublisherError):
    """
    A joint name no movable connection of the world carries.
    """

    joint: str
    """
    The name that was asked for.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"The world has no movable joint named {self.joint!r}."


@dataclass
class UnknownBody(CramPublisherError):
    """
    A body name a constraint or a placement names but the world does not hold.
    """

    name: str
    """
    The name that was asked for.
    """

    def message(self) -> str:
        return f"The world has no body named {self.name!r}."


@dataclass
class AmbiguousBody(CramPublisherError):
    """
    A body name that several bodies of the world carry.
    """

    name: str
    """
    The name that was asked for.
    """

    candidates: List[str]
    """
    The full names of the bodies carrying it.
    """

    def message(self) -> str:
        return f"Several bodies are named {self.name!r}: {self.candidates}."


@dataclass
class UnknownMesh(CramPublisherError):
    """
    A mesh key the publisher does not serve.
    """

    key: str
    """
    The key that was asked for.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"No mesh is served under the key {self.key!r}."


@dataclass
class MissingMeshFile(CramPublisherError):
    """
    A mesh shape whose file does not exist.
    """

    path: Path
    """
    The file the shape names.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def message(self) -> str:
        return f"The mesh file {str(self.path)!r} does not exist."


@dataclass
class UnsupportedShape(CramPublisherError):
    """
    A shape whose geometry has no published form.
    """

    shape_type: str
    """
    The class name of the shape.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def message(self) -> str:
        return f"Shapes of type {self.shape_type} cannot be published."


# %% live


@dataclass
class TeleoperationUnavailable(Conflict):
    """
    Hand control requested while no robot is selected or a plan moves the robot.
    """

    reason: str
    """
    Why hand control is unavailable.
    """

    def message(self) -> str:
        return f"Hand control is unavailable: {self.reason}"


@dataclass
class TeleoperationStillRunning(Conflict):
    """
    A hand-control driver that did not stop within its time budget.
    """

    def message(self) -> str:
        return "The previous hand control has not stopped yet."


@dataclass
class InvalidConstraint(CramPublisherError):
    """
    A constraint whose monitor parameters cannot be read.
    """

    parameter: str
    """
    The parameter that cannot be read.
    """

    expected: str
    """
    What the parameter must be.
    """

    def message(self) -> str:
        return f"The constraint parameter {self.parameter!r} must be {self.expected}."


@dataclass
class UnknownPlanNode(CramPublisherError):
    """
    A plan node identity the executing plan does not hold.
    """

    identifier: str
    """
    The identity that was given.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"The plan has no node {self.identifier!r}."


@dataclass
class UnknownPerceptionImage(CramPublisherError):
    """
    An image key no perception step took an image under.
    """

    key: str
    """
    The key that was given.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"No perception step took an image {self.key!r}."


@dataclass
class NoResolutionExecution(Conflict):
    """
    A request to carry out a resolved plan in a process that carries out none on
    request.
    """

    def message(self) -> str:
        return "This process carries out no resolved plans on request."


@dataclass
class UnknownResolution(CramPublisherError):
    """
    A resolution identity no published resolution carries.
    """

    identifier: str
    """
    The identity that was given.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"No resolution {self.identifier!r} was published."


@dataclass
class ResolutionNotExecutable(Conflict):
    """
    A request to carry out a resolution whose plan cannot be carried out.
    """

    identifier: str
    """
    The identity of the resolution.
    """

    def message(self) -> str:
        return (
            f"The plan resolution {self.identifier!r} resolved cannot be carried out."
        )


@dataclass
class NoQueryStepping(Conflict):
    """
    A request to step through the queries of a process that cannot be held at them.
    """

    def message(self) -> str:
        return "This process cannot be held at the queries it asks."


@dataclass
class MarkersUnavailable(Conflict):
    """
    A marker topic change in a process that reads no markers from ROS.
    """

    def message(self) -> str:
        return "This process reads no markers from ROS."


@dataclass
class NoRecording(Conflict):
    """
    A recording action while no recording is in the state the action needs.
    """

    needed: str
    """
    The recording state the action needs.
    """

    def message(self) -> str:
        return f"There is no {self.needed} recording."


@dataclass
class RecordingNotWritten(Conflict):
    """
    A save of a recording that has ended but whose bundle is still being written.
    """

    def message(self) -> str:
        return "The recording is still being written; save it once it is."


@dataclass
class EmptyRecording(Conflict):
    """
    A recording without frames that was to be written as a bundle.
    """

    def message(self) -> str:
        return "The recording has no frames."


@dataclass
class InvalidFrameRange(CramPublisherError):
    """
    A frame range that runs backwards or past the recorded frames.
    """

    first: int
    """
    The first frame asked for.
    """

    last: int
    """
    The last frame asked for.
    """

    frame_count: int
    """
    How many frames the recording holds.
    """

    def message(self) -> str:
        return (
            f"The frames {self.first} to {self.last} are not within the "
            f"{self.frame_count} recorded frames."
        )


@dataclass
class SceneNameTaken(Conflict):
    """
    A save under a name a scenes directory already holds.
    """

    name: str
    """
    The name asked for.
    """

    def message(self) -> str:
        return f"A scene named {self.name!r} already exists."


@dataclass
class SharedScenesUnavailable(Conflict):
    """
    A save to the shared scenes directory while none is configured.
    """

    def message(self) -> str:
        return "No shared scenes directory is configured."


# %% export


@dataclass
class ExportError(ViewerError):
    """
    A world or a file that cannot be written into a scene bundle.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.INTERNAL_SERVER_ERROR


@dataclass
class UnrepresentableJointLimits(ExportError):
    """
    A bounded joint whose native degree of freedom lacks two finite position limits.
    """

    joint: str
    """
    The name of the connection.
    """

    def message(self) -> str:
        return f"The joint {self.joint!r} needs two finite position limits for a URDF."


@dataclass
class PathOutsideBundle(ExportError):
    """
    A file reference or destination that would leave the bundle directory.
    """

    path: str
    """
    The reference or destination.
    """

    def message(self) -> str:
        return f"{self.path!r} leads out of the bundle directory."


@dataclass
class UrdfSourceNotFound(ExportError):
    """
    A URDF or xacro source that resolves to no file.
    """

    source: str
    """
    The source as it was given.
    """

    def message(self) -> str:
        return f"The URDF source {self.source!r} does not exist."


# %% scenes


@dataclass
class SceneWorldError(ViewerError):
    """
    A scene bundle or a robot configuration a world cannot be built from.
    """


@dataclass
class BundleNotFound(SceneWorldError):
    """
    A directory that holds no scene bundle.
    """

    directory: Path
    """
    The directory that was given.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"{str(self.directory)!r} holds no scene.json."


@dataclass
class UnknownPrimLink(SceneWorldError):
    """
    An annotated prim whose link the bundle's environment does not hold.
    """

    prim: str
    """
    The USD path of the prim.
    """

    link: str
    """
    The link the prim names.
    """

    def message(self) -> str:
        return f"The prim {self.prim!r} names the missing link {self.link!r}."


@dataclass
class InvalidRobotPlacement(SceneWorldError):
    """
    A robot configuration that cannot name its robot unambiguously.
    """

    reason: str
    """
    What is wrong with the configuration.
    """

    def message(self) -> str:
        return self.reason


@dataclass
class RobotPlacementUnavailable(SceneWorldError):
    """
    A configured robot that does not resolve to exactly one robot of a world.
    """

    namespace: str
    """
    The namespace of the configured robot.
    """

    matches: int
    """
    How many robots of the world carry it.
    """

    def message(self) -> str:
        return (
            f"The robot namespace {self.namespace!r} matches {self.matches} robots; "
            f"expected one."
        )


@dataclass
class MissingParameter(CramPublisherError):
    """
    A request without a query parameter its route needs.
    """

    name: str
    """
    The missing parameter.
    """

    def message(self) -> str:
        return f"The request needs the query parameter {self.name!r}."


@dataclass
class InvalidParameter(CramPublisherError):
    """
    A query parameter whose value its route does not accept.
    """

    name: str
    """
    The parameter.
    """

    value: str
    """
    The value given.
    """

    def message(self) -> str:
        return f"{self.value!r} is no valid value of the parameter {self.name!r}."


@dataclass
class MalformedBody(CramPublisherError):
    """
    A request body that is not JSON.
    """

    reason: str
    """
    Why it cannot be read.
    """

    def message(self) -> str:
        return f"The request body is no JSON document: {self.reason}"


# %% demos


@dataclass
class NonLocalAddress(ViewerError):
    """
    A live publisher asked to listen on an address other machines reach; its query route
    runs code, so it serves this machine only.
    """

    host: str
    """
    The address asked for.
    """

    def message(self) -> str:
        return f"The live publisher listens on this machine only, not on {self.host!r}."


@dataclass
class DemoError(ViewerError):
    """
    A task demo or an onboarding run that cannot be completed.
    """


@dataclass
class TaskFailed(DemoError):
    """
    A task whose measured outcome is not what the task was to achieve.
    """

    reason: str
    """
    What the measurement showed.
    """

    def message(self) -> str:
        return f"The task failed: {self.reason}"


@dataclass
class StepAbandoned(DemoError):
    """
    A step of a plan that failed every attempt it had, or an attempt no other could fare
    better than, which aborts the plan.
    """

    step: str
    """
    The identity of the step.
    """

    attempts: int
    """
    How many attempts it made.
    """

    reason: str
    """
    Why its last attempt failed.
    """

    def message(self) -> str:
        return (
            f"{self.step} gave up after {self.attempts} attempt(s), so the plan was "
            f"aborted: {self.reason}"
        )


@dataclass
class ExecutionNotRunning(DemoError):
    """
    An executor asked of an execution whose world is not set up for it, such as a
    physical execution whose physics was not started.
    """

    kind: str
    """
    The value of the execution's executor kind.
    """

    def message(self) -> str:
        return f"The {self.kind} execution runs no world; start it before its plans."


@dataclass
class PerceptionEngineNotNamed(DemoError):
    """
    A perceiving execution started without the RoboKudo analysis engine to perceive
    with.
    """

    variable: str
    """
    The environment variable that names the analysis engine.
    """

    def message(self) -> str:
        return (
            f"A perceiving execution needs the RoboKudo analysis engine it perceives "
            f"with; set {self.variable}."
        )


@dataclass
class EnvironmentBundleHoldsRobot(DemoError):
    """
    A task demo asked to start from a bundle that holds a robot model; a task's robot
    comes from its CRAM specification, so its environment must hold none.
    """

    directory: Path
    """
    The bundle directory.
    """

    def message(self) -> str:
        return (
            f"{str(self.directory)!r} holds a robot model; a task demo starts from an "
            f"environment bundle."
        )


@dataclass
class RecordingNotSaved(DemoError):
    """
    A recording asked for before any run saved it.
    """

    name: str
    """
    The name the recording would be saved under.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"No run saved a recording named {self.name!r} yet."


@dataclass
class DemoStartedNoWorld(DemoError):
    """
    An onboarded demo that finished without starting a visualization of a world.
    """

    demo: Path
    """
    The demo file.
    """

    def message(self) -> str:
        return (
            f"{str(self.demo)!r} started no visualization; select the cramera backend "
            f"through coraplex's visualization in the demo."
        )


# %% plans


@dataclass
class PlanError(ViewerError):
    """
    A plan's step the CRAM workspace cannot turn into an action, or a workspace class or
    action it does not define.
    """


@dataclass
class SurfaceNotInWorld(PlanError):
    """
    A step putting an object on a surface the world does not hold or that offers no free
    spot.
    """

    surface_type: str
    """
    The class name of the surface annotation.
    """

    surface_name: Optional[str]
    """
    The name of the surface, or None for any surface of the class.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        surface = self.surface_type + (
            f" {self.surface_name!r}" if self.surface_name is not None else ""
        )
        return f"The world offers no free spot on a {surface}."


@dataclass
class AnnotationNotInWorld(PlanError):
    """
    A step naming an annotated entity the world does not hold.
    """

    annotation: str
    """
    The class name of the annotation.
    """

    name: str
    """
    The name the step gives.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"The world holds no {self.annotation} named {self.name!r}."


@dataclass
class ArmNotOnRobot(PlanError):
    """
    A step naming arms the robot performing it does not have as the field asks for them.
    """

    arms: str
    """
    The name of the arms the step gives.
    """

    robot: str
    """
    The class name of the robot.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"A {self.robot} has no single arm named {self.arms}."


@dataclass
class ObjectNotGraspable(PlanError):
    """
    A step naming an object to grasp that offers no grasp.
    """

    identifier: str
    """
    The object's identity.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"The object {self.identifier!r} offers no grasp."


@dataclass
class UnknownWorkspaceClass(PlanError):
    """
    A robot or surface annotation class the CRAM workspace does not define.
    """

    name: str
    """
    The class name.
    """

    offered: List[str]
    """
    The class names the workspace defines.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"The CRAM workspace defines no {self.name!r}; it defines {self.offered}."
        )


@dataclass
class RobotDescriptionUnavailable(PlanError):
    """
    A robot whose model description cannot be written: its package is not installed, it
    is no URDF or xacro file, or its xacro does not expand.
    """

    robot: str
    """
    The robot's identifier.
    """

    reason: str
    """
    Why the description cannot be written.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.NOT_FOUND

    def message(self) -> str:
        return f"The model of the robot {self.robot!r} cannot be shown: {self.reason}"


@dataclass
class UnknownAction(PlanError):
    """
    An action class name the CRAM workspace does not define.
    """

    name: str
    """
    The class name.
    """

    offered: List[str]
    """
    The class names of the actions the workspace defines.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"The CRAM workspace defines no action {self.name!r}."


# %% detections


@dataclass
class RobotHasNoTable(PlanError):
    """
    A region on the table named for a robot that is mounted on none.
    """

    robot: str
    """
    The robot's class name.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"The robot {self.robot} stands at no table to look on."


@dataclass
class DetectedObjectNotInTwin(PlanError):
    """
    A step naming the object a detection step finds, where the twin holds no object that
    detection would find.
    """

    step: str
    """
    The identity of the detection step.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"The twin holds no object the detection of {self.step} finds; scan the "
            f"scene so the plan's objects show what stands there."
        )


@dataclass
class AmbiguousDetectedObject(PlanError):
    """
    A step naming the object a detection step finds, where that detection would find
    several objects of the twin.
    """

    step: str
    """
    The identity of the detection step.
    """

    count: int
    """
    How many objects it would find.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"The detection of {self.step} finds {self.count} objects of the twin; give "
            f"it a color or type that tells them apart."
        )


@dataclass
class StepNotGrounded(PlanError):
    """
    A step of a plan to run that failed when the plan was grounded in a copy of its
    world, such as a pick no arm of the robot reaches.
    """

    step: str
    """
    The identity of the step.
    """

    failure: str
    """
    What failed it, in the planner's words.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return f"{self.step} cannot be carried out: {self.failure}"


@dataclass
class ArmOutsideItsWorkspace(PlanError):
    """
    An arm whose tool stands outside the workspace box the safety monitor holds it to
    before the plan moves anything, so the run would be stopped at its first motion.
    """

    arm: str
    """
    The arm, by the protocol's identifier.
    """

    axis: str
    """
    The axis of the plan's frame along which the tool is farthest outside the box.
    """

    position: float
    """
    Where the tool stands along :attr:`axis`, in metres in the plan's frame.
    """

    bound: float
    """
    The edge of the box along :attr:`axis` the tool stands beyond, in metres in the
    plan's frame.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        side = "lower" if self.position < self.bound else "upper"
        return (
            f"The {self.arm} arm stands outside its workspace box: its tool is "
            f"{abs(self.position - self.bound) * 1000:.1f} mm past the box's {side} "
            f"{self.axis} bound ({self.axis} = {self.position:.3f} m, the bound is at "
            f"{self.bound:.3f} m). The real robot stops any motion while a tool is "
            f"outside its box, and the platform does not move it back. "
            f"{self.recovery(f'the {self.arm} arm')}, then validate again."
        )

    @staticmethod
    def recovery(arm: str) -> str:
        """
        :param arm: The arm outside its box as a sentence names it, such as ``the left
            arm``.
        :return: What a person does about it, which is done by hand on the robot; what
            follows it, such as a new validation, is left to the sentence it ends.
        """
        return (
            f"On the real robot, move {arm} back inside its box by hand on its teach "
            f"pendant (see {RealRobotGuideSection.ARM_BACK_INTO_ITS_BOX.reference()})"
        )


class RealRobotGuideSection(StrEnum):
    """
    The sections of the platform's guide to the real Tracy (:meth:`guide_path`) that a
    message sends a person to, by their headings.
    """

    ARM_BACK_INTO_ITS_BOX = "Bringing an arm back into its box"
    """
    How to move an arm that rests outside its workspace box back inside, on the robot's
    teach pendant.
    """

    @staticmethod
    def guide_path() -> str:
        """
        :return: The guide's path, relative to the platform repository's root.
        """
        return "docs/real-tracy.md"

    def reference(self) -> str:
        """
        :return: Where a person finds the section.
        """
        return f"“{self}” in {self.guide_path()}"


@dataclass
class NoArmCarriesObject(PlanError):
    """
    A step leaving its arm to be chosen whose object no arm of the robot can both take
    and carry through the steps up to where it is put down, each inside the workspace
    the safety monitor holds the arm to and within its reach.
    """

    object_identifier: str
    """
    The identity of the object.
    """

    x: float
    """
    The object's x coordinate in the world before it is taken, in metres.
    """

    y: float
    """
    The object's y coordinate in the world before it is taken, in metres.
    """

    destination: str
    """
    Where the object is put down, as a person reads it, such as ``into left_tray``.
    """

    failures: List[str]
    """
    Why each arm failed, as a person reads it, nearer arm first.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        return (
            f"No arm can pick {self.object_identifier} at ({self.x:.2f}, {self.y:.2f}) "
            f"and put it down {self.destination} inside its workspace and reach: "
            + "; ".join(self.failures)
        )


# %% placing


@dataclass
class PlacementNotFound(PlanError):
    """
    A step putting an object down at a place that is only found when the plan is
    grounded, where no such place is found.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY


@dataclass
class UnknownZone(PlacementNotFound):
    """
    A zone the robot profile does not name.
    """

    zone: str
    """
    The zone's identifier.
    """

    def message(self) -> str:
        return f"The robot profile names no zone {self.zone}."


@dataclass
class NoFreeSpotInZone(PlacementNotFound):
    """
    A zone in which every spot the object fits is taken or out of the robot's reach.
    """

    zone: str
    """
    The zone's identifier.
    """

    object_identifier: str
    """
    The identity of the object put down.
    """

    def message(self) -> str:
        return (
            f"The zone {self.zone} has no free spot within reach for "
            f"{self.object_identifier}."
        )


@dataclass
class PlaceBeforePick(PlanError):
    """
    A step putting down an object that no arm holds when the step starts, since no step
    before it picks the object up.
    """

    object_identifier: str
    """
    The identity of the object to put down.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        shown = self.object_identifier.replace("_", " ")
        return (
            f"No arm holds the {shown} when this step starts: put a Pick of the "
            f"{shown} before it."
        )


@dataclass
class PickOfHeldObject(PlanError):
    """
    A step picking up an object an arm already holds when the step starts, since a step
    before it picked the object up and no step put it down since.
    """

    object_identifier: str
    """
    The identity of the object to pick up.
    """

    held_since: Optional[int] = None
    """
    The number of the step that picked the object up, counted from one as the page
    numbers it; None when that step was not grounded together with this one.
    """

    def status(self) -> HTTPStatus:
        return HTTPStatus.UNPROCESSABLE_ENTITY

    def message(self) -> str:
        shown = self.object_identifier.replace("_", " ")
        since = "" if self.held_since is None else f" since step {self.held_since}"
        return (
            f"The {shown} is already held{since}: place it before picking it up again."
        )


@dataclass
class PlaceTaken(PlacementNotFound):
    """
    A pose to put an object down at where another object of the plan stands, or so
    near that the gripper cannot let go beside it.
    """

    x: float
    """
    The pose's x coordinate in the world, in metres.
    """

    y: float
    """
    The pose's y coordinate in the world, in metres.
    """

    object_identifier: str
    """
    The identity of the object put down.
    """

    taken_by: str
    """
    The identity of the object standing there.
    """

    def message(self) -> str:
        return (
            f"The place at ({self.x:.2f}, {self.y:.2f}) for {self.object_identifier} is "
            f"taken: {self.taken_by} stands there. Move the place or {self.taken_by}."
        )


@dataclass
class UnknownSupportingObject(PlacementNotFound):
    """
    An object to put another one on top of that the plan does not hold.
    """

    object_identifier: str
    """
    The identity or reference the step names.
    """

    def message(self) -> str:
        return f"The plan holds no object {self.object_identifier} to put anything on."


@dataclass
class JointStatesNotReceived(DemoError):
    """
    The real robot's drivers sent no joint positions on topics they publish them on.
    """

    topics: List[str]
    """
    The topics that stayed silent.
    """

    def message(self) -> str:
        return (
            f"The real robot reported no joint positions on {', '.join(self.topics)}; "
            f"check that its drivers run."
        )


@dataclass
class RealRobotNotReady(DemoError):
    """
    A real run that would miss what the robot's drivers have to provide, found by the
    preflight before Giskard starts.
    """

    refusal: str
    """
    What the preflight missed and what to do, as a person reads it.
    """

    def message(self) -> str:
        return self.refusal


@dataclass
class RealRunTerminated(DemoError):
    """
    A run on the real robot asked to end, by SIGTERM, before it finished; the robot was
    halted and Giskard ended as the run unwound.
    """

    def message(self) -> str:
        return "The run on the real robot was asked to end; the robot was halted."
