"""
Writing the live world into scene bundles: the ``__live__`` bundle of the current model,
and the ``__recording__`` bundle of a recorded run.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from typing_extensions import TYPE_CHECKING, Dict, List, Optional, Sequence

from cramera_bridge.documents import DocumentFile
from cramera_bridge.exceptions import EmptyRecording
from cramera_bridge.export.world_models import ObjectFiles, WorldModels
from cramera_bridge.geometry import NumericPose
from cramera_bridge.live.perception_images import bundle_file
from cramera_bridge.live.recorded_statecharts import RecordedStatecharts
from cramera_bridge.live.recording import FrameRange, FrameTiming, RecordedFrame
from cramera_bridge.live.segments import RecordedRun
from cramera_bridge.live.world_model import ObjectKey, WorldModelView
from cramera_bridge.robots import RobotRoster
from cramera_protocol.bundle.files import BundleFile
from cramera_protocol.bundle.index import ReservedSceneName
from cramera_protocol.bundle.scene import (
    DetectedEvent,
    ObjectDescription,
    PlanConstraint,
    PlanNode,
    SceneDescription,
)
from cramera_protocol.bundle.trajectory import Trajectory
from cramera_protocol.live_messages import LivePlan
from cramera_protocol.query_steps import QuerySteps
from cramera_protocol.version import CURRENT_VERSION
from semantic_digital_twin.robots.robot_parts import AbstractRobot
from semantic_digital_twin.world_description.geometry import Mesh
from semantic_digital_twin.world_description.world_entity import Body

if TYPE_CHECKING:
    from cramera_bridge.live.plan import PerformedPlan

SIGNATURE_LENGTH = 16
"""
How many hexadecimal digits of the model digest a signature keeps.
"""

FALLBACK_FRAMES_PER_SECOND = 30.0
"""
The frame rate written for a recording too short to measure one, the rate the viewer
plays a trajectory at by default.
"""

STAGING_PREFIX = "."
"""
Starts the name of a bundle directory being written, which the viewer skips.
"""

STAGING_SUFFIX = ".writing"
"""
Ends the name of a bundle directory being written.
"""

REPLACED_SUFFIX = ".replaced"
"""
The suffix of an old bundle version moved aside while the new one takes its place.
"""

LIVE_MESH_DIRECTORY = "live"
"""
The directory below ``meshes`` holding the live bundle's meshes.
"""

RECORDING_MESH_DIRECTORY = "recording"
"""
The directory below ``meshes`` holding a recording's meshes.
"""

# %% model identity


@dataclass(frozen=True)
class WorldSignature:
    """
    The identity of what a live bundle shows: every body outside the free objects with
    its parent, its connection type and how it is drawn, every free object with how it
    is drawn, and the selected robot.

    Poses do not count, nor what holds a free object.
    """

    model: WorldModelView
    """
    The world's current model.
    """

    selected_robot: Optional[AbstractRobot]
    """
    The selected robot.
    """

    def digest(self) -> str:
        """
        :return: The signature.
        """
        objects = {body.id for body in self.model.objects}
        entries = sorted(
            f"{body.id}<-{self.parent_of(body)}{self.drawing_of(body)}"
            for body in self.model.world.bodies
            if body.id not in objects
        ) + sorted(f"{body.id}{self.drawing_of(body)}" for body in self.model.objects)
        robot = (
            ""
            if self.selected_robot is None
            else RobotRoster.identifier(self.selected_robot)
        )
        text = "|".join(entries) + f"|robot:{robot}"
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:SIGNATURE_LENGTH]

    @staticmethod
    def drawing_of(body: Body) -> str:
        """
        :param body: A body of the world.
        :return: How it is drawn: the kind of each visual shape with its size, or the
            file and scale of a mesh, whose size would need the file read.
        """
        return "".join(
            (
                f"[{type(shape).__name__} {shape.filename} {shape.scale}]"
                if isinstance(shape, Mesh)
                else f"[{type(shape).__name__} {shape.local_frame_bounding_box.dimensions}]"
            )
            for shape in body.visual.shapes
        )

    @staticmethod
    def parent_of(body: Body) -> str:
        """
        :param body: A body of the world.
        :return: Its parent's identity and connection type, or ``root``.
        """
        connection = body.parent_connection
        if connection is None:
            return "root"
        return f"{connection.parent.id}:{type(connection).__name__}"


# %% bundle directories


@dataclass(frozen=True)
class BundleDirectory:
    """
    A bundle directory that is written beside its old version and swapped in when
    complete, so a reader never sees a half-written bundle.
    """

    path: Path
    """
    The bundle directory.
    """

    def staging(self) -> Path:
        """
        :return: An empty directory to write the new version into.
        """
        staging = self.path.parent / f"{STAGING_PREFIX}{self.path.name}{STAGING_SUFFIX}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        return staging

    def publish(self, staging: Path) -> None:
        """
        Move the old version aside, the new one into place, and only then delete the old
        one, so the directory never holds a partly deleted bundle.

        :param staging: The complete new version.
        """
        replaced = self.path.parent / (
            f"{STAGING_PREFIX}{self.path.name}{REPLACED_SUFFIX}"
        )
        if replaced.exists():
            shutil.rmtree(replaced)
        if self.path.exists():
            self.path.rename(replaced)
        staging.rename(self.path)
        if replaced.exists():
            shutil.rmtree(replaced)


@dataclass(frozen=True)
class PlanNodeFrames:
    """
    The frames each node of a plan ran in, read from the recorded frames: a node runs in
    every frame in which it, or one of its descendants, is the deepest running action.
    """

    plan: LivePlan
    """
    The plan.
    """

    frames: List[RecordedFrame]
    """
    The recorded frames.
    """

    def ranges(self) -> Dict[str, FrameRange]:
        """
        :return: The first and the last frame of every node that ran, by its identity.
        """
        parents = {node.identifier: node.parent for node in self.plan.nodes}
        first: Dict[str, int] = {}
        last: Dict[str, int] = {}
        for index, frame in enumerate(self.frames):
            identifier = frame.action_node
            while identifier in parents:
                first.setdefault(identifier, index)
                last[identifier] = index
                identifier = parents[identifier]
        return {
            identifier: FrameRange(first[identifier], last[identifier])
            for identifier in first
        }


@dataclass(frozen=True)
class PlanTrees:
    """
    Turns the flat live plan into the nested plan trees a bundle stores.
    """

    plan: LivePlan
    """
    The plan.
    """

    robot: Optional[str]
    """
    The display name of the robot that performed the plan, named on its roots.
    """

    frames: Dict[str, FrameRange] = field(default_factory=dict)
    """
    The frames each node ran in, by its identity; a node without an entry never ran.
    """

    def roots(self) -> List[PlanNode]:
        """
        :return: The plan's roots with their children nested.
        """
        identifiers = {node.identifier for node in self.plan.nodes}
        nodes: Dict[str, PlanNode] = {}
        for node in self.plan.nodes:
            frames = self.frames.get(node.identifier)
            nodes[node.identifier] = PlanNode(
                kind=node.kind,
                label=node.label,
                status=node.status,
                target=node.target,
                arm=node.arm,
                identifier=node.identifier,
                parent=node.parent,
                start=None if frames is None else frames.first,
                end=None if frames is None else frames.last,
                robot=None if node.parent in identifiers else self.robot,
                parameters=dict(node.parameters),
                perceived=(None if node.perceived is None else list(node.perceived)),
                reason=node.reason,
                images=(
                    None
                    if node.images is None
                    else [
                        replace(image, file=bundle_file(image.file))
                        for image in node.images
                    ]
                ),
                constraints=[
                    PlanConstraint(
                        text=constraint.text,
                        monitor=constraint.monitor,
                        observation=constraint.observation,
                    )
                    for constraint in node.constraints
                ],
            )
        roots = []
        for node in self.plan.nodes:
            if node.parent in nodes:
                nodes[node.parent].children.append(nodes[node.identifier])
            else:
                roots.append(nodes[node.identifier])
        return roots


# %% writers


@dataclass
class LiveBundleWriter:
    """
    Writes the live world's current model into a scenes directory; one writer per
    publisher, whose lock keeps two writes of the same directory apart.
    """

    scenes_directory: Path
    """
    The local scenes directory.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    """
    Serializes writes.
    """

    def live_scene(
        self, model: WorldModelView, selected_robot: Optional[AbstractRobot]
    ) -> str:
        """
        Write the ``__live__`` bundle unless the existing one shows the same model.

        :param model: The world's current model.
        :param selected_robot: The selected robot.
        :return: The bundle's name.
        """
        signature = WorldSignature(model, selected_robot).digest()
        directory = BundleDirectory(self.scenes_directory / ReservedSceneName.LIVE)
        with self.lock:
            existing = DocumentFile(directory.path / BundleFile.SCENE).read_if_present(
                SceneDescription
            )
            if existing is not None and existing.bundle_signature == signature:
                return ReservedSceneName.LIVE
            staging = directory.staging()
            models = WorldModels(model.world, staging, LIVE_MESH_DIRECTORY).write(
                model.objects, selected_robot
            )
            scene = SceneDescription(
                name=ReservedSceneName.LIVE,
                format_version=CURRENT_VERSION,
                models=models.models,
                robot=models.robot,
                objects=self.current_objects(model, staging),
                missing_assets=models.missing_assets,
                bundle_signature=signature,
                world_bound=True,
                classes=models.classes,
                annotations=models.annotations,
            )
            DocumentFile(staging / BundleFile.SCENE).write(scene)
            directory.publish(staging)
        return ReservedSceneName.LIVE

    def recording(
        self,
        model: WorldModelView,
        selected_robot: Optional[AbstractRobot],
        frames: List[RecordedFrame],
        plans: List[PerformedPlan],
        query_steps: Optional[QuerySteps] = None,
        detected_events: Sequence[DetectedEvent] = (),
    ) -> str:
        """
        Write a recorded run as the ``__recording__`` bundle.

        :param model: The world's current model.
        :param selected_robot: The selected robot.
        :param frames: The recorded frames.
        :param plans: The plans the run performed, in the order each began.
        :param query_steps: The queries the run asked, or None where it published none.
        :param detected_events: What detectors saw during the run, placed on its frames.
        :return: The bundle's name.
        :raises EmptyRecording: When there are no frames.
        """
        if not frames:
            raise EmptyRecording()
        directory = BundleDirectory(self.scenes_directory / ReservedSceneName.RECORDING)
        with self.lock:
            staging = directory.staging()
            models = WorldModels(model.world, staging, RECORDING_MESH_DIRECTORY).write(
                model.objects, selected_robot
            )
            frames_per_second = (
                FrameTiming(frames).frames_per_second() or FALLBACK_FRAMES_PER_SECOND
            )
            statecharts = RecordedStatecharts.of_frames(
                [frame.statechart for frame in frames]
            )
            scene = SceneDescription(
                name=ReservedSceneName.RECORDING,
                format_version=CURRENT_VERSION,
                frames_per_second=frames_per_second,
                trajectory=BundleFile.TRAJECTORY,
                statecharts=None if statecharts.is_empty() else BundleFile.STATECHARTS,
                query_steps=None if query_steps is None else BundleFile.QUERY_STEPS,
                models=models.models,
                robot=models.robot,
                objects=self.objects(model, frames, staging),
                segments=RecordedRun(frames).segments(),
                plan_trees=self.plan_trees(plans, frames),
                detected_events=list(detected_events),
                missing_assets=models.missing_assets,
                bundle_signature=WorldSignature(model, selected_robot).digest(),
                world_bound=True,
                classes=models.classes,
                annotations=models.annotations,
            )
            DocumentFile(staging / BundleFile.TRAJECTORY).write(
                self.trajectory(frames, frames_per_second)
            )
            self.perception_images(plans, staging)
            if not statecharts.is_empty():
                DocumentFile(staging / BundleFile.STATECHARTS).write(
                    statecharts.document()
                )
            if query_steps is not None:
                DocumentFile(staging / BundleFile.QUERY_STEPS).write(query_steps)
            DocumentFile(staging / BundleFile.SCENE).write(scene)
            directory.publish(staging)
        return ReservedSceneName.RECORDING

    @staticmethod
    def perception_images(plans: List[PerformedPlan], staging: Path) -> None:
        """
        Write the image of every perception step the plans' snapshots name.

        :param plans: The plans the run performed.
        :param staging: The bundle being written.
        """
        for plan in plans:
            for node in plan.latest.nodes:
                for image in node.images or []:
                    taken = plan.images.image(image.file)
                    if taken is None:
                        continue
                    path = staging / taken.bundle_file
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(taken.image.png)

    @staticmethod
    def plan_trees(
        plans: List[PerformedPlan], frames: List[RecordedFrame]
    ) -> List[PlanNode]:
        """
        :param plans: The plans the run performed, in the order each began.
        :param frames: The recorded frames.
        :return: The roots of every plan, each naming its robot, with the frames each
            node ran in.
        """
        return [
            root
            for plan in plans
            for root in PlanTrees(
                plan.latest,
                plan.robot,
                PlanNodeFrames(plan.latest, frames).ranges(),
            ).roots()
        ]

    @staticmethod
    def objects(
        model: WorldModelView, frames: List[RecordedFrame], output_directory: Path
    ) -> List[ObjectDescription]:
        """
        :param model: The world's current model.
        :param frames: The recorded frames.
        :param output_directory: The bundle being written.
        :return: Every recorded object still in the world, spawned where it was first
            recorded.
        """
        first_poses = {}
        for frame in frames:
            for key, pose in frame.objects.items():
                first_poses.setdefault(key, pose)
        files = ObjectFiles(output_directory)
        return [
            files.describe(body, ObjectKey.of(body), first_poses[ObjectKey.of(body)])
            for body in model.objects
            if ObjectKey.of(body) in first_poses
        ]

    @staticmethod
    def current_objects(
        model: WorldModelView, output_directory: Path
    ) -> List[ObjectDescription]:
        """
        :param model: The world's current model.
        :param output_directory: The bundle being written.
        :return: Every free object, spawned where it is now.
        """
        files = ObjectFiles(output_directory)
        return [
            files.describe(
                body,
                ObjectKey.of(body),
                NumericPose.of_entity(model.world, body).published(),
            )
            for body in model.objects
        ]

    @staticmethod
    def trajectory(frames: List[RecordedFrame], frames_per_second: float) -> Trajectory:
        """
        :param frames: The recorded frames.
        :param frames_per_second: The recording's frame rate.
        :return: The trajectory; the base track only when every frame has a robot.
        """
        bases = [frame.base for frame in frames]
        return Trajectory(
            frames=[frame.frames for frame in frames],
            frames_per_second=frames_per_second,
            frame_times=[frame.time for frame in frames],
            base=bases if all(base is not None for base in bases) else [],
            model_bases=[frame.model_bases for frame in frames],
            objects=[frame.objects for frame in frames],
        )
