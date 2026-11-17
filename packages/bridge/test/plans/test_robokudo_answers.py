"""
Reading RoboKudo's answers from its query action while another node asks, as a real run
does while its detections ask from inside Giskard's process.
"""

from __future__ import annotations

import time
import uuid

import pytest

pytest.importorskip("robokudo_msgs")

from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient, ActionServer
from rclpy.executors import MultiThreadedExecutor
from robokudo_msgs.action import Query
from robokudo_msgs.msg import ObjectDesignator

from cramera_bridge.plans.robokudo_answers import RoboKudoAnswers
from cramera_bridge.ros_node import SpinningNode

ANSWER_SECONDS = 5.0
"""
How long a test waits for a goal to be answered.
"""


def red_cube_at(x: float) -> ObjectDesignator:
    """
    :return: The red cube as RoboKudo reports it, at x in the map.
    """
    pose = PoseStamped()
    pose.header.frame_id = "map"
    pose.pose.position.x = x
    pose.pose.orientation.w = 1.0
    return ObjectDesignator(type="", color=["red"], pose=[pose])


def answering(reported: list):
    """
    :return: An execute callback answering every query with ``reported``, or giving
        up on it when ``reported`` is None.
    """

    def execute(goal_handle):
        result = Query.Result()
        if reported is None:
            goal_handle.abort()
            return result
        result.res = reported
        goal_handle.succeed()
        return result

    return execute


def ask(client: ActionClient) -> None:
    """
    Send a query and wait until it is answered, as a detection in another process does.
    """
    sent = client.send_goal_async(Query.Goal())
    deadline = time.monotonic() + ANSWER_SECONDS
    while not sent.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    answered = sent.result().get_result_async()
    while not answered.done() and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [([red_cube_at(0.79)], [0.79]), ([], []), (None, None)],
    ids=["one red cube", "nothing", "gave up"],
)
def test_the_answer_to_a_query_another_node_asked_is_read(reported, expected):
    action = f"/test_robokudo_{uuid.uuid4().hex}/query"
    with SpinningNode("asking", MultiThreadedExecutor).running() as node:
        server = ActionServer(node, Query, action, execute_callback=answering(reported))
        answers = RoboKudoAnswers(node, action_name=action).start()
        client = ActionClient(node, Query, action)
        assert client.wait_for_server(timeout_sec=ANSWER_SECONDS)
        started = time.time()
        ask(client)
        (answer,) = answers.since(started, count=1, timeout=ANSWER_SECONDS)
        answers.stop()
        client.destroy()
        server.destroy()
    assert answer.ended
    if expected is None:
        assert answer.objects is None
    else:
        assert [seen.pose[0].pose.position.x for seen in answer.objects] == expected


def test_a_query_asked_before_the_start_is_left_out():
    action = f"/test_robokudo_{uuid.uuid4().hex}/query"
    with SpinningNode("asking", MultiThreadedExecutor).running() as node:
        server = ActionServer(node, Query, action, execute_callback=answering([]))
        answers = RoboKudoAnswers(node, action_name=action).start()
        client = ActionClient(node, Query, action)
        assert client.wait_for_server(timeout_sec=ANSWER_SECONDS)
        ask(client)
        later = time.time()
        assert answers.since(later, count=0, timeout=0.5) == []
        answers.stop()
        client.destroy()
        server.destroy()
