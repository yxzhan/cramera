"""
The images of what a perception step saw: the camera view rendered from the world model,
and how a step's images reach the live plan, its route and a recording.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np
import pytest
from typing_extensions import List, Optional, Sequence

from cramera_bridge.exceptions import UnknownPerceptionImage
from cramera_bridge.live.bundles import LiveBundleWriter, PlanTrees
from cramera_bridge.live.perception_images import (
    CAMERA_RESOLUTION,
    PERCEIVED_OUTLINE,
    PerceptionCamera,
    RawImage,
    SeenImage,
    WorldModelCamera,
)
from cramera_bridge.performing import TaskContext
from cramera_protocol.bundle.scene import PerceptionImageSource
from cramph.data_types import LifeCycleValues
from semantic_digital_twin.datastructures.field_of_view import FieldOfView
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.exceptions import MissingDefaultCameraError
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import FixedConnection
from semantic_digital_twin.world_description.geometry import Box, Color, Scale
from semantic_digital_twin.world_description.shape_collection import ShapeCollection
from semantic_digital_twin.world_description.world_entity import Body

from ..dataset.live_worlds import SmallPlan
from .test_perception import SeenQuery, detecting

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
"""
The first bytes of every PNG file.
"""


@dataclass
class ForwardCamera:
    """
    Stands in for a robot's camera: it looks along the world's x axis from the root.
    """

    world_T_forward_view: HomogeneousTransformationMatrix
    """
    The camera's pose, its x axis along the direction it looks.
    """

    field_of_view: FieldOfView = field(default_factory=FieldOfView)
    """
    What the camera sees.
    """


@dataclass
class CameraRobot:
    """
    Stands in for a robot with a default camera, or without one.
    """

    camera: Optional[ForwardCamera]
    """
    The robot's default camera, None for a robot without one.
    """

    name = "CameraRobot"
    """
    The robot's name, as the error of a missing camera names the robot.
    """

    def get_default_camera(self) -> ForwardCamera:
        """
        :return: The default camera.
        :raises MissingDefaultCameraError: When the robot has none.
        """
        if self.camera is None:
            raise MissingDefaultCameraError(type(self))
        return self.camera


@dataclass
class BoxWorld:
    """
    A world of one red box ahead of the root, above and to the left of its x axis.
    """

    world: World
    """
    The world.
    """

    box: Body
    """
    The box.
    """

    @classmethod
    def build(cls) -> BoxWorld:
        """
        :return: A new world.
        """
        world = World()
        root = Body(name=PrefixedName("map"))
        shapes = ShapeCollection(
            shapes=[
                Box(scale=Scale(0.2, 0.4, 0.4), color=Color.from_rgba(0.8, 0.1, 0.1))
            ]
        )
        box = Body(name=PrefixedName("box"), visual=shapes, collision=shapes)
        with world.modify_world():
            world.add_kinematic_structure_entity(root)
            world.add_connection(
                FixedConnection(
                    parent=root,
                    child=box,
                    parent_T_connection_expression=HomogeneousTransformationMatrix.from_xyz_rpy(
                        2.0, 0.8, 0.8
                    ),
                )
            )
        return cls(world=world, box=box)

    def robot(self) -> CameraRobot:
        """
        :return: A robot whose camera stands at the root.
        """
        return CameraRobot(
            ForwardCamera(
                HomogeneousTransformationMatrix.from_xyz_rpy(
                    0.0, 0.0, 0.0, reference_frame=self.world.root
                )
            )
        )


@dataclass
class FixedCamera(PerceptionCamera):
    """
    Takes the same image of every step, and counts the steps it saw start.
    """

    starts: int = 0
    """
    How many perception steps started.
    """

    def observe_started(self) -> None:
        self.starts += 1

    def images(
        self, world: World, robot: object, perceived: Sequence[Body]
    ) -> List[SeenImage]:
        return [SeenImage("Annotator", PerceptionImageSource.ROBOKUDO, PNG_SIGNATURE)]


@pytest.fixture
def boxes() -> BoxWorld:
    """
    :return: A new world of one box.
    """
    return BoxWorld.build()


def png_size(png: bytes) -> tuple:
    """
    :param png: A PNG file.
    :return: Its width and height.
    """
    assert png.startswith(PNG_SIGNATURE)
    return struct.unpack(">II", png[16:24])


# %% camera view


def test_a_body_above_and_left_of_the_camera_is_seen_in_the_top_left(boxes):
    bodies, depth, _, _ = WorldModelCamera().trace(
        boxes.world, boxes.robot().get_default_camera()
    )
    rows, columns = np.nonzero(bodies == boxes.box.index)
    height, width = bodies.shape
    assert (height, width) == (CAMERA_RESOLUTION.height, CAMERA_RESOLUTION.width)
    assert rows.max() < height / 2 and columns.max() < width / 2
    nearest_corner = np.linalg.norm([1.9, 0.6, 0.6])
    assert depth[rows, columns].min() == pytest.approx(nearest_corner, abs=0.05)


def test_the_camera_view_outlines_what_the_step_reported(boxes):
    camera = WorldModelCamera()
    reported = camera.images(boxes.world, boxes.robot(), [boxes.box])
    unreported = camera.images(boxes.world, boxes.robot(), [])
    assert [image.title for image in reported] == ["Camera", "Depth"]
    hits = camera.trace(boxes.world, boxes.robot().get_default_camera())
    view = camera.shaded(boxes.world, hits[0], hits[2], hits[3], {boxes.box})
    plain = camera.shaded(boxes.world, hits[0], hits[2], hits[3], set())
    outline = np.all(np.isclose(view, PERCEIVED_OUTLINE), axis=2)
    assert outline.any()
    assert not np.all(np.isclose(plain, PERCEIVED_OUTLINE), axis=2).any()
    assert reported[0].png != unreported[0].png


def test_the_images_are_png_files_the_camera_s_size(boxes):
    images = WorldModelCamera().images(boxes.world, boxes.robot(), [boxes.box])
    assert {png_size(image.png) for image in images} == {
        (CAMERA_RESOLUTION.width, CAMERA_RESOLUTION.height)
    }
    assert {image.source for image in images} == {PerceptionImageSource.CAMERA}


def test_a_robot_without_a_default_camera_takes_no_images(boxes):
    assert WorldModelCamera().images(boxes.world, CameraRobot(None), []) == []


def test_a_camera_that_sees_nothing_takes_no_images(boxes):
    away = CameraRobot(
        ForwardCamera(
            HomogeneousTransformationMatrix.from_xyz_rpy(
                yaw=np.pi, reference_frame=boxes.world.root
            )
        )
    )
    assert WorldModelCamera().images(boxes.world, away, []) == []


# %% the plan, its route and the recording


@pytest.fixture
def camera(publisher) -> FixedCamera:
    """
    :return: The camera the publisher takes every perception step's images with.
    """
    fixed = FixedCamera()
    publisher.plan.images.cameras = [fixed]
    return fixed


@pytest.fixture
def detection(kitchen, robot_world) -> SmallPlan:
    """
    :return: A plan that detects, whose perception step is answered by a robot.
    """
    query = SeenQuery([kitchen.milk], world=kitchen.world, robot=CameraRobot(None))
    return detecting(TaskContext(robot_world.world, robot_world.robot), query)


def ended(publisher, detection: SmallPlan, life_cycle: LifeCycleValues):
    """
    Run a perception step to its end, and publish the plan again as the tick it ended in
    does.

    :return: The step as the plan publishes it.
    """
    publisher.begin_plan(detection.executor)
    publisher.observe_node_started(detection.motion)
    detection.set(detection.motion, life_cycle)
    publisher.observe_node_ended(detection.motion)
    return publisher.refresh_plan().nodes[-1]


def test_a_perception_step_lists_its_images_once_it_ended(publisher, camera, detection):
    publisher.begin_plan(detection.executor)
    assert publisher.current_plan().nodes[-1].images == []
    step = ended(publisher, detection, LifeCycleValues.SUCCEEDED)
    (image,) = step.images
    assert (image.title, image.source) == ("Annotator", PerceptionImageSource.ROBOKUDO)
    assert camera.starts == 1
    assert publisher.perception_image(image.file).image.png == PNG_SIGNATURE
    assert publisher.current_plan().nodes[0].images is None


def test_a_failed_perception_step_still_shows_what_it_saw(publisher, camera, detection):
    step = ended(publisher, detection, LifeCycleValues.FAILED)
    assert step.perceived == []
    assert len(step.images) == 1


def test_an_image_no_step_took_is_not_found(publisher, camera):
    with pytest.raises(UnknownPerceptionImage):
        publisher.perception_image("plan_node_9_0")


def test_the_recording_keeps_the_images_as_files(
    publisher, camera, detection, tmp_path
):
    ended(publisher, detection, LifeCycleValues.SUCCEEDED)
    (plan,) = publisher.plan.performed_plans()
    LiveBundleWriter.perception_images([plan], tmp_path)
    (root,) = PlanTrees(plan=publisher.current_plan(), robot=None).roots()
    (action,) = root.children
    (step,) = action.children
    (image,) = step.images
    assert image.file.startswith("perception/") and image.file.endswith(".png")
    assert (tmp_path / image.file).read_bytes() == PNG_SIGNATURE
    assert (root.images, action.images) == (None, None)


# %% images from ROS


def test_a_ros_image_is_read_in_red_green_blue_order():
    blue_first = RawImage("bgr8", width=1, height=1, step=3, data=bytes([10, 20, 30]))
    grey = RawImage("mono8", width=2, height=1, step=2, data=bytes([0, 255]))
    assert blue_first.rgb()[0, 0].tolist() == pytest.approx(
        [30 / 255, 20 / 255, 10 / 255]
    )
    assert grey.rgb()[0].tolist() == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]


def test_a_padded_row_is_cut_to_the_image_s_width():
    padded = RawImage("rgba8", width=1, height=2, step=8, data=bytes(range(16)))
    assert padded.rgb().shape == (2, 1, 3)
    assert padded.rgb()[1, 0].tolist() == pytest.approx([8 / 255, 9 / 255, 10 / 255])


def test_an_image_in_another_encoding_or_short_of_data_is_not_read():
    assert RawImage("32FC1", width=1, height=1, step=4, data=bytes(4)).rgb() is None
    assert RawImage("rgb8", width=2, height=2, step=6, data=bytes(6)).rgb() is None
