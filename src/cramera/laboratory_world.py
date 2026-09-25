"""Load the authored laboratory into independent CRAM planning worlds."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import Any

from cramera import paths
from semantic_digital_twin.adapters.urdf import URDFParser
from semantic_digital_twin.api import (
    BodySpecification,
    RobotSpecification,
    WorldSpecification,
)
from semantic_digital_twin.collision_checking.collision_rules import (
    AllowCollisionBetweenGroups,
)
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.spatial_types import HomogeneousTransformationMatrix, Pose
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import FixedConnection
from semantic_digital_twin.world_description.geometry import Box, Scale
from semantic_digital_twin.world_description.shape_collection import ShapeCollection


# %% bundle vocabulary
class LaboratoryAsset(StrEnum):
    """Authored scene files consumed by the simulation."""

    SCENE_NAME = "precision_lab"
    """Scene identifier resolved through CRAMERA's scene roots."""
    SCENE = "scene.json"
    """Manifest declaring the environment and independent objects."""
    SEMANTICS = "semantics.json"
    """Metric rack and tube metadata."""
    RESOURCES = "resources"
    """Runtime assets installed alongside the Python package."""


class LaboratoryBody(StrEnum):
    """Stable body names linking semantic actions to the authored scene."""

    CLEAR_TUBE = "tube_clear"
    """Initially empty tube occupying rack slot A1."""
    AMBER_TUBE = "tube_amber"
    """Tube containing the amber sample."""
    TEAL_TUBE = "tube_teal"
    """Tube containing the teal sample."""
    STOPPER = "stopper"
    """Freely movable tube stopper."""
    DRAWER = "laboratory_drawer"
    """Articulated sliding drawer below the worktop."""
    ROOM = "laboratory_room"
    """Fixed worktop and room visual body."""
    RACK = "laboratory_rack"
    """Tube support and insertion aperture collision body."""
    CABINET = "laboratory_cabinet"
    """Closed bench panels omitted from the original URDF collision model."""


class LaboratorySlot(StrEnum):
    """Rack slots named in the laboratory bundle."""

    A1 = "A1"
    """Front row, left column."""
    A2 = "A2"
    """Front row, middle column."""
    A3 = "A3"
    """Front row, right column."""
    B1 = "B1"
    """Rear row, left column."""
    B2 = "B2"
    """Rear row, middle column."""
    B3 = "B3"
    """Rear row, right column."""


class LaboratoryField(StrEnum):
    """Fields read from the external scene and semantic metadata schemas."""

    MODELS = "models"
    """Robot and fixed environment model entries."""
    ROBOT = "robot"
    """Whether a model describes a robot."""
    URDF = "urdf"
    """Path to a body's URDF description."""
    OBJECTS = "objects"
    """Independent scene objects."""
    KEY = "key"
    """Object identifier shared with the viewer."""
    SPAWN = "spawn"
    """Initial object position and quaternion."""
    LABORATORY = "laboratory"
    """Manual workbench configuration."""
    TUBES = "tubes"
    """Tubes and their initial rack slots."""
    SLOT = "slot"
    """Rack slot occupied by one tube."""
    RACK = "rack"
    """Rack geometry metadata."""
    SLOTS = "slots"
    """Named insertion poses in the rack."""
    ID = "id"
    """Identifier of a rack slot."""
    POSE = "pose"
    """Tube-bottom position and quaternion."""
    TUBE = "tube"
    """Geometry and grasp affordances shared by tubes."""
    HEIGHT = "height"
    """Full tube height above its root."""
    GRASP_HEIGHT = "graspHeight"
    """Authored grasp height above the tube bottom."""
    OUTER_RADIUS = "outerRadius"
    """Outer glass radius."""


@dataclass
class MissingLaboratoryBundle(FileNotFoundError):
    """The requested laboratory description is unavailable."""

    directory: Path
    """Directory expected to contain the authored scene."""

    def __str__(self) -> str:
        """Identify the missing bundle without hiding the filesystem location."""
        return f"Laboratory bundle not found: {self.directory}"


