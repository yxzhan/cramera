"""
Running a plan's steps with retries: the run log every attempt is written to, a step
keeping its object in view of Tracy's fixed camera, a cube nudged while the plan runs in
MuJoCo, which the step acting on it grounds anew, and a cube taken away, whose step is
given up once it used up its attempts.
"""

from __future__ import annotations

import io
import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from importlib.util import find_spec

import pytest
from typing_extensions import Optional

from coraplex.plans import executors
from cramera_bridge.exceptions import StepAbandoned
from cramera_bridge.execution import PhysicalExecution, PlanExecution
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.objects import LIBRARY_VARIABLE
from cramera_bridge.ros_node import SpinningNode
from cramera_bridge.plans.run_log import (
    AttemptOutcome,
    LookAt,
    ObjectPerceived,
    RunLog,
    RunLogEntry,
    SafetyStopped,
    StepAttempt,
    StepContinuedUnseen,
    StepRegrounded,
    StepScheduled,
    StepStarted,
    View,
)
from cramera_bridge.plans.step_run import (
    Alternative,
    AttemptedStep,
    AttemptProgress,
    OBJECT_RECOGNITION_NOT_RUNNING,
    failures_left_to_the_run_log,
)
from cramera_protocol.geometry import FloorRectangle
from cramph.composites import Sequence
from cramph.context import StatechartContext
from cramph.data_types import LifeCycleValues
from cramph.executor import Executor, StatechartExecutor
from cramph.monitors import CountTicks
from cramph.statechart import Statechart
from cramera_protocol.plan_builder import (
    DemoDocument,
    MonitorObservation,
    PlanDocument,
    RunEvent,
    RunEventKind,
    RunMonitor,
    StepState,
)
from cramera_protocol.robot_profile import PerceptionSettings, RuntimeCheck, Zone
from cramera_protocol.viewer import EnvironmentVariable
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from rclpy.executors import MultiThreadedExecutor
from semantic_digital_twin.robots.tracy import Tracy
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

from ..dataset.bundles import DATASET

CUBE_LIBRARY = DATASET / "cube_library"
"""
A red, a yellow and a blue cube, each named by its color.
"""

TRACY_PICK = DATASET / "plans" / "tracy_pick.json"
"""
Tracy parks its arms, then picks up the red cube with the left arm.
"""

NUDGED_CUBES = DATASET / "plans" / "tracy_nudged_cubes.json"
"""
Tracy detects the red cube and puts it into the centre zone, then the blue cube into the
right tray, keeping each in view while it is picked up.
"""

ZONES = [
    Zone(
        identifier="center",
        label="Center",
        area=FloorRectangle(min_x=0.6, max_x=0.8, min_y=-0.1, max_y=0.1),
    ),
    Zone(
        identifier="right_tray",
        label="Right tray",
        area=FloorRectangle(min_x=0.6, max_x=0.8, min_y=-0.5, max_y=-0.3),
    ),
]
"""
The zones of Tracy's profile the plan puts the cubes into.
"""

NUDGE = 0.05
"""
How far, in metres, the red cube is pushed along the table once it was detected.
"""

FAR_AWAY = (3.0, 3.0, 0.03)
"""
Where the blue cube is taken to once the red one was put down: on the floor, beyond what
Tracy's camera sees and its arms reach.
"""

# %% the run log


def test_every_entry_is_a_line_of_the_runs_output():
    stream = io.StringIO()
    run_log = RunLog(stream=stream)
    entry = StepAttempt(
        step="step_3", number=1, attempts=3, outcome=AttemptOutcome.SUCCEEDED
    )

    run_log.write(entry)

    assert stream.getvalue() == (
        f"{RunLog.line_prefix} {entry.line()}\n{entry.event().to_output_line()}\n"
    )
    assert run_log.entries == [entry]
    assert run_log.events() == [entry.event()]


@dataclass
class FollowingExecutor(Executor):
    """
    Holds a statechart another process ticks, as the client of the real robot's motion
    server does, which takes over the states that process reports instead of ticking.
    """

    def default_context_extensions(self) -> list:
        return []

    def default_executor_extensions(self) -> list:
        return []

    def execute(self) -> None:
        """
        Runs nothing: the other process ticks the statechart.
        """


