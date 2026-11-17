"""
Images of what a perception step saw: the robot's camera view rendered from the world
model, and the images a RoboKudo pipeline published while it answered the step.
"""

from __future__ import annotations

import struct
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from typing_extensions import Dict, List, Optional, Sequence, Set, Tuple

from cramera_protocol.bundle.scene import PerceptionImage, PerceptionImageSource
from semantic_digital_twin.datastructures.camera_resolution import CameraResolution
from semantic_digital_twin.exceptions import MissingDefaultCameraError
from semantic_digital_twin.robots.robot_parts import AbstractRobot, Camera
from semantic_digital_twin.spatial_computations.raytracer import RayTracer
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

PERCEPTION_DIRECTORY = "perception"
"""
The directory of a bundle that holds the images of its perception steps.
"""

PNG_SUFFIX = ".png"
"""
The suffix of an image's file.
"""

CAMERA_RESOLUTION = CameraResolution(width=320, height=240)
"""
The size of a rendered camera view, small enough to render while the plan waits.
"""

NEAREST_SURFACE = 0.05
"""
How close to the camera, in metres, a surface may be and still count as seen; nearer
ones belong to the camera's own housing.
"""

FARTHEST_SURFACE = 10.0
"""
How far from the camera, in metres, a rendered ray reaches.
"""

AMBIENT_LIGHT = 0.35
"""
The share of a surface's color it shows when it faces away from the camera.
"""

BACKGROUND = (0.13, 0.15, 0.18)
"""
The color of a pixel whose ray hits nothing.
"""

OUTLINE = (0.05, 0.05, 0.06)
"""
The color of the line between two bodies in the camera view.
"""

PERCEIVED_OUTLINE = (1.0, 0.69, 0.0)
"""
The color of the line around an object the step reported.
"""

PERCEIVED_TINT = 0.25
"""
How strongly an object the step reported is tinted with :data:`PERCEIVED_OUTLINE`.
"""

DEPTH_COLORS = np.array(
    [
        [0.99, 0.91, 0.15],
        [0.35, 0.78, 0.39],
        [0.13, 0.57, 0.55],
        [0.23, 0.32, 0.55],
        [0.27, 0.0, 0.33],
    ]
)
"""
The colors of the depth view, from the nearest surface seen to the farthest.
"""

CAMERA_TITLE = "Camera"
"""
The title of the rendered camera view.
"""

DEPTH_TITLE = "Depth"
"""
The title of the rendered depth view.
"""

# %% images


@dataclass(frozen=True)
class SeenImage:
    """
    One image a source took of what a perception step saw.
    """

    title: str
    """
    What the image shows.
    """

    source: PerceptionImageSource
    """
    Where it comes from.
    """

    png: bytes
    """
    The image as a PNG file.
    """


@dataclass(frozen=True)
class TakenImage:
    """
    An image of one perception step, with the key it is served and written under.
    """

    key: str
    """
    The image's key: the step's identity and the image's place among its images.
    """

    image: SeenImage
    """
    The image.
    """

    def document(self, file: str) -> PerceptionImage:
        """
        :param file: Where the image is: its key in a live plan, its path in a bundle.
        :return: The image as the protocol describes it.
        """
        return PerceptionImage(
            title=self.image.title, source=self.image.source, file=file
        )

    @property
    def bundle_file(self) -> str:
        """
        The image's path in a bundle.
        """
        return bundle_file(self.key)


def bundle_file(key: str) -> str:
    """
    :param key: An image's key.
    :return: The path a bundle keeps the image at.
    """
    return f"{PERCEPTION_DIRECTORY}/{key}{PNG_SUFFIX}"


class PngImage:
    """
    Writes an image as a PNG file.
    """

    @staticmethod
    def of_rgb(pixels: np.ndarray) -> bytes:
        """
        :param pixels: The image, rows from the top, as height × width × 3 values
            between 0 and 1.
        :return: The image as an 8-bit RGB PNG file.
        """
        data = (np.clip(pixels, 0.0, 1.0) * 255).round().astype(np.uint8)
        height, width, _ = data.shape
        rows = b"".join(b"\x00" + data[row].tobytes() for row in range(height))
        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return b"".join(
            [
                b"\x89PNG\r\n\x1a\n",
                PngImage.chunk(b"IHDR", header),
                PngImage.chunk(b"IDAT", zlib.compress(rows)),
                PngImage.chunk(b"IEND", b""),
            ]
        )

    @staticmethod
    def chunk(kind: bytes, data: bytes) -> bytes:
        """
        :param kind: The chunk's type.
        :param data: Its data.
        :return: The chunk with its length and checksum.
        """
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )


class ImageEncoding(StrEnum):
    """
    The pixel encodings of a ROS image a RoboKudo pipeline publishes that are read.
    """

    RGB8 = "rgb8"
    BGR8 = "bgr8"
    RGBA8 = "rgba8"
    BGRA8 = "bgra8"
    MONO8 = "mono8"


CHANNEL_ORDERS = {
    ImageEncoding.RGB8: (0, 1, 2),
    ImageEncoding.BGR8: (2, 1, 0),
    ImageEncoding.RGBA8: (0, 1, 2),
    ImageEncoding.BGRA8: (2, 1, 0),
    ImageEncoding.MONO8: (0, 0, 0),
}
"""
Which of an encoding's channels hold red, green and blue.
"""

CHANNEL_COUNTS = {
    ImageEncoding.RGB8: 3,
    ImageEncoding.BGR8: 3,
    ImageEncoding.RGBA8: 4,
    ImageEncoding.BGRA8: 4,
    ImageEncoding.MONO8: 1,
}
"""
How many bytes an encoding's pixel takes.
"""


@dataclass(frozen=True)
class RawImage:
    """
    The pixels of an image as a ROS image message carries them.
    """

    encoding: str
    """
    The pixel encoding, such as ``bgr8``.
    """

    width: int
    """
    The image's width in pixels.
    """

    height: int
    """
    The image's height in pixels.
    """

    step: int
    """
    How many bytes a row takes.
    """

    data: bytes
    """
    The rows, from the top.
    """

    def rgb(self) -> Optional[np.ndarray]:
        """
        :return: The image as height × width × 3 values between 0 and 1, or None for an
            encoding that is not read, or data that does not fill the image.
        """
        if self.encoding not in {encoding.value for encoding in ImageEncoding}:
            return None
        encoding = ImageEncoding(self.encoding)
        channels = CHANNEL_COUNTS[encoding]
        if (
            self.step < self.width * channels
            or len(self.data) < self.step * self.height
        ):
            return None
        rows = np.frombuffer(self.data, dtype=np.uint8)[: self.step * self.height]
        pixels = rows.reshape(self.height, self.step)[:, : self.width * channels]
        pixels = pixels.reshape(self.height, self.width, channels)
        return pixels[..., list(CHANNEL_ORDERS[encoding])] / 255.0


# %% sources


class PerceptionCamera(ABC):
    """
    Takes images of what a perception step saw.
    """

    def observe_started(self) -> None:
        """
        Note that a perception step started, so only what it saw is taken when it ends.
        """

    @abstractmethod
    def images(
        self, world: World, robot: AbstractRobot, perceived: Sequence[Body]
    ) -> List[SeenImage]:
        """
        :param world: The world the step ran in.
        :param robot: The robot that perceived.
        :param perceived: The bodies the step reported.
        :return: The images of what the step saw, empty when the source saw nothing.
        """

    def stop(self) -> None:
        """
        Release what the source holds.
        """