@dataclass(frozen=True)
class LaboratoryObject:
    """An independently movable object and its authored initial placement."""

    body_name: str
    """Name of the root body in the object's description."""
    description: Path
    """URDF path relative to the bundle directory."""
    pose: Pose
    """Initial pose in the laboratory world frame."""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LaboratoryObject:
        """Read a movable object's entry from the scene manifest."""
        return cls(
            body_name=data[LaboratoryField.KEY],
            description=Path(data[LaboratoryField.URDF]),
            pose=Pose.from_xyz_quaternion(*data[LaboratoryField.SPAWN]),
        )


@dataclass(frozen=True)
class LaboratoryDescription:
    """Scene files and metric affordances shared with the visual workbench."""

    environment: Path
    """Environment description relative to the bundle directory."""
    objects: list[LaboratoryObject]
    """Movable tube and stopper descriptions."""
    slots: dict[str, Pose]
    """Tube-bottom poses for all rack slots, in the world frame."""
    occupancy: dict[str, str]
    """Initial tube body name for each occupied slot."""
    grasp_height: float
    """Height of the intended grasp above the tube bottom, in meters."""
    tube_height: float
    """Tube height above its root frame, in meters."""
    tube_radius: float
    """Outer glass radius, in meters."""

    @classmethod
    def from_json(
        cls, scene: dict[str, Any], semantics: dict[str, Any]
    ) -> LaboratoryDescription:
        """Read the authored manifest and geometry metadata together."""
        [environment] = [
            model
            for model in scene[LaboratoryField.MODELS]
            if not model[LaboratoryField.ROBOT]
        ]
        tube = semantics[LaboratoryField.TUBE]
        return cls(
            environment=Path(environment[LaboratoryField.URDF]),
            objects=[
                LaboratoryObject.from_json(item)
                for item in scene[LaboratoryField.OBJECTS]
            ],
            slots={
                slot[LaboratoryField.ID]: Pose.from_xyz_quaternion(
                    *slot[LaboratoryField.POSE]
                )
                for slot in semantics[LaboratoryField.RACK][LaboratoryField.SLOTS]
            },
            occupancy={
                item[LaboratoryField.SLOT]: item[LaboratoryField.KEY]
                for item in scene[LaboratoryField.LABORATORY][LaboratoryField.TUBES]
            },
            grasp_height=tube[LaboratoryField.GRASP_HEIGHT],
            tube_height=tube[LaboratoryField.HEIGHT],
            tube_radius=tube[LaboratoryField.OUTER_RADIUS],
        )


# %% closed cabinet collisions
class CabinetPart(StrEnum):
    """Names of structural objects in the authored Blender bench."""

    SPLASHBACK = "Rear splashback"
    """Raised worktop rear edge."""
    PLINTH = "Recessed plinth"
    """Recessed cabinet base."""
    SIDE = "Cabinet side"
    """Outer cabinet side panel."""
    REAR = "Cabinet rear"
    """Rear cabinet panel."""
    CROSS_MEMBER = "Upper cross member"
    """Upper cabinet reinforcement below the worktop."""
    PARTITION = "Cabinet partition"
    """Internal vertical partition beside a cabinet bay."""
    SHELF = "Drawer lower shelf"
    """Horizontal shelf beneath the sliding drawer."""
    LOWER_DOOR = "Lower left cabinet"
    """Closed door beneath the drawer bay."""
    DOOR = "Cabinet door"
    """Closed center or right cabinet front."""


@dataclass(frozen=True)
class CabinetPanel:
    """One axis-aligned structural panel authored by ``build_bench`` in Blender."""

    part: CabinetPart
    """Blender object whose bounds define this panel."""
    origin: HomogeneousTransformationMatrix
    """Panel center in the laboratory frame."""
    size: Scale
    """Full panel dimensions before the visual bevel is applied."""

    def collision_shape(self) -> Box:
        """Preserve the authored panel bounds as a collision box."""
        return Box(origin=self.origin, scale=self.size)


