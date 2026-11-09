"""
Fixtures of the live publisher tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typing_extensions import Iterator, List

from cramera.http.messages import Header, HttpMethod, Request
from cramera.http.server import RequestAnswerer
from cramera.locations import DataLocations
from cramera_bridge.live.publisher import LivePublisher
from cramera_bridge.performing import TaskContext
from cramera_bridge.live.server import LiveServer
from cramera_bridge.visualization import PublishedModelChange, PublishedStateChange
from cramera_bridge.questions.workspace_classes import WorkspaceClassIndex

from ..dataset.live_worlds import SmallPlan
from ..dataset.worlds import TwoRobots

LIVE_DATASET = Path(__file__).parent.parent / "dataset" / "live"
"""
The request bodies and files of the live tests.
"""

LOCAL_HOST = "127.0.0.1:8765"
"""
The host a local page addresses the publisher with.
"""


@pytest.fixture
def locations(tmp_path: Path) -> DataLocations:
    """
    :return: Data locations in a temporary directory, with a shared scenes directory.
    """
    return DataLocations(data=tmp_path / "data", shared_scenes=tmp_path / "shared")


@pytest.fixture
def publisher(kitchen, locations) -> Iterator[LivePublisher]:
    """
    :return: A publisher attached to the kitchen world and told of its changes, taking a
        snapshot of every change outside a plan.
    """
    publisher = LivePublisher(
        locations=locations, class_index=WorkspaceClassIndex(), snapshot_interval=0.0
    )
    publisher.attach(kitchen.world)
    callbacks = [
        PublishedStateChange(_world=kitchen.world, publisher=publisher),
        PublishedModelChange(_world=kitchen.world, publisher=publisher),
    ]
    yield publisher
    for callback in callbacks:
        callback.stop()


@pytest.fixture
def small_plan(robot_world) -> SmallPlan:
    """
    :return: A plan of a root, an action and the motion raising the PR2's torso, none
        of them started.
    """
    return SmallPlan.raising_the_torso(
        TaskContext(robot_world.world, robot_world.robot)
    )


@pytest.fixture
def two_robots() -> TwoRobots:
    """
    :return: Two robots in a world of their own, to perform a plan each.
    """
    return TwoRobots.build()


@pytest.fixture
def robot_plans(two_robots) -> List[SmallPlan]:
    """
    :return: A small plan for each of the two robots, each waiting a tick.
    """
    return [SmallPlan.waiting(two_robots.context(robot)) for robot in two_robots.robots]


@pytest.fixture
def answerer(publisher) -> RequestAnswerer:
    """
    :return: The answerer of the publisher's routes.
    """
    return LiveServer(publisher).answerer()


def live_request(
    method: HttpMethod, path: str, body: bytes = b"", query: str = "", **headers: str
) -> Request:
    """
    :param method: The method.
    :param path: The route.
    :param body: The body.
    :param query: The query without ``?``.
    :param headers: Further headers.
    :return: A request a local page sends.
    """
    return Request(
        method=method,
        path=path,
        query=query,
        headers={Header.HOST.value: LOCAL_HOST, **headers},
        body=body,
    )


def dataset_body(name: str) -> bytes:
    """
    :param name: A file of the live dataset.
    :return: Its bytes.
    """
    return (LIVE_DATASET / name).read_bytes()