def test_the_steps_of_a_plan_ticked_in_another_process_are_followed_as_they_run():
    """
    On the real robot the motion server ticks the plan and the run only takes over the
    states it reports, so no tick of the run's own executor tells how far the plan is;
    every state taken over has to.
    """
    context = StatechartContext(world=World())
    statechart = Statechart(context=context)
    first, second = CountTicks(ticks=1), CountTicks(ticks=1)
    statechart.add_node(Sequence(nodes=[first, second]))
    stream = io.StringIO()
    progress = AttemptProgress(run_log=RunLog(stream=stream), attempts=3)
    progress.steps = [
        AttemptedStep(node=first, step="step_1", number=1),
        AttemptedStep(node=second, step="step_2", number=1),
    ]
    other_process = StatechartExecutor(context=context)
    other_process.compile(statechart)
    following = FollowingExecutor(context=context)
    following.compile(statechart)
    progress.after_compile(following)
    while second.life_cycle_state is not LifeCycleValues.RUNNING:
        other_process.tick()
    reported_while_running = [
        (event.step, event.state) for event in progress.run_log.events()
    ]

    assert reported_while_running == [
        ("step_1", StepState.RUNNING),
        ("step_1", StepState.DONE),
        ("step_2", StepState.RUNNING),
    ]


@pytest.mark.parametrize(
    "outcome, state",
    [
        (AttemptOutcome.SUCCEEDED, StepState.DONE),
        (AttemptOutcome.RETRYING, StepState.RETRYING),
        (AttemptOutcome.ABORTED, StepState.FAILED),
        (AttemptOutcome.STOPPED, StepState.FAILED),
    ],
)
def test_an_attempt_ends_its_step_in_the_state_of_its_outcome(outcome, state):
    entry = StepAttempt(
        step="step_3", number=2, attempts=3, outcome=outcome, arm="right"
    )
    assert entry.event() == RunEvent(
        kind=RunEventKind.STEP,
        step="step_3",
        state=state,
        attempt=2,
        attempts=3,
        arm="right",
    )


def test_a_step_is_pending_then_running_with_the_arm_its_grounding_chose():
    scheduled = StepScheduled(step="step_3", arm="left")
    started = StepStarted(step="step_3", number=1, attempts=3, arm="left")
    assert scheduled.event().state is StepState.PENDING
    assert started.event().state is StepState.RUNNING
    assert "left arm" in scheduled.line() and "left arm" in started.line()


def test_a_perceived_object_and_a_safety_stop_are_events_of_their_own():
    perceived = ObjectPerceived(step="step_2", target="red_cube", color="red")
    stopped = SafetyStopped(constraint="Left arm workspace", step="step_3")
    assert perceived.event().kind is RunEventKind.PERCEIVED
    assert perceived.line() == "step_2: perceived red_cube (red)"
    assert stopped.event() == RunEvent(
        kind=RunEventKind.SAFETY_STOP, step="step_3", constraint="Left arm workspace"
    )


def test_a_step_grounded_again_says_in_one_line_where_its_object_was_found():
    entry = StepRegrounded(step="step_3", targets=["red_cube"])
    assert entry.line() == (
        "step_3: red_cube found elsewhere on the table; the step was grounded again "
        "where it lies"
    )
    assert entry.event() == RunEvent(
        kind=RunEventKind.REGROUNDED, step="step_3", target="red_cube"
    )


def test_a_step_carrying_on_unseen_says_in_one_line_where_it_acts():
    entry = StepContinuedUnseen(
        step="step_3", targets=["red_cube"], reason="the red cube was not seen"
    )
    assert entry.line() == (
        "step_3: the red cube was not seen; continuing where the plan expected "
        "red cube"
    )
    assert entry.event() == RunEvent(
        kind=RunEventKind.CONTINUED_UNSEEN,
        step="step_3",
        target="red_cube",
        reason="the red cube was not seen",
    )


def test_a_look_at_says_whether_its_target_is_in_view():
    entry = LookAt(step="step_3", target="red_cube", view=View.IN_VIEW)
    assert f"look-at: {View.IN_VIEW}" in entry.line()
    assert entry.event().monitor is RunMonitor.LOOK_AT
    assert entry.event().observation is MonitorObservation.HOLDS


def test_a_failed_attempt_names_why_and_what_the_next_one_does():
    entry = StepAttempt(
        step="step_3",
        number=1,
        attempts=3,
        outcome=AttemptOutcome.RETRYING,
        reason="the cube moved.",
        next_attempt=Alternative.OTHER_ARM.value,
    )
    assert entry.line() == (
        f"step_3: attempt 1/3 {AttemptOutcome.RETRYING}: the cube moved. "
        f"Next attempt {Alternative.OTHER_ARM.value}."
    )


