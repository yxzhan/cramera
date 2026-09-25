"""Load the packaged laboratory headlessly, optionally with a CRAM robot context."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from coraplex.datastructures.dataclasses import Context
from cramera.laboratory_world import LaboratoryEnvironment
from semantic_digital_twin.api import RobotSpecification
from semantic_digital_twin.robots.hsrb import HSRB
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.spatial_types import HomogeneousTransformationMatrix
from semantic_digital_twin.world import World
from typing_extensions import Sequence


# %% example configuration
class ExampleRobot(StrEnum):
    """Robot choices for the headless laboratory example."""

    NONE = "none"
    """Load only the environment and its movable objects."""
    PR2 = "pr2"
    """Add a PR2 mobile manipulator."""
    HSRB = "hsrb"
    """Add a Toyota HSR mobile manipulator."""


class ExampleArgument(StrEnum):
    """Command-line options accepted by the example."""

    ROBOT = "--robot"
    """Choose the robot added to the world."""
    BUNDLE_DIRECTORY = "--bundle-directory"
    """Read a custom authored laboratory bundle."""


@dataclass
class LoadedLaboratory:
    """A laboratory world and its optional robot planning context."""

    laboratory: LaboratoryEnvironment
    """Authored environment and rack metadata."""
    world: World
    """Independent world populated with the laboratory and selected robot."""
    context: Context | None
    """Native CRAM context when a robot was selected."""


@dataclass
class LaboratoryExample:
    """Construct a headless planning world from a reusable laboratory bundle."""

    robot: ExampleRobot = ExampleRobot.NONE
    """Optional mobile manipulator for the example."""
    bundle_directory: Path | None = None
    """Custom bundle directory; omitted to use the packaged laboratory."""
    odom_T_robot_start: HomogeneousTransformationMatrix = field(
        default_factory=lambda: HomogeneousTransformationMatrix.from_xyz_rpy(
            x=0.40, y=-0.72, yaw=math.pi / 2
        )
    )
    """Robot start pose in metres at the front of the laboratory bench."""

    def load(self) -> LoadedLaboratory:
        """Create the world and bind a selected robot to a native CRAM context."""
        laboratory = (
            LaboratoryEnvironment()
            if self.bundle_directory is None
            else LaboratoryEnvironment(bundle_directory=self.bundle_directory)
        )
        if self.robot is ExampleRobot.NONE:
            return LoadedLaboratory(laboratory, laboratory.create_world(), None)

        robot_type = PR2 if self.robot is ExampleRobot.PR2 else HSRB
        specification = RobotSpecification(
            semantic_annotation_type=robot_type,
            odom_T_robot_start=self.odom_T_robot_start,
        )
        world = laboratory.create_world(robots=[specification])
        [robot] = world.get_semantic_annotations_by_type(robot_type)
        return LoadedLaboratory(laboratory, world, Context(world=world, robot=robot))


# %% headless entry point
def main(arguments: Sequence[str] | None = None) -> None:
    """Print the loaded world's independently movable objects and rack slots."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        ExampleArgument.ROBOT,
        type=ExampleRobot,
        choices=list(ExampleRobot),
        default=ExampleRobot.NONE,
    )
    parser.add_argument(ExampleArgument.BUNDLE_DIRECTORY, type=Path)
    options = parser.parse_args(arguments)
    result = LaboratoryExample(
        robot=options.robot, bundle_directory=options.bundle_directory
    ).load()
    print(f"Laboratory: {result.laboratory.bundle_directory}")
    print(f"Robot: {options.robot}")
    print(f"World bodies: {len(result.world.bodies)}")
    print(
        "Movable objects: "
        + ", ".join(item.body_name for item in result.laboratory.description.objects)
    )
    print("Rack slots: " + ", ".join(result.laboratory.description.slots))
    print(f"CRAM context: {'ready' if result.context is not None else 'no robot'}")


if __name__ == "__main__":
    main()
