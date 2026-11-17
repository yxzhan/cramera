"""
What a robot model reaches, for the quick check of a marker dragged across the scene:
the sphere around each arm's shoulder and the top of the robot's table, measured with
the robot standing at the origin, so in the frame of the pose a plan stands it at.

Run as ``python -m cramera_bridge.plans.reaching <order.json> <answer.json>``, it reads
a :class:`~cramera_protocol.reach.ReachRequest` and writes the
:class:`~cramera_protocol.reach.RobotReach` once its standard input closes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from typing_extensions import Any, Optional

from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.order_command import OrderCommand
from cramera_bridge.plans.placing import ArmReaches
from cramera_bridge.plans.regions import RegionBoxes
from cramera_protocol.geometry import FloorRectangle
from cramera_protocol.geometry import Pose as PlannedPose
from cramera_protocol.plan_builder import DemoDocument, PlanDocument, PlannedRobot
from cramera_protocol.reach import ReachRequest, RobotReach, TableTop
from semantic_digital_twin.robots.robot_part_mixins import HasMountingTable
from semantic_digital_twin.robots.robot_parts import AbstractRobot

# %% reach


@dataclass(frozen=True)
class RobotReachMeasure:
    """
    Measures what a robot model reaches, standing at the origin of an empty floor.
    """

    request: ReachRequest
    """
    The robot model.
    """

    robot_identifier: str = "robot_1"
    """
    The identity the measured robot is given in its plan.
    """

    def measure(self) -> RobotReach:
        """
        :return: What each arm of the robot reaches, and the top of its table.
        """
        plan = PlanDocument(
            name="reach",
            robots=[
                PlannedRobot(
                    identifier=self.robot_identifier,
                    model=self.request.robot,
                    label=self.request.robot,
                    pose=PlannedPose(0, 0, 0, 0, 0, 0, 1),
                )
            ],
            selected_robot=self.robot_identifier,
        )
        demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
        context = demo.build_context(demo.build_world())
        return RobotReach(
            robot=self.request.robot,
            arms=ArmReaches(context.robot).arms(),
            table=self.table_top(context.robot),
        )

    @staticmethod
    def table_top(robot: AbstractRobot) -> Optional[TableTop]:
        """
        :param robot: The robot standing at the origin.
        :return: The top of the table it is mounted on, or None for a robot on the
            floor.
        """
        if not isinstance(robot, HasMountingTable):
            return None
        box = RegionBoxes(robot).table()
        return TableTop(
            area=FloorRectangle(
                min_x=float(box.min_x),
                max_x=float(box.max_x),
                min_y=float(box.min_y),
                max_y=float(box.max_y),
            ),
            height=float(box.min_z),
        )


# %% command


@dataclass(frozen=True)
class ReachCommand(OrderCommand):
    """
    Measures the reach of the robot model an order file names and writes it into a file.
    """

    def answer(self, order: Any) -> RobotReach:
        return RobotReachMeasure(ReachRequest.from_json(order)).measure()


if __name__ == "__main__":
    ReachCommand().main(sys.argv[1:])