# %% perceiving while the plan runs


def test_a_failed_plan_is_left_to_the_run_log_not_logged_as_an_error(caplog):
    """
    The executor logs every plan that did not finish as an error before it raises; a
    step run reports each such failure in the run log, as a retry, a step grounded anew
    or the step given up, so the error line, which also showed after a person's Stop, is
    held back while the step run performs its plans.
    """
    executor_log = logging.getLogger(executors.__name__)
    with failures_left_to_the_run_log():
        executor_log.error("Motion did not finish")
        executor_log.warning("a warning")
    executor_log.error("Motion did not finish, outside a step run")

    assert [(record.levelno, record.message) for record in caplog.records] == [
        (logging.WARNING, "a warning"),
        (logging.ERROR, "Motion did not finish, outside a step run"),
    ]


def test_a_pick_reports_the_object_its_grasp_perceived_first(tmp_path, monkeypatch):
    if not Tracy.is_description_installed():
        pytest.skip("iai_tracy_description is not installed")
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "data"))
    plan = PlanDocument.from_json(json.loads(TRACY_PICK.read_text("utf-8")))
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    [_, pick] = plan.robots[0].steps

    demo.run()

    perceived = [
        entry for entry in demo.run_log.entries if isinstance(entry, ObjectPerceived)
    ]
    assert perceived == [
        ObjectPerceived(step=pick.identifier, target="red_cube", color="red")
    ]


# %% nudging and taking away cubes while the plan runs in physics


@dataclass
class Disturbance:
    """
    Moves cubes of the running plan once the run log says a step succeeded.
    """

    demo: PlannedTaskDemo
    """
    The running demo.
    """

    moves: dict[str, tuple[str, tuple[float, float, float]]]
    """
    By the identity of the step after whose success it moves: the identity of the cube
    and the offset it is moved by.
    """

    done: list[str] = field(default_factory=list)
    """
    The steps after which a cube was moved.
    """

    def __call__(self, entry: RunLogEntry) -> None:
        if not isinstance(entry, StepAttempt) or entry.step not in self.moves:
            return
        if entry.outcome is not AttemptOutcome.SUCCEEDED or entry.step in self.done:
            return
        identifier, offset = self.moves[entry.step]
        self.move(self.demo.bodies[identifier], offset)
        self.done.append(entry.step)

    @staticmethod
    def move(cube: Body, offset: tuple[float, float, float]) -> None:
        """
        Move a free cube by an offset in the world's coordinates.
        """
        world = cube._world
        position = cube.global_pose.position
        cube.parent_connection.origin = HomogeneousTransformationMatrix.from_xyz_rpy(
            float(position.x) + offset[0],
            float(position.y) + offset[1],
            float(position.z) + offset[2],
            reference_frame=world.root,
        )
        world.notify_state_change()


@dataclass
class DisturbedRun:
    """
    What a run of the plan wrote to its log, and how it ended.
    """

    entries: list[RunLogEntry]
    """
    The run log.
    """

    failure: StepAbandoned
    """
    The step the run gave up.
    """

    def attempts_at(self, step: str) -> list[StepAttempt]:
        """
        :return: The attempts at a step, in order.
        """
        return [
            entry
            for entry in self.entries
            if isinstance(entry, StepAttempt) and entry.step == step
        ]


@pytest.fixture(scope="module")
def disturbed_run(tmp_path_factory) -> DisturbedRun:
    """
    :return: The plan run in MuJoCo, the red cube nudged once it was detected and the
        blue one taken away once the red one was put down.
    """
    if not Tracy.is_description_installed():
        pytest.skip("iai_tracy_description is not installed")
    data = tmp_path_factory.mktemp("data")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
        patch.setenv(EnvironmentVariable.DATA, str(data))
        plan = PlanDocument.from_json(json.loads(NUDGED_CUBES.read_text("utf-8")))
        demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan, zones=ZONES), port=0)
        demo.execution = PhysicalExecution()
        far_away = (FAR_AWAY[0] - 0.55, FAR_AWAY[1] - 0.30, FAR_AWAY[2] - 0.906)
        demo.run_log.listeners.append(
            Disturbance(
                demo,
                {
                    "step_2": ("red_cube", (0.0, -NUDGE, 0.0)),
                    "step_4": ("blue_cube", far_away),
                },
            )
        )
        with pytest.raises(StepAbandoned) as abandoned:
            demo.run()
    return DisturbedRun(entries=demo.run_log.entries, failure=abandoned.value)


