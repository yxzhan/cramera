"""
The cramera viewer's live visualization: publishes a world and the plans performed in it
to the viewer while a demo runs.

World changes reach the publisher through the world's own callbacks and a plan's
progress through an extension of the executor running it.
"""

from __future__ import annotations

import atexit
import importlib.util
import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import Any, Callable, Optional

from cramera.locations import DataLocations
from cramera_bridge.detections import DetectionRecorder
from cramera_bridge.live.marker_topics import MarkerTopicConfiguration
from cramera_bridge.live.publisher import LivePublisher
from cramera_bridge.live.server import LIVE_HOST, LIVE_PORT, LiveServer
from cramera_protocol.live_messages import RecordingState
from cramera_bridge.questions.live import WorldHighlightKeys
from cramera_bridge.questions.workspace_classes import WorkspaceClassIndex
from cramph.executor import Executor, StatechartExecutor, TickingExecutorExtension
from cramph.state_history import (
    StateHistory,
    StateHistoryItem,
    StateHistoryObserver,
)
from semantic_digital_twin.callbacks.callback import (
    ModelChangeCallback,
    StateChangeCallback,
)
from semantic_digital_twin.world import World

ROS_MODULES = ("rclpy", "visualization_msgs")
"""
The modules reading markers from ROS needs.
"""

ROS_IMAGE_MODULES = ("rclpy", "sensor_msgs")
"""
The modules reading RoboKudo's images from ROS needs.
"""


class DetectionEnvironmentVariable(StrEnum):
    """
    The environment variables configuring the detection of a run's events.
    """

    DETECT_EVENTS = "CRAMERA_DETECT_EVENTS"
    """
    Set to :data:`DETECTION_ENABLED` for segmind's detectors to watch every run a
    visualization serves, so its recording lists what they saw.
    """


DETECTION_ENABLED = "1"
"""
The value of :attr:`DetectionEnvironmentVariable.DETECT_EVENTS` that turns detection on.
"""

# %% world callbacks


@dataclass(eq=False)
class PublishedStateChange(StateChangeCallback):
    """
    Tells the publisher whenever the world's state changes, so it takes a snapshot once
    the state is settled.
    """

    publisher: LivePublisher = field(kw_only=True)
    """
    The publisher.
    """

    def on_state_change(self, **kwargs: Any) -> None:
        self.publisher.observe_state_change()


@dataclass(eq=False)
class PublishedModelChange(ModelChangeCallback):
    """
    Lets the publisher read the model again whenever the world's model changes.
    """

    publisher: LivePublisher = field(kw_only=True)
    """
    The publisher.
    """

    def on_model_change(self, **kwargs: Any) -> None:
        self.publisher.observe_model_change()


# %% plan progress


@dataclass
class PublishedPlanProgress(TickingExecutorExtension, StateHistoryObserver):
    """
    Publishes the plan an executor compiled, and after every tick the nodes that started
    and ended in it and the statechart's state.

    It also follows every state the plan's history records, which on the real robot is
    the only sign of progress: the motion server ticks the plan there, and the executor
    takes over the states it reports.
    """

    publisher: LivePublisher = field(kw_only=True)
    """
    The publisher.
    """

    reported: Optional[StateHistoryItem] = field(default=None, init=False)
    """
    The newest state of the statechart whose changes were published.
    """

    def after_compile(self, executor: Executor) -> None:
        """
        Publish the plan the executor compiled, and the nodes its first tick started,
        and follow every state its history records.
        """
        self.publisher.begin_plan(executor)
        executor.statechart.state_history.add_observer(self)
        self.report(executor)

    def after_recompile(self, executor: StatechartExecutor) -> None:
        """
        Publishes nothing again: the plan is read from the statechart, which keeps the
        nodes it held.
        """

    def after_tick(self, executor: StatechartExecutor) -> None:
        self.report(executor)

    def on_state_change(self, history: StateHistory) -> None:
        """
        Publish the nodes that started and ended in the state the history recorded, and
        the plan again when any did.
        """
        if self.report_nodes(history):
            with self.publisher.world_lock:
                self.publisher.refresh_plan()

    def after_run(self, executor: Executor) -> None:
        """
        Publish the state the plan left.
        """
        executor.statechart.state_history.remove_observer(self)
        self.report_nodes(executor.statechart.state_history)
        self.publisher.end_plan(executor)

    def report(self, executor: Executor) -> None:
        """
        Publish the nodes that started and ended since the state published last, then
        the statechart's state.

        :param executor: The executor running the plan.
        """
        statechart = executor.statechart
        changed = self.report_nodes(statechart.state_history)
        self.publisher.observe_tick(statechart, changed)

    def report_nodes(self, history: StateHistory) -> bool:
        """
        Publish the nodes that started and ended in the newest state the history
        recorded, unless it was published already.

        :param history: The history of the plan's statechart.
        :return: Whether any node started or ended.
        """
        moment = history.latest_item
        if moment is None or moment is self.reported:
            return False
        self.reported = moment
        changed = False
        for node in history.nodes_started_in_latest_item():
            self.publisher.observe_node_started(node)
            changed = True
        for node in history.nodes_ended_in_latest_item():
            self.publisher.observe_node_ended(node)
            changed = True
        return changed


# %% the backend


