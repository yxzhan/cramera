"""
Writing a perception report to the run log for every detection a run's plans ran: what
the detection asked, every object RoboKudo reported for it and which one the step took.

RoboKudo's answers are read from its query action (see
:mod:`~cramera_bridge.plans.robokudo_answers`), since on the real robot the detections
ask from inside Giskard's process. A plan's detections run one after another and each
asks one query, so the n-th detection that ran in a plan asked the n-th query RoboKudo
accepted while the plan ran.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from typing_extensions import Dict, List, Optional

from coraplex.perception import Detection, PerceptionTask
from coraplex.plans.expected_positions import BodiesWhereExpected, ExpectedPosition
from cramera_bridge.plans.perception_reports import (
    PerceptionAnswer,
    PerceptionSearch,
    ReportedDesignator,
)
from cramera_bridge.plans.regions import RegionBoxes
from cramera_bridge.plans.robokudo_answers import QueryAnswer, RoboKudoAnswers
from cramera_bridge.plans.run_log import RunLog, RunLogEntry
from cramera_bridge.plans.step_run import AttemptProgress, StepRun
from cramera_protocol.detection import DetectionRegion
from cramera_protocol.geometry import Bounds, Vector3
from cramera_protocol.plan_builder import (
    PerceptionReport,
    RunEvent,
    RunEventKind,
    SurfaceDepth,
)
from cramph.data_types import LifeCycleValues
from cramph.executor import Executor, ExecutorExtension
from cramph.node import StatechartNode
from krrood_spatial_math.spatial_types import (
    HomogeneousTransformationMatrix,
    Point3,
    Pose,
)
from robokudo_msgs.msg import ObjectDesignator
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.geometry import VolumetricBoundingBox
from semantic_digital_twin.world_description.world_entity import Body

ANSWER_WAIT_SECONDS = 2.0
"""
How long, after a plan ended, its detections' answers are waited for; RoboKudo
published them as it answered, before the plan could go on.
"""

SAME_BOX_METRES = 0.001
"""
How close the corners of two boxes lie when they are the same box.
"""

SAME_SPOT_METRES = 0.001
"""
How close two positions of an object lie when they are the same spot.
"""

# %% the run log entry


@dataclass(frozen=True)
class PerceptionReported(RunLogEntry):
    """
    A detection of a step was answered, or went unanswered.
    """

    report: PerceptionReport
    """
    What it asked and what came back.
    """

    step: Optional[str] = None
    """
    The identity of the step it belongs to, None when no step is known.
    """

    def line(self) -> str:
        reported = len(self.report.objects)
        objects = f" ({reported} object{'s' if reported != 1 else ''} reported)"
        prefix = "" if self.step is None else f"{self.step}: "
        return f"{prefix}{self.report.summary}{objects if self.report.answered else ''}"

    def event(self) -> RunEvent:
        return RunEvent(
            kind=RunEventKind.PERCEPTION, step=self.step, perception=self.report
        )


# %% reporting


@dataclass
class PerceptionReporting(ExecutorExtension):
    """
    Reports every detection of a plan that ran, once the plan ended.
    """

    answers: RoboKudoAnswers
    """
    RoboKudo's answers to every query.
    """

    step_run: StepRun
    """
    The run of the steps, whose objects and sites the detections look for.
    """

    run_log: RunLog
    """
    Where the reports are written.
    """

    started: float = field(default=0.0, init=False)
    """
    When the plan reported last started, in seconds since the epoch.
    """

    last_step: Optional[str] = field(default=None, init=False)
    """
    The step reported last, which a detection outside the steps' plans, such as the
    table-wide search after a step lost its object, belongs to.
    """

    boxes: Dict[PerceptionTask, Bounds] = field(default_factory=dict, init=False)
    """
    The box each detection of the plan looks in, as it lay when the plan was compiled,
    by the detection: a box around an object, as a grasp's detection looks in, moves
    with the object, which the plan moves on.
    """

    validated: Dict[str, Dict[str, List[float]]] = field(init=False)
    """
    Where each step expected its objects when the run started, as the plan was validated,
    by the identity of the step and of the object; a step planned again since may expect
    them where they were last seen instead.
    """

    def __post_init__(self) -> None:
        self.validated = {
            scheduled.step.identifier: dict(scheduled.expected_positions)
            for scheduled in self.step_run.steps
        }

    def after_compile(self, executor: Executor) -> None:
        """
        Note when the plan started and where its detections look.
        """
        self.started = time.time()
        self.boxes = {
            node: self.bounds(node.query.region, node.query.world)
            for node in executor.statechart.nodes
            if isinstance(node, PerceptionTask)
        }

    def after_run(self, executor: Executor) -> None:
        """
        Write a report for every detection the plan ran.
        """
        tasks = [
            node
            for node in executor.statechart.nodes
            if isinstance(node, PerceptionTask)
            and node.life_cycle_state is not LifeCycleValues.NOT_STARTED
        ]
        if not tasks:
            return
        answers = self.answers.since(self.started, len(tasks), ANSWER_WAIT_SECONDS)
        progress = next(
            (
                extension
                for extension in executor.extensions
                if isinstance(extension, AttemptProgress)
            ),
            None,
        )
        for place, task in enumerate(tasks):
            answer = answers[place] if place < len(answers) else None
            step = self.step_of(task, progress)
            self.last_step = step
            self.run_log.write(
                PerceptionReported(
                    report=self.search(task, step).report(self.answer(answer, task)),
                    step=step,
                )
            )

    def step_of(
        self, task: PerceptionTask, progress: Optional[AttemptProgress]
    ) -> Optional[str]:
        """
        :param task: A detection that ran.
        :param progress: The progress of the attempt whose plan ran, None for another
            plan.
        :return: The step whose node holds the detection, else the step reported last.
        """
        if progress is not None:
            ancestors = self.ancestors(task)
            for attempted in progress.steps:
                if any(node is attempted.node for node in ancestors):
                    return attempted.step
        return self.last_step

    @staticmethod
    def ancestors(node: StatechartNode) -> List[StatechartNode]:
        """
        :return: The node and every node above it.
        """
        chain = []
        while node is not None:
            chain.append(node)
            node = node.parent_node
        return chain

    # %% what was asked

    def search(self, task: PerceptionTask, step: Optional[str]) -> PerceptionSearch:
        """
        :param task: A detection that ran.
        :param step: The identity of the step it belongs to, None when none is known.
        :return: What it asked: where the step expects the object, when the step checks
            that the object lies there, with how far from there it may lie, and where
            the plan was validated with it when the step expects it elsewhere since.
        """
        query = task.query
        box = self.boxes.get(task) or self.bounds(query.region, query.world)
        expected = self.expected(task, query.region)
        grounded = (
            None
            if expected is None
            else self.in_root(expected[1].position, query.world)
        )
        return PerceptionSearch(
            type=query.semantic_annotation.__name__.lower(),
            box=box,
            target=(
                None if expected is None else self.target_of(expected[1], query.world)
            ),
            color=None if query.color is None else query.color.value,
            table_wide=self.is_table(box, query.world),
            grounded=grounded,
            validated=(
                None
                if expected is None
                else self.validated_elsewhere(step, expected[1].body, grounded)
            ),
            tolerance=None if expected is None else expected[0].tolerance,
        )

    def validated_elsewhere(
        self, step: Optional[str], body: Body, grounded: Vector3
    ) -> Optional[Vector3]:
        """
        :param step: The identity of the step whose detection looks for the body.
        :param body: One of the plan's objects.
        :param grounded: Where the step expects it now, in the world's root.
        :return: Where the step expected it when the run started, as the plan was
            validated, when that is elsewhere; None when it is the same spot or the
            step expected it nowhere then.
        """
        validated = next(
            (
                Vector3(*position)
                for name, position in self.validated.get(step, {}).items()
                if self.step_run.bodies.get(name) is body
            ),
            None,
        )
        if validated is None or (
            abs(validated.x - grounded.x) < SAME_SPOT_METRES
            and abs(validated.y - grounded.y) < SAME_SPOT_METRES
            and abs(validated.z - grounded.z) < SAME_SPOT_METRES
        ):
            return None
        return validated

    @staticmethod
    def expected(
        task: PerceptionTask, region: VolumetricBoundingBox
    ) -> Optional[tuple[BodiesWhereExpected, ExpectedPosition]]:
        """
        :param task: A detection that ran.
        :param region: The box it looked in.
        :return: The check after the detection that the object lies where the plan was
            validated, and the position it expects inside the box; None when the
            detection is not followed by such a check.
        """
        detection = task.parent_node
        sequence = None if detection is None else detection.parent_node
        if sequence is None:
            return None
        for sibling in sequence.children:
            if not isinstance(sibling, BodiesWhereExpected):
                continue
            for expected in sibling.expected:
                if region.contains(expected.position):
                    return sibling, expected
        return None

    def target_of(self, expected: ExpectedPosition, world: World) -> Optional[str]:
        """
        :param expected: Where the plan expects one of its objects.
        :param world: The world of the run.
        :return: The identity of the plan's object.
        """
        name = next(
            (
                identifier
                for identifier, body in self.step_run.bodies.items()
                if body is expected.body
            ),
            None,
        )
        return None if name is None else self.step_run.target_of(name)

    @staticmethod
    def in_root(point: Point3, world: World) -> Vector3:
        """
        :param point: A point.
        :param world: Its world.
        :return: It in the world's root.
        """
        root_point = world.transform(point, world.root)
        return Vector3(float(root_point.x), float(root_point.y), float(root_point.z))

    @staticmethod
    def bounds(region: VolumetricBoundingBox, world: World) -> Bounds:
        """
        :param region: A box.
        :param world: Its world.
        :return: Its corners in the world's root.
        """
        box = region.transform_to_origin(
            HomogeneousTransformationMatrix(reference_frame=world.root)
        )
        return Bounds(
            Vector3(float(box.min_x), float(box.min_y), float(box.min_z)),
            Vector3(float(box.max_x), float(box.max_y), float(box.max_z)),
        )

    def is_table(self, box: Bounds, world: World) -> bool:
        """
        :param box: A box looked in, in the world's root.
        :param world: The world of the run.
        :return: Whether it is the box above the robot's table.
        """
        table = self.bounds(
            RegionBoxes(self.step_run.context.robot, site=self.step_run.site).box(
                DetectionRegion()
            ),
            world,
        )
        corners = [
            (box.minimum, table.minimum),
            (box.maximum, table.maximum),
        ]
        return all(
            abs(mine.x - theirs.x) < SAME_BOX_METRES
            and abs(mine.y - theirs.y) < SAME_BOX_METRES
            and abs(mine.z - theirs.z) < SAME_BOX_METRES
            for mine, theirs in corners
        )

    # %% what came back

    def answer(
        self, answer: Optional[QueryAnswer], task: PerceptionTask
    ) -> PerceptionAnswer:
        """
        :param answer: RoboKudo's answer to the detection's query, None when none was
            seen.
        :param task: The detection.
        :return: Every object it reported, in the world's root, each with how far it
            lay inside the surface it stands on had it been the object looked for,
            when the detection looks for one of the plan's objects.
        """
        if answer is None or answer.objects is None:
            return PerceptionAnswer(objects=None)
        expected = self.expected(task, task.query.region)
        return PerceptionAnswer(
            objects=[
                self.designator(
                    reported, task, None if expected is None else expected[1].body
                )
                for reported in answer.objects
                if reported.pose
            ]
        )

    def designator(
        self, reported: ObjectDesignator, task: PerceptionTask, body: Optional[Body]
    ) -> ReportedDesignator:
        """
        :param reported: An object RoboKudo reported.
        :param task: The detection it answered.
        :param body: The plan's object the detection looks for, None for none.
        :return: It in the world's root, with how far the detection would put `body`
            inside the surface it stands on where RoboKudo saw it, see
            :meth:`~coraplex.perception.Detection.depth_inside_surface`.
        """
        world = task.query.world
        pose = reported.pose[0]
        frame = (
            world.root
            if pose.header.frame_id == str(world.root.name)
            else world.get_kinematic_structure_entity_by_name(pose.header.frame_id)
        )
        position = pose.pose.position
        orientation = pose.pose.orientation
        extent = reported.shape_size[0].dimensions if reported.shape_size else None
        inside = (
            None
            if body is None
            else Detection(
                semantic_annotation=task.query.semantic_annotation,
                pose=Pose.from_xyz_quaternion(
                    position.x,
                    position.y,
                    position.z,
                    orientation.x,
                    orientation.y,
                    orientation.z,
                    orientation.w,
                    reference_frame=frame,
                ),
            ).depth_inside_surface(
                body, world, task.query.trust_detected_orientation
            )
        )
        return ReportedDesignator(
            position=self.in_root(
                Point3(position.x, position.y, position.z, reference_frame=frame),
                world,
            ),
            size=None if extent is None else Vector3(extent.x, extent.y, extent.z),
            colors=list(reported.color),
            type=reported.type,
            inside_surface=(
                None
                if inside is None
                else SurfaceDepth(
                    surface=inside.surface.root.name.name,
                    depth=inside.depth,
                )
            ),
        )