def test_a_step_keeping_its_cube_in_view_reports_it_in_view(disturbed_run):
    looks = [entry for entry in disturbed_run.entries if isinstance(entry, LookAt)]
    assert LookAt(step="step_3", target="detected:step_2", view=View.IN_VIEW) in looks


def test_a_cube_nudged_once_detected_is_grounded_anew_and_picked_up_at_once(
    disturbed_run,
):
    assert StepRegrounded(step="step_3", targets=["red_cube"]) in disturbed_run.entries
    [picked] = disturbed_run.attempts_at("step_3")
    assert picked.number == 1
    assert picked.outcome is AttemptOutcome.SUCCEEDED
    [placed] = disturbed_run.attempts_at("step_4")
    assert placed.outcome is AttemptOutcome.SUCCEEDED


def test_a_cube_taken_away_aborts_the_plan_once_its_step_used_up_its_attempts(
    disturbed_run,
):
    attempts = disturbed_run.attempts_at("step_5")
    assert [attempt.number for attempt in attempts] == [1, 2, 3]
    assert attempts[-1].outcome is AttemptOutcome.ABORTED
    assert disturbed_run.failure == StepAbandoned(
        "step_5", attempts[-1].attempts, attempts[-1].reason
    )


def test_the_run_events_follow_every_step_through_its_retries(disturbed_run):
    events = [entry.event() for entry in disturbed_run.entries]
    states = {
        step: [
            event.state
            for event in events
            if event.kind is RunEventKind.STEP and event.step == step
        ]
        for step in ("step_2", "step_3", "step_5")
    }
    assert states["step_2"] == [StepState.PENDING, StepState.RUNNING, StepState.DONE]
    assert StepState.RETRYING not in states["step_3"]
    assert states["step_3"][-1] is StepState.DONE
    assert states["step_5"].count(StepState.RETRYING) == 2
    assert states["step_5"][-1] is StepState.FAILED
    [perceived, *_] = [e for e in events if e.kind is RunEventKind.PERCEIVED]
    assert (perceived.target, perceived.color) == ("red_cube", "red")
    [picked] = [
        event
        for event in events
        if event.step == "step_3" and event.state is StepState.DONE
    ]
    assert picked.arm in ("left", "right")


# %% how strictly a run checks what it perceives before a step


MOVED_WITHIN_REACH = (0.0, -0.04, 0.0)
"""
How far the red cube is pushed before the run: farther than the strict check allows,
inside the box the pick looks for it in.
"""

MOVED_ELSEWHERE = (0.10, 0.0, 0.0)
"""
How far the red cube is pushed before the run: beyond the box the pick looks for it in,
still on the table.
"""

TAKEN_AWAY = (FAR_AWAY[0] - 0.55, FAR_AWAY[1] - 0.10, FAR_AWAY[2] - 0.906)
"""
How far the red cube is taken before the run: onto the floor, out of sight.
"""

VALIDATED = (0.55, 0.10)
"""
Where the plan holds the red cube on the table, in the world's coordinates.
"""


@dataclass
class PickRun:
    """
    How a run of the pick, its cube moved once the plan was grounded, ended.
    """

    entries: list[RunLogEntry]
    """
    The run log.
    """

    abandoned: Optional[StepAbandoned]
    """
    The step the run gave up, or None when every step succeeded.
    """

    picked_at: Optional[tuple[float, float]]
    """
    Where the cube lay on the table once the pick took hold of it, or None when it did
    not.
    """

    def attempts_at(self, step: str) -> list[StepAttempt]:
        """
        :return: The attempts at a step, in order.
        """
        return [
            entry
            for entry in self.entries
            if isinstance(entry, StepAttempt) and entry.step == step
        ]


@dataclass
class MovedOnceGrounded:
    """
    Moves the red cube once the run grounded the plan and scheduled the pick, before
    anything ran.
    """

    demo: PlannedTaskDemo
    """
    The running demo.
    """

    offset: tuple[float, float, float]
    """
    How far the cube is moved, in the world's coordinates.
    """

    def __call__(self, entry: RunLogEntry) -> None:
        if isinstance(entry, StepScheduled) and entry.step == "step_2":
            Disturbance.move(self.demo.bodies["red_cube"], self.offset)


