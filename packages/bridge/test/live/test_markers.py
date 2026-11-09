"""
Debug markers kept the way RViz keeps them, posed in the world, and their topics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from http import HTTPStatus

import pytest
from typing_extensions import List

from cramera.http.messages import HttpMethod
from cramera_bridge.live.marker_topics import (
    DefaultMarkerTopic,
    MarkerEnvironmentVariable,
    MarkerTopicConfiguration,
    MarkerTopicSource,
)
from cramera_bridge.live.markers import (
    MarkerAction,
    MarkerBoard,
    MarkerStore,
    MarkerType,
)
from cramera_bridge.live.publisher import LivePublisher
from cramera_bridge.live.server import LiveServer
from cramera_protocol.geometry import Pose
from cramera_protocol.live import Capability, LiveRoute
from cramera_protocol.live_messages import MarkerKind, MarkerTopicRequest, MarkerTopics

from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix

from ..dataset.live_worlds import (
    Header,
    MarkerColor,
    MarkerMessage,
    MarkerPose,
    Orientation,
    Vector,
)
from .conftest import dataset_body, live_request
from .test_snapshot_pacing import ManualClock

TOPIC = DefaultMarkerTopic.SEMANTIC_WORLD.value
"""
The topic the test markers arrive on.
"""

# %% one topic


def test_markers_are_added_replaced_and_deleted():
    store = MarkerStore()
    assert store.observe([MarkerMessage(id=1), MarkerMessage(id=2)]) is True
    assert store.observe([MarkerMessage(id=1)]) is False
    assert store.observe([MarkerMessage(id=1, action=MarkerAction.DELETE)]) is True
    assert list(store.entries) == [("points", 2)]
    assert store.observe([MarkerMessage(action=MarkerAction.DELETE_ALL)]) is True
    assert store.entries == {}
    assert store.revision == 3


def test_markers_the_viewer_cannot_draw_are_left_out():
    mesh_marker_type = max(MarkerType) + 1
    assert MarkerStore().observe([MarkerMessage(type=mesh_marker_type)]) is False


def test_a_marker_keeps_its_kind_colour_and_points():
    store = MarkerStore()
    store.observe(
        [MarkerMessage(type=MarkerType.LINE_LIST, points=[Vector(1, 2, 3)], text="a")]
    )
    entry = store.entries[("points", 0)]
    assert entry.kind is MarkerKind.LINE_LIST
    assert entry.color == "#ff0000"
    assert entry.points[0].z == 3


def test_a_marker_without_an_orientation_is_drawn_unrotated():
    store = MarkerStore()
    store.observe([MarkerMessage(pose=MarkerPose(orientation=Orientation(w=0.0)))])
    assert store.entries[("points", 0)].pose[3:] == (0.0, 0.0, 0.0, 1.0)


def test_a_marker_s_orientation_is_scaled_to_unit_length():
    store = MarkerStore()
    store.observe(
        [MarkerMessage(pose=MarkerPose(orientation=Orientation(z=2.0, w=0.0)))]
    )
    assert store.entries[("points", 0)].pose[3:] == (0.0, 0.0, 1.0, 0.0)


def test_points_keep_colours_of_their_own():
    store = MarkerStore()
    store.observe(
        [
            MarkerMessage(
                type=MarkerType.POINTS,
                points=[Vector(), Vector(1, 0, 0)],
                colors=[MarkerColor(), MarkerColor(r=0.0, g=1.0)],
            )
        ]
    )
    assert store.entries[("points", 0)].colors == ("#ff0000", "#00ff00")


# %% posed in the world


def test_a_marker_in_a_body_frame_moves_with_the_body(kitchen):
    board = MarkerBoard()
    board.observe(TOPIC, [MarkerMessage(header=Header(frame_id="milk"))])
    assert board.refresh(kitchen.world) is True
    marker = board.current().markers[0]
    assert marker.pose == Pose(1.0, 0.1, 0.9, 0.0, 0.0, 0.0, 1.0)
    assert marker.topic == TOPIC


def test_a_marker_in_the_world_frame_keeps_its_pose(kitchen):
    board = MarkerBoard()
    board.observe(
        TOPIC, [MarkerMessage(pose=MarkerPose(position=Vector(0.5, 0.0, 0.0)))]
    )
    board.refresh(kitchen.world)
    assert board.current().markers[0].pose.x == 0.5


UNKNOWN_FRAME = "no_such_frame"
"""
A frame no body of the test world is named.
"""


def test_a_marker_in_an_unknown_frame_is_left_out(kitchen):
    board = MarkerBoard()
    board.observe(TOPIC, [MarkerMessage(header=Header(frame_id=UNKNOWN_FRAME))])
    board.refresh(kitchen.world)
    assert board.current().markers == []


def test_markers_drawing_world_bodies_are_left_out(kitchen):
    board = MarkerBoard()
    board.observe(TOPIC, [MarkerMessage(ns=str(kitchen.milk.name))])
    board.refresh(kitchen.world)
    assert board.current().markers == []


def test_the_overlay_is_rebuilt_only_after_a_change(kitchen):
    board = MarkerBoard()
    board.observe(TOPIC, [MarkerMessage()])
    board.refresh(kitchen.world)
    assert board.refresh(kitchen.world) is False
    board.drop(TOPIC)
    assert board.refresh(kitchen.world) is True
    assert board.current().markers == []
    assert board.version() == 2


def move_milk(kitchen, x: float) -> None:
    """
    Put the milk at another place along the table.
    """
    kitchen.milk_connection.origin = HomogeneousTransformationMatrix.from_xyz_rpy(
        x, 0.1, 0.9, reference_frame=kitchen.root
    )


def test_a_marker_in_a_body_frame_follows_the_body_without_a_new_overlay(kitchen):
    board = MarkerBoard()
    board.observe(
        TOPIC,
        [MarkerMessage(id=0), MarkerMessage(id=1, header=Header(frame_id="milk"))],
    )
    board.refresh(kitchen.world)
    move_milk(kitchen, 1.5)
    assert board.refresh(kitchen.world) is False
    version, poses = board.placed(kitchen.world)
    assert version == 1
    assert list(poses) == ["1"]
    assert poses["1"].x == 1.5


def test_markers_in_the_world_frame_have_no_pose_of_their_own_in_the_state(kitchen):
    board = MarkerBoard()
    board.observe(TOPIC, [MarkerMessage()])
    board.refresh(kitchen.world)
    assert board.placed(kitchen.world) == (1, {})


# %% in the state


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def resting(kitchen, locations, clock) -> LivePublisher:
    """
    :return: A publisher of the kitchen world, which nothing moves.
    """
    publisher = LivePublisher(locations=locations, clock=clock)
    publisher.attach(kitchen.world)
    clock.advance()
    return publisher


def test_markers_arriving_while_the_world_rests_reach_the_state(resting):
    before = resting.current_state().markers_version
    resting.markers.observe(TOPIC, [MarkerMessage()])
    assert resting.state.state().markers_version == before + 1
    assert len(resting.marker_overlay().markers) == 1


def test_the_last_of_a_burst_of_markers_reaches_the_state_once_it_is_read(
    resting, clock
):
    resting.markers.observe(TOPIC, [MarkerMessage(id=1)])
    resting.markers.observe(TOPIC, [MarkerMessage(id=2)])
    assert len(resting.marker_overlay().markers) == 1
    clock.advance()
    resting.current_state()
    assert len(resting.marker_overlay().markers) == 2


def test_unchanged_markers_take_no_snapshot(resting):
    resting.markers.observe(TOPIC, [MarkerMessage()])
    frames = resting.recording.frame_count()
    resting.markers.observe(TOPIC, [MarkerMessage()])
    assert resting.recording.frame_count() == frames


def test_the_state_carries_where_markers_following_a_body_are(resting, kitchen, clock):
    resting.markers.observe(TOPIC, [MarkerMessage(header=Header(frame_id="milk"))])
    move_milk(kitchen, 1.5)
    clock.advance()
    resting.observe_state_change()
    state = resting.current_state()
    assert state.marker_poses["0"].x == 1.5
    assert state.markers_version == 1


def test_a_dropped_topic_takes_its_markers_out_of_the_state(resting, clock):
    resting.markers.observe(TOPIC, [MarkerMessage()])
    clock.advance()
    resting.markers.drop(TOPIC)
    assert resting.marker_overlay().markers == []


# %% topics


@dataclass
class RecordedTopics(MarkerTopicSource):
    """
    A topic source that remembers what it was told, like a ROS listener.
    """

    topics_read: List[str] = field(default_factory=list)
    """
    The topics it was told to read.
    """

    stopped: bool = False
    """
    Whether it was stopped.
    """

    def subscribe(self, topic: str) -> None:
        if topic not in self.topics_read:
            self.topics_read.append(topic)

    def unsubscribe(self, topic: str) -> None:
        self.topics_read.remove(topic)

    def subscribed_topics(self) -> List[str]:
        return sorted(self.topics_read)

    def available_topics(self) -> List[str]:
        return [DefaultMarkerTopic.CORAPLEX.value]

    def stop(self) -> None:
        self.stopped = True


def test_topics_are_read_and_dropped_through_the_routes(kitchen, locations):
    source = RecordedTopics()
    source.subscribe(TOPIC)
    publisher = LivePublisher(locations=locations, marker_source=source)
    publisher.attach(kitchen.world)
    answerer = LiveServer(publisher).answerer()
    answer = answerer.answer(
        live_request(
            HttpMethod.POST, LiveRoute.MARKER_TOPICS, dataset_body("marker_topic.json")
        )
    )
    topics = MarkerTopics.from_json(json.loads(answer.body))
    assert answer.status is HTTPStatus.OK
    assert topics.subscribed == []
    assert topics.available == [DefaultMarkerTopic.CORAPLEX.value]
    publisher.change_marker_topic(MarkerTopicRequest(topic=TOPIC))
    assert publisher.marker_topics().subscribed == [TOPIC]
    assert Capability.MARKERS in publisher.info().capabilities


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("", [topic.value for topic in DefaultMarkerTopic]),
        (" /a, ,/b ", ["/a", "/b"]),
    ],
)
def test_the_topics_read_at_start_come_from_the_environment(configured, expected):
    configuration = MarkerTopicConfiguration(
        {MarkerEnvironmentVariable.TOPICS: configured}
    )
    assert configuration.topics() == expected