@dataclass
class WorldModelCamera(PerceptionCamera):
    """
    Renders what the robot's default camera sees of the world model, as in simulation:
    the bodies in their own colors, shaded by how they face the camera, with the objects
    the step reported outlined, and the distance to every surface seen.

    It traces the bodies' collision shapes, as the check whether the camera sees an
    object does, so a body without any is not drawn.
    """

    tracer: Optional[RayTracer] = field(default=None, init=False)
    """
    The ray tracer of the world rendered last, kept because building one reads every
    body.
    """

    def images(
        self, world: World, robot: AbstractRobot, perceived: Sequence[Body]
    ) -> List[SeenImage]:
        try:
            camera = robot.get_default_camera()
        except MissingDefaultCameraError:
            return []
        hits = self.trace(world, camera)
        if hits is None:
            return []
        bodies, depth, normals, directions = hits
        return [
            SeenImage(
                CAMERA_TITLE,
                PerceptionImageSource.CAMERA,
                PngImage.of_rgb(
                    self.shaded(world, bodies, normals, directions, set(perceived))
                ),
            ),
            SeenImage(
                DEPTH_TITLE,
                PerceptionImageSource.CAMERA,
                PngImage.of_rgb(self.depth_view(depth)),
            ),
        ]

    def trace(
        self, world: World, camera: Camera
    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """
        Cast one ray through every pixel of the camera and keep the nearest surface it
        hits beyond the camera's housing.

        :param world: The world.
        :param camera: The camera.
        :return: Per pixel, rows from the top: the index of the body hit (-1 for none),
            the distance along the ray (-1 for none), the surface's normal and the ray's
            direction; None when the camera sees nothing at all.
        """
        if self.tracer is None or self.tracer.world is not world:
            self.tracer = RayTracer(world)
        camera_rays = self.tracer.create_camera_rays(
            camera.world_T_forward_view,
            resolution=CAMERA_RESOLUTION,
            field_of_view=camera.field_of_view,
        )
        origins = camera_rays.origin_points
        directions = camera_rays.direction_vectors
        pixels = camera_rays.pixel_coordinates
        intersections = self.tracer.ray_test(
            origins,
            origins + directions * FARTHEST_SURFACE,
            multiple_hits=True,
            min_distance=NEAREST_SURFACE,
            max_distance=FARTHEST_SURFACE,
        )
        points, rays = intersections.hit_points, intersections.ray_indices
        hit_bodies = intersections.bodies
        if len(rays) == 0:
            return None
        distances = np.linalg.norm(points - origins[rays], axis=1)
        nearest = np.lexsort((distances, rays))
        first = nearest[np.unique(rays[nearest], return_index=True)[1]]
        width, height = CAMERA_RESOLUTION.width, CAMERA_RESOLUTION.height
        columns, rows = pixels[rays[first], 0], pixels[rays[first], 1]
        bodies = np.full((height, width), -1, dtype=np.int64)
        bodies[rows, columns] = [hit_bodies[index].index for index in first]
        depth = np.full((height, width), -1.0)
        depth[rows, columns] = distances[first]
        surface = np.zeros((height, width, 3))
        surface[rows, columns] = points[first]
        ray_directions = np.zeros((height, width, 3))
        ray_directions[pixels[:, 1], pixels[:, 0]] = directions
        return bodies, depth, self.normals(surface, depth >= 0), ray_directions

    @staticmethod
    def normals(surface: np.ndarray, seen: np.ndarray) -> np.ndarray:
        """
        :param surface: Per pixel, the point seen.
        :param seen: Per pixel, whether a point was seen.
        :return: Per pixel, the unit normal of the surface seen, estimated from its
            neighbours; zero where nothing or no surface around it was seen.
        """
        across = np.gradient(surface, axis=1)
        down = np.gradient(surface, axis=0)
        normals = np.cross(across, down)
        lengths = np.linalg.norm(normals, axis=2, keepdims=True)
        usable = (lengths[..., 0] > 0) & seen
        normals[usable] /= lengths[usable]
        normals[~usable] = 0.0
        return normals

    @staticmethod
    def color_of(body: Body) -> Tuple[float, float, float]:
        """
        :param body: A body.
        :return: The color of its first visual shape.
        """
        for shape in body.visual.shapes:
            return shape.color.R, shape.color.G, shape.color.B
        return 1.0, 1.0, 1.0

    def shaded(
        self,
        world: World,
        bodies: np.ndarray,
        normals: np.ndarray,
        directions: np.ndarray,
        perceived: Set[Body],
    ) -> np.ndarray:
        """
        :param world: The world.
        :param bodies: Per pixel, the index of the body seen.
        :param normals: Per pixel, the surface's normal.
        :param directions: Per pixel, the ray's direction.
        :param perceived: The bodies the step reported.
        :return: The camera view.
        """
        by_index = {body.index: body for body in world.bodies}
        seen = [index for index in np.unique(bodies) if index >= 0]
        colors = np.array(
            [[0.0, 0.0, 0.0]] + [self.color_of(by_index[index]) for index in seen]
        )
        lookup = np.zeros(bodies.max() + 2, dtype=np.int64)
        lookup[np.array(seen, dtype=np.int64) + 1] = np.arange(1, len(seen) + 1)
        base = colors[lookup[bodies + 1]]
        facing = np.abs(np.sum(normals * directions, axis=2))
        image = base * (AMBIENT_LIGHT + (1 - AMBIENT_LIGHT) * facing[..., None])
        image[bodies < 0] = BACKGROUND
        marked = np.isin(bodies, [body.index for body in perceived])
        image[marked] = (1 - PERCEIVED_TINT) * image[
            marked
        ] + PERCEIVED_TINT * np.array(PERCEIVED_OUTLINE)
        image[self.edges(bodies)] = OUTLINE
        image[self.edges(np.where(marked, 1, 0), width=2)] = PERCEIVED_OUTLINE
        return image

    @staticmethod
    def edges(labels: np.ndarray, width: int = 1) -> np.ndarray:
        """
        :param labels: Per pixel, a label.
        :param width: How many pixels the line is wide.
        :return: Per pixel, whether it lies on the line between two labels.
        """
        edges = np.zeros(labels.shape, dtype=bool)
        for shift in range(1, width + 1):
            edges[:, shift:] |= labels[:, shift:] != labels[:, :-shift]
            edges[shift:, :] |= labels[shift:, :] != labels[:-shift, :]
        return edges

    @staticmethod
    def depth_view(depth: np.ndarray) -> np.ndarray:
        """
        :param depth: Per pixel, the distance to the surface seen, -1 for none.
        :return: The depth view: near surfaces bright, far ones dark.
        """
        seen = depth >= 0
        image = np.zeros(depth.shape + (3,))
        image[:] = BACKGROUND
        if not seen.any():
            return image
        near, far = depth[seen].min(), depth[seen].max()
        scale = (depth[seen] - near) / max(far - near, 1e-6) * (len(DEPTH_COLORS) - 1)
        lower = np.floor(scale).astype(int).clip(0, len(DEPTH_COLORS) - 2)
        share = (scale - lower)[:, None]
        image[seen] = (1 - share) * DEPTH_COLORS[lower] + share * DEPTH_COLORS[
            lower + 1
        ]
        return image


# %% store


@dataclass
class PerceptionImages:
    """
    The images of every perception step, taken by every camera when the step ends.
    """

    cameras: List[PerceptionCamera] = field(
        default_factory=lambda: [WorldModelCamera()]
    )
    """
    The cameras asked for images, in the order their images are shown.
    """

    steps: Dict[str, List[TakenImage]] = field(default_factory=dict)
    """
    The images of every step that ended, by the step's identity.
    """

    def observe_started(self) -> None:
        """
        Note that a perception step started.
        """
        for camera in self.cameras:
            camera.observe_started()

    def observe_ended(
        self,
        identifier: str,
        world: World,
        robot: Optional[AbstractRobot],
        perceived: Sequence[Body],
    ) -> None:
        """
        Take the images of a perception step that ended.

        :param identifier: The step's identity.
        :param world: The world it ran in.
        :param robot: The robot that perceived, None when the plan names none.
        :param perceived: The bodies the step reported.
        """
        if robot is None:
            return
        seen = [
            image
            for camera in self.cameras
            for image in camera.images(world, robot, perceived)
        ]
        self.steps[identifier] = [
            TakenImage(key=f"{identifier}_{place}", image=image)
            for place, image in enumerate(seen)
        ]

    def of_step(self, identifier: str) -> List[TakenImage]:
        """
        :param identifier: A step's identity.
        :return: Its images, empty before it ended.
        """
        return list(self.steps.get(identifier, []))

    def image(self, key: str) -> Optional[TakenImage]:
        """
        :param key: An image's key.
        :return: The image, or None when no step took one under the key.
        """
        return next(
            (
                image
                for images in self.steps.values()
                for image in images
                if image.key == key
            ),
            None,
        )

    def stop(self) -> None:
        """
        Release what the cameras hold.
        """
        for camera in self.cameras:
            camera.stop()