@dataclass
class WhereTheCubeWasPicked:
    """
    Remembers where the red cube lay on the table once its pick succeeded.
    """

    demo: PlannedTaskDemo
    """
    The running demo.
    """

    position: Optional[tuple[float, float]] = None
    """
    Where the cube lay, or None before the pick succeeded.
    """

    def __call__(self, entry: RunLogEntry) -> None:
        if not isinstance(entry, StepAttempt) or entry.step != "step_2":
            return
        if entry.outcome is not AttemptOutcome.SUCCEEDED:
            return
        position = self.demo.bodies["red_cube"].global_pose.position
        self.position = (float(position.x), float(position.y))


def run_pick(
    tmp_path,
    monkeypatch,
    runtime_check: RuntimeCheck,
    moved: Optional[tuple[float, float, float]] = None,
    execution: Optional[PlanExecution] = None,
    abandons: bool = False,
) -> PickRun:
    """
    Run Tracy's pick of the red cube kinematically, its arm left to the run's grounding,
    which records where the pick expects the cube, the cube moved once the run grounded
    the plan.

    :param abandons: Whether the run is expected to give up its pick.
    """
    monkeypatch.setenv(LIBRARY_VARIABLE, str(CUBE_LIBRARY))
    monkeypatch.setenv(EnvironmentVariable.DATA, str(tmp_path / "data"))
    document = json.loads(TRACY_PICK.read_text("utf-8"))
    document["robots"][0]["steps"][1]["values"]["arm"] = "automatic"
    plan = PlanDocument.from_json(document)
    perception = PerceptionSettings(answer_timeout=0.5, runtime_check=runtime_check)
    demo = PlannedTaskDemo.of_document(
        DemoDocument(plan=plan, perception=perception), port=0
    )
    if execution is not None:
        demo.execution = execution
    if moved is not None:
        demo.run_log.listeners.append(MovedOnceGrounded(demo, moved))
    picked = WhereTheCubeWasPicked(demo)
    demo.run_log.listeners.append(picked)
    with pytest.raises(StepAbandoned) if abandons else nullcontext() as abandoned:
        demo.run()
    return PickRun(
        entries=demo.run_log.entries,
        abandoned=abandoned.value if abandons else None,
        picked_at=picked.position,
    )


@pytest.fixture
def tracy_installed() -> None:
    if not Tracy.is_description_installed():
        pytest.skip("iai_tracy_description is not installed")


def test_strict_grounds_a_pick_anew_when_its_cube_lies_beyond_2_cm(
    tmp_path, monkeypatch, tracy_installed
):
    run = run_pick(tmp_path, monkeypatch, RuntimeCheck.STRICT, MOVED_WITHIN_REACH)

    assert StepRegrounded(step="step_2", targets=["red_cube"]) in run.entries


def test_lenient_picks_a_cube_seen_near_where_it_was_validated_where_it_was_seen(
    tmp_path, monkeypatch, tracy_installed
):
    run = run_pick(tmp_path, monkeypatch, RuntimeCheck.LENIENT, MOVED_WITHIN_REACH)

    assert not any(isinstance(entry, StepRegrounded) for entry in run.entries)
    [picked] = run.attempts_at("step_2")
    assert picked.outcome is AttemptOutcome.SUCCEEDED
    assert run.picked_at == pytest.approx(
        (VALIDATED[0] + MOVED_WITHIN_REACH[0], VALIDATED[1] + MOVED_WITHIN_REACH[1]),
        abs=0.01,
    )


@pytest.mark.parametrize("runtime_check", list(RuntimeCheck))
def test_a_cube_seen_elsewhere_grounds_its_pick_anew_where_it_lies(
    tmp_path, monkeypatch, tracy_installed, runtime_check
):
    run = run_pick(tmp_path, monkeypatch, runtime_check, MOVED_ELSEWHERE)

    assert StepRegrounded(step="step_2", targets=["red_cube"]) in run.entries
    assert run.picked_at == pytest.approx(
        (VALIDATED[0] + MOVED_ELSEWHERE[0], VALIDATED[1] + MOVED_ELSEWHERE[1]),
        abs=0.01,
    )


def test_strict_gives_up_a_pick_whose_cube_is_seen_nowhere(
    tmp_path, monkeypatch, tracy_installed
):
    run = run_pick(
        tmp_path, monkeypatch, RuntimeCheck.STRICT, TAKEN_AWAY, abandons=True
    )

    assert run.abandoned.step == "step_2"
    assert run.abandoned.reason.startswith(
        "the red cube was not detected on the table again: "
    )
    assert not any(isinstance(entry, StepContinuedUnseen) for entry in run.entries)