@dataclass(frozen=True)
class CabinetStructure:
    """Structural panel bounds from ``cramera/tools/laboratory/build_blender.py``.

    Dimensions follow :meth:`LaboratoryBuilder.build_bench`. Drawer guides, handles,
    adjustable feet and room decoration are outside this collision coverage.
    """

    def panels(self) -> list[CabinetPanel]:
        """Describe closed panels individually, leaving the sliding drawer bay open."""
        panels = [
            CabinetPanel(
                CabinetPart.SPLASHBACK,
                HomogeneousTransformationMatrix.from_xyz_rpy(0, 0.352, 0.958),
                Scale(1.78, 0.025, 0.116),
            ),
            CabinetPanel(
                CabinetPart.PLINTH,
                HomogeneousTransformationMatrix.from_xyz_rpy(0, 0.027, 0.083),
                Scale(1.66, 0.59, 0.12),
            ),
            CabinetPanel(
                CabinetPart.REAR,
                HomogeneousTransformationMatrix.from_xyz_rpy(0, 0.33, 0.47),
                Scale(1.62, 0.023, 0.7),
            ),
            CabinetPanel(
                CabinetPart.CROSS_MEMBER,
                HomogeneousTransformationMatrix.from_xyz_rpy(0, 0.02, 0.813),
                Scale(1.62, 0.64, 0.05),
            ),
            CabinetPanel(
                CabinetPart.SHELF,
                HomogeneousTransformationMatrix.from_xyz_rpy(-0.52, 0.006, 0.597),
                Scale(0.558, 0.616, 0.018),
            ),
            CabinetPanel(
                CabinetPart.LOWER_DOOR,
                HomogeneousTransformationMatrix.from_xyz_rpy(-0.52, -0.313, 0.352),
                Scale(0.558, 0.023, 0.451),
            ),
            CabinetPanel(
                CabinetPart.DOOR,
                HomogeneousTransformationMatrix.from_xyz_rpy(0.005, -0.313, 0.469),
                Scale(0.436, 0.024, 0.681),
            ),
            CabinetPanel(
                CabinetPart.DOOR,
                HomogeneousTransformationMatrix.from_xyz_rpy(0.523, -0.313, 0.469),
                Scale(0.552, 0.024, 0.681),
            ),
        ]
        panels.extend(
            CabinetPanel(
                CabinetPart.SIDE,
                HomogeneousTransformationMatrix.from_xyz_rpy(x, 0.02, 0.468),
                Scale(0.026, 0.654, 0.684),
            )
            for x in (-0.81, 0.81)
        )
        panels.extend(
            CabinetPanel(
                CabinetPart.PARTITION,
                HomogeneousTransformationMatrix.from_xyz_rpy(x, 0.015, 0.464),
                Scale(0.021, 0.63, 0.675),
            )
            for x in (-0.225, 0.24)
        )
        return panels

    def populate(self, world: World) -> None:
        """Add fixed collision panels without duplicating the authored GLB visuals."""
        BodySpecification(
            name=LaboratoryBody.CABINET,
            shapes=ShapeCollection(
                shapes=[panel.collision_shape() for panel in self.panels()]
            ),
            visual_shapes=ShapeCollection(),
        ).spawn(world)


