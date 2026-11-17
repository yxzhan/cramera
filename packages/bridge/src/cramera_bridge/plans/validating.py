"""
Validating a plan before it runs on the robot: the performing robot's steps are grounded
one after another in a copy of the plan's twin, so nothing moves, and the report says
how each fared. A detection perceives where the robot's camera sees its region now,
through the robot's perception or that of its simulated scene, else it is assumed from
the twin. For the real robot, the twin's robot stands as the real one reports.

Run as ``python -m cramera_bridge.plans.validating <order.json> <report.json>``, it reads
a :class:`~cramera_protocol.validation.ValidationOrder` and writes the
:class:`~cramera_protocol.validation.ValidationReport` once its standard input closes, so
each validation has a process of its own.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass

from typing_extensions import Any, Iterator, List

from coraplex.perception import PerceptionInterface, WorldPerception
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.plans.grounding import PlanGrounding
from cramera_bridge.plans.order_command import OrderCommand
from cramera_bridge.plans.robot_perception import RobotPerception
from cramera_bridge.real_robot import ReportedPosture
from cramera_protocol.plan_builder import PlanStep
from cramera_protocol.validation import ValidationOrder, ValidationReport
from semantic_digital_twin.world import World

# %% validating


@dataclass(frozen=True)
class PlanValidator:
    """
    Validates the plan of an order.
    """

    order: ValidationOrder
    """
    The plan, its environment and the scene the robot's camera sees.
    """

    def validate(self) -> ValidationReport:
        """
        :return: How each step of the performing robot fared, starting where
            :meth:`build_world` stands the robot.
        """
        demo = PlannedTaskDemo.of_document(self.order.demo, port=0)
        world = self.build_world(demo)
        context = demo.build_context(world)
        steps = demo.steps()
        with self.perception(demo, steps) as live:
            grounded = PlanGrounding(
                context=context,
                bodies=dict(demo.bodies),
                actions=demo.discovered_actions,
                perception=live,
                site=demo.site,
                safety=demo.document.safety,
            ).ground(steps)
        return ValidationReport(steps=[step.to_step_validation() for step in grounded])

    def build_world(self, demo: PlannedTaskDemo) -> World:
        """
        :param demo: The plan's demo.
        :return: The plan's twin, the robot standing as the real one reports when the
            order says so, else in the posture the plan gives it.
        :raises JointStatesNotReceived: When the real robot reports no joint positions.
        """
        world = demo.build_world()
        if self.order.reads_robot_posture:
            ReportedPosture().apply(world)
        return world

    @contextmanager
    def perception(
        self, demo: PlannedTaskDemo, steps: List[PlanStep]
    ) -> Iterator[PerceptionInterface]:
        """
        Reach the robot's perception while the block runs, if a step looks for objects;
        a plan that looks for none is given the twin, which it never asks.

        :param demo: The plan's demo.
        :param steps: The performing robot's steps.
        """
        if not any(
            demo.discovered_actions.named(step.action).looks_for_objects()
            for step in steps
        ):
            yield WorldPerception()
            return
        with RobotPerception(
            demo=self.order.demo, simulated_objects=self.order.simulated_objects
        ).perceiving() as live:
            yield live


# %% command


@dataclass(frozen=True)
class ValidateCommand(OrderCommand):
    """
    Validates the plan an order file names and writes the report into a file.
    """

    def answer(self, order: Any) -> ValidationReport:
        return PlanValidator(ValidationOrder.from_json(order)).validate()


if __name__ == "__main__":
    ValidateCommand().main(sys.argv[1:])