def test_lenient_picks_a_cube_seen_nowhere_where_it_was_validated(
    tmp_path, monkeypatch, tracy_installed
):
    """
    The world the collision avoidance plans in puts the cube back where the plan was
    validated with it, where the pick then acts; in this test's shared world, that is
    where the cube lies again.
    """
    run = run_pick(tmp_path, monkeypatch, RuntimeCheck.LENIENT, TAKEN_AWAY)

    assert (
        StepContinuedUnseen(
            step="step_2", targets=["red_cube"], reason="the red cube was not seen"
        )
        in run.entries
    )
    [picked] = run.attempts_at("step_2")
    assert (picked.number, picked.outcome) == (1, AttemptOutcome.SUCCEEDED)
    assert run.picked_at == pytest.approx(VALIDATED, abs=0.01)


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_strict_gives_up_a_pick_whose_perception_does_not_answer(
    tmp_path, monkeypatch, tracy_installed
):
    from ..dataset.real_robot import RoboKudoThatNeverAnswers
    from .test_grounding import KinematicExecutionAskingRoboKudo

    with SpinningNode("robokudo_stand_in", MultiThreadedExecutor).running() as node:
        with RoboKudoThatNeverAnswers().serving(node):
            run = run_pick(
                tmp_path,
                monkeypatch,
                RuntimeCheck.STRICT,
                execution=KinematicExecutionAskingRoboKudo(ros_node=node),
                abandons=True,
            )

    assert run.abandoned.step == "step_2"
    assert not any(isinstance(entry, StepContinuedUnseen) for entry in run.entries)


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_lenient_picks_where_validated_when_its_perception_does_not_answer(
    tmp_path, monkeypatch, tracy_installed
):
    from ..dataset.real_robot import RoboKudoThatNeverAnswers
    from .test_grounding import KinematicExecutionAskingRoboKudo

    with SpinningNode("robokudo_stand_in", MultiThreadedExecutor).running() as node:
        with RoboKudoThatNeverAnswers().serving(node):
            run = run_pick(
                tmp_path,
                monkeypatch,
                RuntimeCheck.LENIENT,
                execution=KinematicExecutionAskingRoboKudo(ros_node=node),
            )

    [continued] = [
        entry for entry in run.entries if isinstance(entry, StepContinuedUnseen)
    ]
    assert continued.step == "step_2"
    assert continued.reason.startswith("RoboKudo did not answer within 0.5 s")
    assert run.picked_at == pytest.approx(VALIDATED, abs=0.01)


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_strict_gives_up_a_pick_whose_object_recognition_is_not_running(
    tmp_path, monkeypatch, tracy_installed
):
    from .test_grounding import KinematicExecutionAskingRoboKudo

    with SpinningNode("no_robokudo", MultiThreadedExecutor).running() as node:
        run = run_pick(
            tmp_path,
            monkeypatch,
            RuntimeCheck.STRICT,
            execution=KinematicExecutionAskingRoboKudo(ros_node=node),
            abandons=True,
        )

    assert run.abandoned.step == "step_2"
    assert run.abandoned.reason == OBJECT_RECOGNITION_NOT_RUNNING
    assert "object recognition" in run.abandoned.reason
    assert "not running" in run.abandoned.reason
    assert not any(isinstance(entry, StepContinuedUnseen) for entry in run.entries)


@pytest.mark.skipif(
    find_spec("robokudo_msgs") is None, reason="RoboKudo's messages are not installed"
)
def test_lenient_picks_where_validated_when_its_object_recognition_is_not_running(
    tmp_path, monkeypatch, tracy_installed
):
    """
    An object in the twin counts as seen; a perception that is not running only leaves
    the twin's belief unrefined.
    """
    from .test_grounding import KinematicExecutionAskingRoboKudo

    with SpinningNode("no_robokudo", MultiThreadedExecutor).running() as node:
        run = run_pick(
            tmp_path,
            monkeypatch,
            RuntimeCheck.LENIENT,
            execution=KinematicExecutionAskingRoboKudo(ros_node=node),
        )

    assert (
        StepContinuedUnseen(
            step="step_2",
            targets=["red_cube"],
            reason=OBJECT_RECOGNITION_NOT_RUNNING,
        )
        in run.entries
    )
    assert run.picked_at == pytest.approx(VALIDATED, abs=0.01)