# %% reusable laboratory environment
@dataclass
class LaboratoryEnvironment:
    """Create CRAM worlds with the authored visuals, movable objects and drawer."""

    bundle_directory: Path = field(
        default_factory=lambda: LaboratoryEnvironment.resolve_directory()
    )
    """Directory containing the authored laboratory bundle."""
    allow_support_contacts: bool = field(default=False, kw_only=True)
    """Whether planning collision checks ignore initial tubes against their rack."""
    description: LaboratoryDescription = field(init=False)
    """Parsed scene and geometry metadata."""

    def __post_init__(self) -> None:
        """Resolve the supplied directory and read its authored descriptions."""
        self.bundle_directory = Path(self.bundle_directory).expanduser().resolve()
        if not (self.bundle_directory / LaboratoryAsset.SCENE).is_file():
            raise MissingLaboratoryBundle(self.bundle_directory)
        self.description = LaboratoryDescription.from_json(
            json.loads((self.bundle_directory / LaboratoryAsset.SCENE).read_text()),
            json.loads((self.bundle_directory / LaboratoryAsset.SEMANTICS).read_text()),
        )

    @staticmethod
    def resolve_directory() -> Path:
        """Locate the portable laboratory bundled with the installed package."""
        return (
            Path(__file__).resolve().parent
            / LaboratoryAsset.RESOURCES
            / LaboratoryAsset.SCENE_NAME
        )

    def slot_pose(self, slot: LaboratorySlot, *, world: World | None = None) -> Pose:
        """Return a fresh tube-bottom placement target in the supplied world's root frame.

        :param slot: Authored rack slot receiving the tube.
        :param world: Bind the target to this world's root when supplied.
        :return: Placement target independent of the stored initial metadata.
        """
        pose = self.description.slots[slot]
        return Pose.from_xyz_quaternion(
            *pose.to_position().to_np()[:3],
            *pose.to_quaternion().to_np(),
            reference_frame=world.root if world is not None else None,
        )

    @property
    def grasp_height(self) -> float:
        """Authored grasp offset above the tube's root frame."""
        return self.description.grasp_height

    @property
    def tube_height(self) -> float:
        """Authored height of each tube in meters."""
        return self.description.tube_height

    @property
    def tube_radius(self) -> float:
        """Authored outer tube radius in meters."""
        return self.description.tube_radius

    def create_world(self, *, robots: Sequence[RobotSpecification] = ()) -> World:
        """Create an independent laboratory world with any requested robots.

        :param robots: Robot descriptions and placements; empty creates only the lab.
        :return: A semantic digital twin usable by ordinary CRAM contexts and plans.
        """
        world = WorldSpecification(robots=list(robots)).to_domain_object()
        self.populate(world)
        return world

    def populate(self, world: World) -> None:
        """Load the fixed environment and freely movable objects into an existing world.

        :param world: World receiving the authored laboratory at its root origin.
        """
        environment = URDFParser.from_file(
            str(self.bundle_directory / self.description.environment), prefix=""
        ).parse()
        with world.modify_world():
            connection = FixedConnection.create_with_dofs(
                world=world,
                parent=world.root,
                child=environment.root,
                parent_T_connection_expression=HomogeneousTransformationMatrix(),
            )
            world.merge_world(environment, root_connection=connection)
        CabinetStructure().populate(world)
        for item in self.description.objects:
            object_world = URDFParser.from_file(
                str(self.bundle_directory / item.description), prefix=""
            ).parse()
            world.merge_world_at_pose(object_world, item.pose.to_homogeneous_matrix())
        if not self.allow_support_contacts:
            return
        with world.modify_world():
            world.collision_manager.add_ignore_collision_rule(
                AllowCollisionBetweenGroups(
                    body_group_a=[
                        world.get_body_by_name(name)
                        for name in self.description.occupancy.values()
                    ],
                    body_group_b=[world.get_body_by_name(LaboratoryBody.RACK)],
                )
            )


# %% PR2 transfer compatibility
@dataclass
class LaboratoryWorld(LaboratoryEnvironment):
    """Build the PR2 transfer setup with its initial poses and support contact policy."""

    bundle_directory: Path = field(
        default_factory=lambda: LaboratoryWorld.resolve_directory()
    )
    """Authored bundle, preferring the user's local scene when available."""
    allow_support_contacts: bool = field(default=True, kw_only=True)
    """Allow the transfer's intended tube-to-rack support contacts during planning."""
    source_slot: LaboratorySlot = LaboratorySlot.A1
    """Initially occupied rack slot from which the robot picks a tube."""
    target_slot: LaboratorySlot = LaboratorySlot.A3
    """Rack slot receiving the transported tube."""
    base_pose: Pose = field(
        default_factory=lambda: Pose.from_xyz_rpy(x=0.40, y=-0.72, yaw=math.pi / 2)
    )
    """Initial PR2 base placement facing the bench from its front edge."""

    @staticmethod
    def resolve_directory() -> Path:
        """Prefer the locally authored scene and fall back to the packaged laboratory."""
        directory = paths.resolve_scene_directory(LaboratoryAsset.SCENE_NAME)
        return (
            directory
            if directory is not None
            else LaboratoryEnvironment.resolve_directory()
        )

    @property
    def source_body_name(self) -> str:
        """Body initially occupying the selected source rack slot."""
        return self.description.occupancy[self.source_slot]

    @property
    def source_pose(self) -> Pose:
        """Initial tube-bottom pose in the world frame, with no bound world object."""
        return self.slot_pose(self.source_slot)

    @property
    def target_pose(self) -> Pose:
        """Destination tube-bottom pose in the world frame, with no bound world object."""
        return self.slot_pose(self.target_slot)

    def build(self) -> World:
        """Spawn the PR2 and populate its world with independent laboratory bodies."""
        return self.create_world(
            robots=[
                RobotSpecification(
                    semantic_annotation_type=PR2,
                    odom_T_robot_start=self.base_pose.to_homogeneous_matrix(),
                )
            ]
        )
