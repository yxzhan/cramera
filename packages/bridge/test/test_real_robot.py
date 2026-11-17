"""
The real robot: the preflight checking that its drivers are up before a real run, and
reading where it stands.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import threading
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path


import pytest
from typing_extensions import Iterator

pytest.importorskip("control_msgs")

from cramera_bridge.exceptions import (
    JointStatesNotReceived,
    RealRobotNotReady,
    RealRunTerminated,
)
from cramera_bridge.execution import RealExecution
from cramera_bridge.plans.command import PlanDemoCommand
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.mirroring import PostureMirror
from cramera_bridge.plans.validating import PlanValidator
from cramera_bridge.real_robot import (
    ArmHalt,
    ArmStandstill,
    GiskardScript,
    GiskardProcess,
    OrbbecCameraTopic,
    PreflightCheck,
    RealRobotPreflight,
    RealRobotVariable,
    ReportedPosture,
    RequirementKind,
    RunningGiskard,
    main,
)
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.bundle.files import BundleFile
from cramera_protocol.plan_builder import (
    ArmHaltLine,
    ArmReport,
    DemoDocument,
    DemoOption,
    PlanDocument,
    RobotPosture,
    SafetyStop,
)
from cramera_protocol.geometry import AxisAlignedBox
from cramera_protocol.robot_profile import ArmWorkspace, ExecutorKind, SafetyLimits
from cramera_protocol.validation import ValidationOrder
from giskardpy.motion_statechart.exceptions import SafetyConstraintViolated
from semantic_digital_twin.datastructures.definitions import StaticJointState
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.robots.tracy import (
    Tracy,
    TracyJoint,
    TracyParallelGripperCommandTopic,
    TracyTopic,
    TracyVelocityCommandTopic,
)
from rclpy.action.graph import get_action_names_and_types
from semantic_digital_twin.world import World
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64MultiArray

from .dataset.real_robot import (
    GripperDriverStandIn,
    JointStatePublisherStandIn,
    tracy_gripper_drivers,
)
from .test_execution import PARKED_TRACY_PLAN, needs_tracy

pytestmark = pytest.mark.skipif(
    not GiskardProcess(GiskardScript.STANDALONE).is_installed,
    reason="giskardpy's Tracy scripts are not installed",
)

# %% preflight


@pytest.fixture
def node():
    """
    :return: A spinning node the stand-ins and the preflight share.
    """
    with SpinningNode("cramera_real_robot_test").running() as running:
        yield running


def missing_requirements(report) -> set:
    """
    :return: What the report found missing, by kind and name.
    """
    return {(check.kind, check.name) for check in report.missing}


def test_the_preflight_names_every_driver_the_real_run_misses(node):
    report = RealRobotPreflight(
        giskard_script=GiskardScript.VELOCITY, wait_seconds=0.2
    ).check(node)

    assert not report.ready
    assert missing_requirements(report) == {
        (RequirementKind.ROBOT_DESCRIPTION, "/robot_description"),
        *(
            (RequirementKind.JOINT_STATES, topic.value)
            for topic in (
                TracyTopic.LEFT_ARM_JOINT_STATES,
                TracyTopic.RIGHT_ARM_JOINT_STATES,
            )
        ),
        *(
            (RequirementKind.GRIPPER_ACTION_SERVER, topic.value)
            for topic in TracyParallelGripperCommandTopic
        ),
        *((RequirementKind.CAMERA_TOPIC, topic.value) for topic in OrbbecCameraTopic),
    }


def test_a_gripper_without_its_action_server_is_reported_missing(node):
    left = TracyParallelGripperCommandTopic.LEFT_GRIPPER
    server = GripperDriverStandIn(left.value).serve(node)
    report = RealRobotPreflight(
        giskard_script=GiskardScript.STANDALONE, wait_seconds=1.0
    ).check(node)
    server.destroy()

    assert missing_requirements(report) == {
        (
            RequirementKind.GRIPPER_ACTION_SERVER,
            TracyParallelGripperCommandTopic.RIGHT_GRIPPER.value,
        )
    }


def test_a_preflight_finding_everything_is_ready(node):
    with tracy_gripper_drivers(node):
        report = RealRobotPreflight(
            giskard_script=GiskardScript.STANDALONE, wait_seconds=1.0
        ).check(node)

    assert report.ready
    assert report.missing == []


def test_gripper_servers_discovered_while_the_preflight_waits_are_found(node):
    serving = ExitStack()
    discovered_later = threading.Timer(
        3.0, lambda: serving.enter_context(tracy_gripper_drivers(node))
    )
    discovered_later.start()
    report = RealRobotPreflight(giskard_script=GiskardScript.STANDALONE).check(node)
    discovered_later.join()
    serving.close()

    assert report.ready


def test_the_preflight_command_prints_what_is_missing_and_fails(
    giskard_simulating_the_arms, capsys
):
    with pytest.raises(SystemExit) as ended:
        main()

    assert ended.value.code == 1
    printed = capsys.readouterr().out
    for topic in TracyParallelGripperCommandTopic:
        assert (
            PreflightCheck(
                RequirementKind.GRIPPER_ACTION_SERVER, topic.value, False
            ).to_text()
            in printed
        )


# %% Giskard

GISKARD_OWNER = Path(__file__).parent / "dataset" / "scripts" / "giskard_owner.py"
"""
Starts a Giskard and waits until it is killed.
"""


def test_giskard_ends_with_the_process_that_started_it_however_that_ends():
    owner = subprocess.Popen(
        [sys.executable, str(GISKARD_OWNER)], stdout=subprocess.PIPE, text=True
    )
    launch = Path("/proc") / owner.stdout.readline().strip()
    owner.kill()
    owner.wait()

    deadline = time.monotonic() + 30
    while launch.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not launch.exists()


def blocking_the_stop_signals() -> None:
    """
    Block SIGINT and SIGTERM in a process about to start, as the viewer's server blocked
    them in every process it started until a run ignored its Stop.
    """
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})


def wait_until_giskard_serves(node) -> None:
    """
    Wait until a Giskard serves its command action, as one that runs a goal does.
    """
    action = RealRobotPreflight.giskard_command_action
    deadline = time.monotonic() + 60
    while action not in dict(get_action_names_and_types(node)):
        assert time.monotonic() < deadline, "Giskard never served its action"
        time.sleep(0.2)


def test_a_running_giskard_ends_with_the_process_that_started_it_even_one_blocking_the_stop_signals(
    node,
):
    owner = subprocess.Popen(
        [sys.executable, str(GISKARD_OWNER)],
        stdout=subprocess.PIPE,
        text=True,
        preexec_fn=blocking_the_stop_signals,
    )
    launch = Path("/proc") / owner.stdout.readline().strip()
    wait_until_giskard_serves(node)
    owner.kill()
    owner.wait()

    deadline = time.monotonic() + 30
    while launch.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not launch.exists()


TERMINATION_WAITER = (
    Path(__file__).parent / "dataset" / "scripts" / "termination_waiter.py"
)
"""
Waits, as a run on the real robot does, for a request to end.
"""


def test_a_real_run_started_with_the_stop_signals_blocked_still_ends_when_asked_to():
    waiter = subprocess.Popen(
        [sys.executable, str(TERMINATION_WAITER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=blocking_the_stop_signals,
    )
    assert waiter.stdout.readline().strip() == "waiting"
    waiter.send_signal(signal.SIGTERM)

    assert waiter.wait(timeout=5) != 0
    assert RealRunTerminated.__name__ in waiter.stderr.read()


def test_a_terminated_real_run_performs_no_further_plan_even_when_its_ending_was_caught():
    execution = RealExecution()
    with execution.ended_by_termination():
        with pytest.raises(RealRunTerminated):
            execution.terminate(signal.SIGTERM, None)

    with pytest.raises(RealRunTerminated):
        execution.executor(None, False, [], None)


LEFT_BEHIND = "giskardpy.left_behind"
"""
An argument that makes a sleeping process look like a Giskard.
"""


def sleeping_giskard(environment: dict[str, str]) -> subprocess.Popen:
    """
    :param environment: Its environment beside this process's.
    :return: A process looking like a Giskard, the leader of a session of its own; it is
        reaped as soon as it ends, as a process left behind by its parent is.
    """
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", LEFT_BEHIND],
        env={**os.environ, **environment},
        start_new_session=True,
    )
    threading.Thread(target=process.wait, daemon=True).start()
    return process


def ended_process() -> int:
    """
    :return: The process identity of a process that ended.
    """
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_a_giskard_a_killed_run_left_behind_is_ended_and_a_foreign_one_kept():
    left_behind = sleeping_giskard(
        {RealRobotVariable.GISKARD_OWNER: str(ended_process())}
    )
    owned = sleeping_giskard({RealRobotVariable.GISKARD_OWNER: str(os.getpid())})
    foreign = sleeping_giskard({})
    try:
        ended = RunningGiskard.end_left_behind()
        time.sleep(0.2)

        assert left_behind.pid in ended
        assert left_behind.poll() is not None
        assert owned.poll() is None and foreign.poll() is None
        assert {giskard.pid for giskard in RunningGiskard.all()} >= {
            owned.pid,
            foreign.pid,
        }
    finally:
        for process in (left_behind, owned, foreign):
            process.kill()


def test_the_preflight_names_the_giskard_still_running_with_its_process(node):
    with (
        tracy_gripper_drivers(node),
        GiskardProcess(GiskardScript.STANDALONE).running() as giskard,
    ):
        wait_until_giskard_serves(node)
        report = RealRobotPreflight(
            giskard_script=GiskardScript.STANDALONE, wait_seconds=1.0
        ).check(node)

    assert not report.ready
    assert report.to_text().splitlines()[-1] == (
        "The real robot is not ready: another Giskard is still running "
        f"(pid {giskard.pid}, started by this platform)."
    )


# %% where the real robot stands

STANDING_POSITIONS = {
    TracyTopic.LEFT_ARM_JOINT_STATES.value: {
        TracyJoint.LEFT_SHOULDER_PAN.value: 0.3,
        TracyJoint.LEFT_ELBOW.value: -1.1,
    },
    TracyTopic.RIGHT_ARM_JOINT_STATES.value: {TracyJoint.RIGHT_WRIST_2.value: 0.7},
}
"""
Joint positions the stand-ins of the arms' drivers report, by topic.
"""


@pytest.fixture
def tracy_plan_world() -> World:
    """
    :return: The world of a plan in which Tracy parks its arms.
    """
    plan = PlanDocument.from_json(PARKED_TRACY_PLAN)
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    return demo.build_world()


@pytest.fixture
def standing_robot(node):
    """
    Report :data:`STANDING_POSITIONS` from stand-ins of the arms' drivers.
    """
    with ExitStack() as publishing:
        for topic, positions in STANDING_POSITIONS.items():
            publishing.enter_context(
                JointStatePublisherStandIn(topic, positions).publishing(node)
            )
        yield


def reported_positions(world: World) -> dict:
    """
    :return: The positions the world holds of the joints the stand-ins report.
    """
    return {
        name: world.get_connection_by_name(name).position
        for positions in STANDING_POSITIONS.values()
        for name in positions
    }


@needs_tracy
def test_the_world_takes_the_joint_positions_the_real_robot_reports(
    standing_robot, tracy_plan_world
):
    ReportedPosture().apply(tracy_plan_world)

    assert reported_positions(tracy_plan_world) == pytest.approx(
        {
            name: position
            for positions in STANDING_POSITIONS.values()
            for name, position in positions.items()
        }
    )


@needs_tracy
def test_a_robot_whose_drivers_stay_silent_has_no_reported_posture(tracy_plan_world):
    with pytest.raises(JointStatesNotReceived) as raised:
        ReportedPosture(wait_seconds=0.2).apply(tracy_plan_world)
    assert raised.value.topics == list(ReportedPosture().joint_state_topics)


def test_the_mirror_reports_the_joint_positions_the_drivers_publish(standing_robot):
    mirror = subprocess.Popen(
        [sys.executable, "-m", PostureMirror.__module__],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    with ExitStack() as mirroring:
        mirroring.callback(mirror.wait, timeout=30)
        mirroring.callback(mirror.stdin.close)
        expected = {
            name: position
            for positions in STANDING_POSITIONS.values()
            for name, position in positions.items()
        }
        posture = RobotPosture.from_json(json.loads(mirror.stdout.readline()))
        while posture.joint_positions.keys() != expected.keys():
            posture = RobotPosture.from_json(json.loads(mirror.stdout.readline()))

    assert posture.joint_positions == pytest.approx(expected)


def test_the_mirror_says_how_long_ago_each_arm_reported():
    now = [10.0]
    mirror = PostureMirror(clock=lambda: now[0])
    mirror.keep(
        TracyTopic.LEFT_ARM_JOINT_STATES,
        JointState(name=["left_shoulder_pan_joint"], position=[0.3]),
    )
    now[0] = 10.25

    posture = mirror.posture()

    assert posture.joint_positions == {"left_shoulder_pan_joint": 0.3}
    assert posture.arms == [
        ArmReport(arm="left_arm", age_seconds=pytest.approx(0.25)),
        ArmReport(arm="right_arm"),
    ]


def test_the_mirror_writes_its_arms_before_any_driver_reported():
    mirror = PostureMirror(interval=0.01)
    output = io.StringIO()
    stopped = threading.Event()
    threading.Timer(0.05, stopped.set).start()

    mirror.write(output, stopped)

    first = RobotPosture.from_json(json.loads(output.getvalue().splitlines()[0]))
    assert first == RobotPosture(
        arms=[ArmReport(arm="left_arm"), ArmReport(arm="right_arm")]
    )


@needs_tracy
def test_a_validation_for_the_real_robot_starts_where_it_stands(standing_robot):
    plan = PlanDocument.from_json(PARKED_TRACY_PLAN)
    order = ValidationOrder(demo=DemoDocument(plan=plan), reads_robot_posture=True)
    demo = PlannedTaskDemo.of_document(order.demo, port=0)

    world = PlanValidator(order).build_world(demo)

    assert reported_positions(world) == pytest.approx(
        {
            name: position
            for positions in STANDING_POSITIONS.values()
            for name, position in positions.items()
        }
    )


# %% running a plan on the real robot

GRIPPER_PLAN = {
    **PARKED_TRACY_PLAN,
    "name": "tracy_grips",
    "task": "Park the arms, then close and open the left gripper and close the right",
    "robots": [
        {
            **PARKED_TRACY_PLAN["robots"][0],
            "steps": [
                {"id": "step_1", "action": "park_arms", "values": {"arms": "both"}},
                {
                    "id": "step_2",
                    "action": "set_gripper",
                    "values": {"gripper": "left", "motion": "close"},
                },
                {
                    "id": "step_3",
                    "action": "set_gripper",
                    "values": {"gripper": "left", "motion": "open"},
                },
                {
                    "id": "step_4",
                    "action": "set_gripper",
                    "values": {"gripper": "right", "motion": "close"},
                },
            ],
        }
    ],
}
"""
A plan in which Tracy parks its arms and moves its grippers.
"""

TRACY_SAFETY = SafetyLimits(
    workspaces=[
        ArmWorkspace(
            arm="left",
            label="Left arm workspace",
            box=AxisAlignedBox(
                min_x=0.3, max_x=1.2, min_y=-0.3, max_y=0.7, min_z=0.875, max_z=1.6
            ),
        ),
        ArmWorkspace(
            arm="right",
            label="Right arm workspace",
            box=AxisAlignedBox(
                min_x=0.3, max_x=1.2, min_y=-0.7, max_y=0.3, min_z=0.875, max_z=1.6
            ),
        ),
    ],
    maximum_tool_velocity=0.5,
)
"""
The safety limits of Tracy's profile.
"""


@pytest.fixture
def giskard_simulating_the_arms(monkeypatch) -> None:
    """
    Have real runs start a Giskard that simulates Tracy's arms, as no driver runs.
    """
    monkeypatch.setenv(RealRobotVariable.GISKARD_SCRIPT, GiskardScript.STANDALONE)


@needs_tracy
def test_a_plan_runs_on_the_real_robot_and_drives_its_grippers_through_their_drivers(
    locations, giskard_simulating_the_arms, node
):
    command = PlanDemoCommand(DemoDocument(plan=PlanDocument.from_json(GRIPPER_PLAN)))
    with tracy_gripper_drivers(node) as drivers:
        saved = command.main(
            [DemoOption.PORT, "0", DemoOption.EXECUTOR, ExecutorKind.REAL]
        )

    assert (locations.local_scenes / saved.scene / BundleFile.SCENE).is_file()
    left, right = TracyParallelGripperCommandTopic
    assert len(drivers[left.value].commanded_positions) == 2
    assert len(drivers[right.value].commanded_positions) == 1


@needs_tracy
def test_giskard_stops_a_real_run_that_breaks_a_safety_limit(
    locations, giskard_simulating_the_arms, node, capsys
):
    """
    Giskard's simulated Tracy stands with its arms stretched out beside the table, far
    outside the profile's workspaces, and a real run starts where the robot stands.
    """
    command = PlanDemoCommand(
        DemoDocument(plan=PlanDocument.from_json(GRIPPER_PLAN), safety=TRACY_SAFETY)
    )
    with (
        tracy_gripper_drivers(node),
        pytest.raises(SafetyConstraintViolated) as violated,
    ):
        command.main([DemoOption.PORT, "0", DemoOption.EXECUTOR, ExecutorKind.REAL])

    assert violated.value.constraint == TRACY_SAFETY.workspaces[0].label
    assert SafetyStop.from_output(capsys.readouterr().out) == SafetyStop(
        constraint=f"{violated.value.constraint}: {violated.value.violation}"
    )


@needs_tracy
def test_a_real_run_is_refused_while_a_gripper_driver_is_missing(
    locations, giskard_simulating_the_arms, capsys
):
    command = PlanDemoCommand(DemoDocument(plan=PlanDocument.from_json(GRIPPER_PLAN)))

    saved = command.main([DemoOption.PORT, "0", DemoOption.EXECUTOR, ExecutorKind.REAL])

    assert saved is None
    output = capsys.readouterr().out
    assert "gripper action server present" in output
    assert '"kind": "refused"' in output
    assert "Traceback" not in output


@needs_tracy
def test_attaching_to_a_robot_that_is_not_ready_raises_before_anything_runs(
    giskard_simulating_the_arms,
):
    with pytest.raises(RealRobotNotReady), RealExecution().attached():
        pytest.fail("the block ran although the robot was not ready")


@needs_tracy
def test_the_world_of_a_real_run_follows_the_robot_as_giskard_reports_it(
    giskard_simulating_the_arms, node
):
    plan = PlanDocument.from_json(PARKED_TRACY_PLAN)
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    demo.execution = RealExecution()
    with tracy_gripper_drivers(node), demo.execution.attached():
        world = demo.build_world()
        assert world is demo.execution.reported_world()
        context = demo.build_context(world)
        demo.performance(context).perform(demo.build_plan(context), context)
        [tracy] = world.get_semantic_annotations_by_type(Tracy)
        [planned] = plan.robots
        assert tracy.name == PrefixedName(planned.label, prefix=planned.identifier)
        for arm in tracy.all_arms:
            parked = arm.get_joint_state_by_type(StaticJointState.PARK)
            for connection, position in zip(parked.connections, parked.target_values):
                assert connection.position == pytest.approx(position, abs=0.02)


# %% stopping a run on the real robot

REAL_RUN = (
    Path(__file__).parent / "dataset" / "scripts" / "real_run_until_terminated.py"
)
"""
Performs a plan in a run on the real robot until it ends or is asked to end.
"""


@dataclass
class VelocityCommandRecorder:
    """
    Records the commands every arm's velocity controller would receive.
    """

    commands: dict[str, list[list[float]]] = field(default_factory=dict)
    """
    The velocities of every command received, by topic, in the order received.
    """

    @contextmanager
    def recording(self, node) -> Iterator[VelocityCommandRecorder]:
        """
        Record from `node` while the block runs.
        """
        with ExitStack() as recording:
            for topic in TracyVelocityCommandTopic:
                received = self.commands.setdefault(topic.value, [])
                subscription = node.create_subscription(
                    Float64MultiArray,
                    topic.value,
                    lambda message, received=received: received.append(
                        list(message.data)
                    ),
                    10,
                )
                recording.callback(node.destroy_subscription, subscription)
            yield self


@needs_tracy
def test_a_real_run_asked_to_end_halts_the_arms_and_ends_giskard(
    giskard_simulating_the_arms, node
):
    with (
        tracy_gripper_drivers(node),
        VelocityCommandRecorder().recording(node) as recorder,
    ):
        run = subprocess.Popen(
            [sys.executable, str(REAL_RUN), json.dumps(PARKED_TRACY_PLAN)],
            stdout=subprocess.PIPE,
            text=True,
        )
        started = [Path("/proc") / pid for pid in run.stdout.readline().split()]
        assert run.stdout.readline().strip() == "moving"
        time.sleep(3.0)
        run.send_signal(signal.SIGTERM)
        run.wait(timeout=30)
        time.sleep(0.5)

    assert run.returncode != 0
    assert started
    assert not any(process.exists() for process in started)
    assert {topic: received[-1:] for topic, received in recorder.commands.items()} == {
        topic.value: [[0.0] * 6] for topic in TracyVelocityCommandTopic
    }


# %% the plan builder's own halt of the arms


def started_arm_halt() -> subprocess.Popen:
    """
    :return: The plan builder's halt of the arms, once it said it is ready.
    """
    halt = subprocess.Popen(
        [sys.executable, "-m", "cramera_bridge.arm_halt"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert halt.stdout.readline().strip() == ArmHaltLine.READY
    return halt


@contextmanager
def arms_reporting(node, velocity: float) -> Iterator[None]:
    """
    Report every arm's joints moving at `velocity` from stand-ins of the arms' drivers.
    """
    with ExitStack() as reporting:
        for topic, positions in STANDING_POSITIONS.items():
            reporting.enter_context(
                JointStatePublisherStandIn(
                    topic, positions, velocity=velocity
                ).publishing(node)
            )
        yield


def test_the_plan_builders_arm_halt_commands_zero_velocities_once_its_input_closes(
    node,
):
    with (
        arms_reporting(node, velocity=0.0),
        VelocityCommandRecorder().recording(node) as recorder,
    ):
        halt = started_arm_halt()
        assert recorder.commands == {
            topic.value: [] for topic in TracyVelocityCommandTopic
        }
        output, _ = halt.communicate("", timeout=30)
        time.sleep(0.2)

    assert halt.returncode == 0
    assert output.strip() == ArmHaltLine.HALTED
    assert recorder.commands == {
        topic.value: [[0.0] * 6] for topic in TracyVelocityCommandTopic
    }


def test_the_plan_builders_arm_halt_is_not_confirmed_while_an_arm_reports_moving(node):
    with (
        arms_reporting(node, velocity=0.2),
        VelocityCommandRecorder().recording(node),
    ):
        halt = started_arm_halt()
        output, _ = halt.communicate("", timeout=30)

    assert halt.returncode == 1
    assert output.startswith(ArmHaltLine.NOT_HALTED)


def test_the_plan_builders_arm_halt_fails_without_controllers():
    halt = started_arm_halt()
    output, _ = halt.communicate("", timeout=30)

    assert halt.returncode == 1
    assert output.startswith(ArmHaltLine.NOT_HALTED)


# %% holding the arms still while no plan runs

HOLD_PERIOD = 0.1
"""
How often, in seconds, a standstill of these tests commands zero velocities.
"""


@contextmanager
def held_arms(node) -> Iterator[ArmStandstill]:
    """
    :return: A standstill holding the arms through `node` while the block runs.
    """
    with (
        ArmHalt().ready(node) as halt,
        ArmStandstill(halt, hold_period=HOLD_PERIOD).holding() as standstill,
    ):
        yield standstill


def commands_since(
    recorder: VelocityCommandRecorder, counts: dict[str, int]
) -> dict[str, list[list[float]]]:
    """
    :param counts: How many commands every topic had received before.
    :return: The commands every topic received since.
    """
    return {
        topic: received[counts[topic] :]
        for topic, received in recorder.commands.items()
    }


def command_counts(recorder: VelocityCommandRecorder) -> dict[str, int]:
    """
    :return: How many commands every topic received so far.
    """
    return {topic: len(received) for topic, received in recorder.commands.items()}


def assert_held_still(commands: dict[str, list[list[float]]]) -> None:
    """
    Assert that both arms received zero velocities again and again, and nothing else.
    """
    assert {topic: received[:2] for topic, received in commands.items()} == {
        topic.value: [[0.0] * 6] * 2 for topic in TracyVelocityCommandTopic
    }
    assert all(
        command == [0.0] * 6 for received in commands.values() for command in received
    )


def test_the_arms_are_held_still_while_no_plan_runs(node):
    with (
        VelocityCommandRecorder().recording(node) as recorder,
        held_arms(node),
    ):
        time.sleep(5 * HOLD_PERIOD)

    assert_held_still(recorder.commands)


def test_the_arms_are_not_commanded_while_a_plan_runs(node):
    with (
        VelocityCommandRecorder().recording(node) as recorder,
        held_arms(node) as standstill,
    ):
        standstill.after_compile(None)
        compiled = command_counts(recorder)
        time.sleep(5 * HOLD_PERIOD)

    assert commands_since(recorder, compiled) == {
        topic.value: [] for topic in TracyVelocityCommandTopic
    }


def test_the_arms_are_halted_once_a_plan_ends(node):
    with (
        VelocityCommandRecorder().recording(node) as recorder,
        held_arms(node) as standstill,
    ):
        standstill.after_compile(None)
        compiled = command_counts(recorder)
        standstill.after_run(None)
        time.sleep(0.2)
        ended = commands_since(recorder, compiled)

    assert {topic: received[:1] for topic, received in ended.items()} == {
        topic.value: [[0.0] * 6] for topic in TracyVelocityCommandTopic
    }


@needs_tracy
def test_the_arms_are_held_still_after_a_plan_ran_on_the_real_robot(
    giskard_simulating_the_arms, node
):
    plan = PlanDocument.from_json(PARKED_TRACY_PLAN)
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    demo.execution = RealExecution()
    with (
        tracy_gripper_drivers(node),
        VelocityCommandRecorder().recording(node) as recorder,
        demo.execution.attached(),
    ):
        context = demo.build_context(demo.build_world())
        demo.performance(context).perform(demo.build_plan(context), context)
        ran = command_counts(recorder)
        time.sleep(3 * ArmStandstill.hold_period)
        held = commands_since(recorder, ran)

    assert_held_still(held)
