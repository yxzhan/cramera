"""
Stand-ins for the drivers of the real Tracy: its grippers' parallel gripper command
servers and its arms' joint state publishers, and for a RoboKudo that never answers.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from threading import Event

from control_msgs.action import ParallelGripperCommand
from rclpy.action import ActionServer
from rclpy.action.server import ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from sensor_msgs.msg import JointState
from typing_extensions import Iterator

from coraplex.perception import ROBOKUDO_QUERY_ACTION_NAME
from robokudo_msgs.action import Query
from semantic_digital_twin.robots.tracy import TracyParallelGripperCommandTopic

# %% grippers


@dataclass
class GripperDriverStandIn:
    """
    Answers every goal of a gripper's parallel gripper command action as reached.
    """

    topic: str
    """
    The name the action is served under.
    """

    commanded_positions: list[float] = field(default_factory=list)
    """
    The finger positions the goals commanded, in the order they arrived.
    """

    def execute(self, goal_handle: ServerGoalHandle) -> ParallelGripperCommand.Result:
        """
        :param goal_handle: A goal that arrived.
        :return: The result reporting it reached.
        """
        self.commanded_positions.extend(goal_handle.request.command.position)
        goal_handle.succeed()
        return ParallelGripperCommand.Result(reached_goal=True)

    def serve(self, node: Node) -> ActionServer:
        """
        :param node: The node serving the action.
        :return: The server, until it is destroyed.
        """
        return ActionServer(
            node, ParallelGripperCommand, self.topic, execute_callback=self.execute
        )


@contextmanager
def tracy_gripper_drivers(node: Node) -> Iterator[dict[str, GripperDriverStandIn]]:
    """
    Serve both of Tracy's gripper actions from `node` while the block runs.

    :param node: A spinning node.
    :return: The stand-in of each gripper by its action's name.
    """
    drivers = {
        topic.value: GripperDriverStandIn(topic.value)
        for topic in TracyParallelGripperCommandTopic
    }
    with ExitStack() as serving:
        for driver in drivers.values():
            serving.callback(driver.serve(node).destroy)
        yield drivers


# %% joint states


@dataclass
class JointStatePublisherStandIn:
    """
    Publishes fixed positions of named joints on a joint state topic, as a driver does.
    """

    topic: str
    """
    The topic the positions are published on.
    """

    positions: dict[str, float]
    """
    The position of each joint by its name.
    """

    period: float = 0.05
    """
    How long, in seconds, lies between two messages.
    """

    velocity: float = 0.0
    """
    The velocity every joint reports.
    """

    @contextmanager
    def publishing(self, node: Node) -> Iterator[None]:
        """
        Publish the positions from `node` while the block runs.

        :param node: A spinning node.
        """
        publisher = node.create_publisher(JointState, self.topic, 10)
        message = JointState(
            name=list(self.positions),
            position=list(self.positions.values()),
            velocity=[self.velocity] * len(self.positions),
        )
        timer = node.create_timer(self.period, lambda: publisher.publish(message))
        with ExitStack() as publishing:
            publishing.callback(node.destroy_publisher, publisher)
            publishing.callback(node.destroy_timer, timer)
            yield


# %% perception


@dataclass
class RoboKudoThatNeverAnswers:
    """
    Takes up every query on RoboKudo's query action and answers none, as a pipeline does
    whose camera data never arrives.
    """

    released: Event = field(default_factory=Event)
    """
    Set once the queries taken up may end, unanswered.
    """

    def execute(self, goal_handle: ServerGoalHandle) -> Query.Result:
        """
        :param goal_handle: A query that arrived, held until :attr:`released` is set.
        :return: An empty result, the query aborted.
        """
        self.released.wait()
        goal_handle.abort()
        return Query.Result()

    @contextmanager
    def serving(self, node: Node) -> Iterator[None]:
        """
        Serve the query action from `node` while the block runs.

        :param node: A node spun by a multi-threaded executor.
        """
        server = ActionServer(
            node,
            Query,
            ROBOKUDO_QUERY_ACTION_NAME,
            execute_callback=self.execute,
            callback_group=ReentrantCallbackGroup(),
        )
        with ExitStack() as serving:
            serving.callback(server.destroy)
            serving.callback(self.released.set)
            yield