@dataclass
class LiveVisualization:
    """
    Serves a world to the cramera viewer while a demo runs, records the run and writes
    the recording when it stops.
    """

    world: World
    """
    The world served.
    """

    port: int = LIVE_PORT
    """
    The port of the live routes.
    """

    host: str = LIVE_HOST
    """
    The address the live routes listen on.
    """

    locations: DataLocations = field(
        default_factory=lambda: DataLocations.from_environment(
            dict(os.environ), Path.home()
        )
    )
    """
    Where bundles are written.
    """

    read_ros_markers: bool = True
    """
    Whether markers are read from ROS when ROS is installed.
    """

    read_robokudo_images: bool = True
    """
    Whether the images RoboKudo's pipelines publish are read from ROS when ROS is
    installed, to show with every perception step.
    """

    class_index: Optional[WorkspaceClassIndex] = None
    """
    The workspace classes queries may name; the installed CRAM packages when None.
    """

    detect_events: bool = field(
        default_factory=lambda: (
            os.environ.get(DetectionEnvironmentVariable.DETECT_EVENTS)
            == DETECTION_ENABLED
        )
    )
    """
    Whether segmind's detectors watch the run, so the viewer shows what they saw and the
    recording lists it; by default as the process environment says.
    """

    detections: Optional[DetectionRecorder] = field(default=None, init=False)
    """
    Segmind watching the run, once started with :attr:`detect_events`; it stops before
    the recording is written, so the recording keeps what it saw last, and stays
    readable after.
    """

    publisher: Optional[LivePublisher] = field(default=None, init=False)
    """
    The publisher, while started.
    """

    server: Optional[LiveServer] = field(default=None, init=False)
    """
    The HTTP server, while started.
    """

    resources: ExitStack = field(default_factory=ExitStack, init=False)
    """
    Releases what :meth:`start` acquired, in reverse order.
    """

    def start(self) -> LiveVisualization:
        """
        Attach to the world and start serving; a start that fails part-way releases what
        it acquired.

        :return: This visualization.
        """
        with ExitStack() as acquired:
            publisher = LivePublisher(
                locations=self.locations,
                class_index=(
                    WorkspaceClassIndex.installed()
                    if self.class_index is None
                    else self.class_index
                ),
            )
            if self.read_ros_markers and all(
                importlib.util.find_spec(module) is not None for module in ROS_MODULES
            ):
                publisher.marker_source = self.ros_marker_listener(publisher)
                acquired.callback(publisher.marker_source.stop)
            if self.read_robokudo_images and all(
                importlib.util.find_spec(module) is not None
                for module in ROS_IMAGE_MODULES
            ):
                listener = self.robokudo_image_listener()
                acquired.callback(listener.stop)
                publisher.plan.images.cameras.append(listener)
            publisher.attach(self.world)
            acquired.callback(self.finish_recording, publisher)
            if self.detect_events:
                self.detections = DetectionRecorder(
                    self.world, WorldHighlightKeys(publisher.current_objects)
                ).start()
                acquired.callback(self.detections.stop)
                publisher.detections = self.detections
            for callback_class in (PublishedStateChange, PublishedModelChange):
                callback = callback_class(_world=self.world, publisher=publisher)
                acquired.callback(callback.stop)
            acquired.callback(publisher.stop_teleoperation)
            server = LiveServer(publisher, host=self.host, port=self.port).start()
            acquired.callback(server.stop)
            self.publisher, self.server = publisher, server
            self.resources = acquired.pop_all()
        atexit.register(self.stop)
        return self

    @staticmethod
    def ros_marker_listener(publisher: LivePublisher) -> Any:
        """
        Start reading markers from ROS; imported here because ROS is optional.

        :param publisher: The publisher whose board receives the markers.
        :return: The started listener.
        """
        from cramera_bridge.live.ros_markers import RosMarkerListener

        return RosMarkerListener(publisher.markers).start(
            MarkerTopicConfiguration.of_process().topics()
        )

    @staticmethod
    def robokudo_image_listener() -> Any:
        """
        Start reading RoboKudo's images from ROS; imported here because ROS is optional.

        :return: The started listener.
        """
        from cramera_bridge.live.ros_perception_images import RoboKudoImageListener

        return RoboKudoImageListener().start()

    @staticmethod
    def finish_recording(publisher: LivePublisher) -> None:
        """
        Write a recording that holds frames before the visualization goes.

        :param publisher: The publisher.
        """
        status = publisher.recording_status()
        if status.state is RecordingState.RECORDING and status.frame_count > 0:
            publisher.finish_recording()

    def progress(self) -> PublishedPlanProgress:
        """
        :return: The extension publishing the plan of the executor it is added to.
        """
        return PublishedPlanProgress(publisher=self.publisher)

    def url(self) -> str:
        """
        :return: The base URL of the live routes.
        """
        return self.server.url()

    def when_stopped(self, callback: Callable[[], Any]) -> None:
        """
        :param callback: Called when the visualization stops, before what :meth:`start`
            acquired is released.
        """
        self.resources.callback(callback)

    def stop(self) -> None:
        """
        Write the recording and release the callbacks, the server, hand control, the
        marker listener and the detectors.
        """
        atexit.unregister(self.stop)
        self.resources.close()
        self.publisher = None
        self.server = None
