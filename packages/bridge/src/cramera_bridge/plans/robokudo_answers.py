"""
RoboKudo's answers to every query of its query action, whichever process asked: on the
real robot a run's detections ask from inside Giskard's process, so the run reads what
RoboKudo reported from the action itself.

A ROS 2 action publishes the state of every goal it accepted on its status topic and
keeps each result for its result timeout (15 minutes by default), handing it to any
node asking its result service for the goal. This listener follows the status topic
and fetches the result of every goal that ended.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from action_msgs.msg import GoalStatus, GoalStatusArray
from rclpy.client import Client
from rclpy.node import Node
from rclpy.qos import qos_profile_action_status_default
from rclpy.subscription import Subscription
from rclpy.task import Future
from robokudo_msgs.action import Query
from robokudo_msgs.msg import ObjectDesignator
from typing_extensions import Dict, List, Optional, Self

from coraplex.perception import ROBOKUDO_QUERY_ACTION_NAME

STATUS_TOPIC_SUFFIX = "/_action/status"
"""
The topic below an action's name its goals' states are published on.
"""

RESULT_SERVICE_SUFFIX = "/_action/get_result"
"""
The service below an action's name its goals' results are fetched from.
"""

ANSWERED_STATES = (GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_ABORTED)
"""
The states of a goal whose result is fetched.
"""

ENDED_STATES = (*ANSWERED_STATES, GoalStatus.STATUS_CANCELED)
"""
The states of a goal that ended.
"""

NANOSECONDS = 1e-9
"""
Seconds in a nanosecond.
"""


@dataclass
class QueryAnswer:
    """
    One query RoboKudo accepted, and what it answered.
    """

    accepted: float
    """
    When RoboKudo accepted the query, in seconds since the epoch.
    """

    ended: bool = False
    """
    Whether the query ended and its answer, if any, was fetched.
    """

    fetching: bool = False
    """
    Whether its answer is being fetched.
    """

    objects: Optional[List[ObjectDesignator]] = None
    """
    The objects RoboKudo reported; None for a query it gave up on, that was canceled
    or that has not ended.
    """


@dataclass
class RoboKudoAnswers:
    """
    Follows every query of RoboKudo's query action and keeps its answer.
    """

    node: Node
    """
    The node the status is read and the results are fetched through; it is spun
    elsewhere.
    """

    action_name: str = f"/{ROBOKUDO_QUERY_ACTION_NAME}"
    """
    The action RoboKudo answers queries on.
    """

    answers: Dict[bytes, QueryAnswer] = field(default_factory=dict, init=False)
    """
    Every query seen, by its goal identity.
    """

    changed: threading.Condition = field(
        default_factory=threading.Condition, init=False
    )
    """
    Guards :attr:`answers`, notified when a query ended.
    """

    subscription: Optional[Subscription] = field(default=None, init=False)
    """
    The subscription to the status topic, while started.
    """

    client: Optional[Client] = field(default=None, init=False)
    """
    The client of the result service, while started.
    """

    def start(self) -> Self:
        """
        :return: This listener, following the action's status.
        """
        self.client = self.node.create_client(
            Query.Impl.GetResultService, self.action_name + RESULT_SERVICE_SUFFIX
        )
        self.subscription = self.node.create_subscription(
            GoalStatusArray,
            self.action_name + STATUS_TOPIC_SUFFIX,
            self.on_status,
            qos_profile_action_status_default,
        )
        return self

    def stop(self) -> None:
        """
        Stop following the action.
        """
        if self.subscription is not None:
            self.node.destroy_subscription(self.subscription)
        if self.client is not None:
            self.node.destroy_client(self.client)
        self.subscription = None
        self.client = None

    def on_status(self, message: GoalStatusArray) -> None:
        """
        Note every query not seen before, and fetch the answer of every one that ended.

        :param message: The states of the goals the action holds.
        """
        for status in message.status_list:
            key = bytes(status.goal_info.goal_id.uuid)
            with self.changed:
                answer = self.answers.get(key)
                if answer is None:
                    stamp = status.goal_info.stamp
                    answer = QueryAnswer(
                        accepted=stamp.sec + stamp.nanosec * NANOSECONDS
                    )
                    self.answers[key] = answer
                if answer.ended or answer.fetching or status.status not in ENDED_STATES:
                    continue
                if status.status not in ANSWERED_STATES:
                    answer.ended = True
                    self.changed.notify_all()
                    continue
                # fetched once: a goal that ended stays ended in every later status
                answer.fetching = True
            request = Query.Impl.GetResultService.Request()
            request.goal_id = status.goal_info.goal_id
            self.client.call_async(request).add_done_callback(
                lambda future, answer=answer: self.received(answer, future)
            )

    def received(self, answer: QueryAnswer, future: Future) -> None:
        """
        :param answer: A query that ended.
        :param future: Its result.
        """
        response = future.result()
        with self.changed:
            if response is not None and response.status == GoalStatus.STATUS_SUCCEEDED:
                answer.objects = list(response.result.res)
            answer.ended = True
            self.changed.notify_all()

    def since(self, start: float, count: int, timeout: float) -> List[QueryAnswer]:
        """
        :param start: A time, in seconds since the epoch.
        :param count: How many queries were asked since.
        :param timeout: How long, in seconds, to wait for their answers to arrive.
        :return: The queries accepted since ``start``, in the order accepted, once
            ``count`` of them ended or the wait ran out.
        """
        deadline = time.monotonic() + timeout
        with self.changed:
            self.changed.wait_for(
                lambda: (
                    sum(1 for answer in self.asked_since(start) if answer.ended)
                    >= count
                ),
                max(0.0, deadline - time.monotonic()),
            )
            return self.asked_since(start)

    def asked_since(self, start: float) -> List[QueryAnswer]:
        """
        :param start: A time, in seconds since the epoch.
        :return: The queries accepted since, in the order accepted.
        """
        return sorted(
            (answer for answer in self.answers.values() if answer.accepted >= start),
            key=lambda answer: answer.accepted,
        )
