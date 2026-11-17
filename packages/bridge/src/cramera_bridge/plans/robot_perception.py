"""
The perception of a plan's robot while the plan is composed: RoboKudo's query action,
which a simulated robot answers itself, rendering its camera from a world of its own
that holds the objects of its simulated scene, so the plan's twin can differ from what
the camera sees.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta

from typing_extensions import Iterator, List, Optional

from coraplex.perception import PerceptionInterface, RoboKudoPerception
from cramera_bridge.exceptions import PerceptionEngineNotNamed
from cramera_bridge.execution import (
    PERCEIVING_NODE_NAME,
    PERCEPTION_ENGINE_VARIABLE,
    PhysicalExecution,
)
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_bridge.ros_node import SpinningNode
from cramera_protocol.plan_builder import DemoDocument, PlannedObject
from robokudo.defs import PACKAGE_NAME
from robokudo.query_service import QueryService
from robokudo.utils.module_loader import ModuleLoader

# %% perception


@dataclass
class RobotPerception:
    """
    Reaches the perception of a plan's robot, or of its simulation.
    """

    demo: DemoDocument
    """
    The plan whose robot perceives, and its environment.
    """

    simulated_objects: Optional[List[PlannedObject]] = None
    """
    The objects standing in a simulated scene that the robot's camera is rendered from,
    or None to ask the robot's own perception.
    """

    analysis_engine: str = field(
        default_factory=lambda: os.environ.get(PERCEPTION_ENGINE_VARIABLE, "")
    )
    """
    The module of RoboKudo's analysis engines a simulated scene is perceived with.
    """

    @contextmanager
    def perceiving(self) -> Iterator[PerceptionInterface]:
        """
        Reach the robot's perception over RoboKudo's query action while the block runs,
        waiting for each answer as long as the plan's perception settings allow; for a
        simulated scene, also answer that action from the analysis engine rendering the
        robot's camera from a world holding the scene's objects.

        :raises PerceptionEngineNotNamed: When a simulated scene is perceived and no
            analysis engine is named.
        """
        with ExitStack() as perceiving:
            node = perceiving.enter_context(
                SpinningNode(PERCEIVING_NODE_NAME).running()
            )
            if self.simulated_objects is not None:
                self.serve_simulated_scene(perceiving)
            yield RoboKudoPerception(
                ros_node=node,
                answer_timeout=timedelta(seconds=self.demo.perception.answer_timeout),
            )

    def serve_simulated_scene(self, perceiving: ExitStack) -> None:
        """
        Answer RoboKudo's query action from the analysis engine, rendering the robot's
        camera from a world of the plan's robot, its arms parked, and the scene's
        objects, until ``perceiving`` closes.

        :param perceiving: Stops the analysis engine when it closes.
        :raises PerceptionEngineNotNamed: When no analysis engine is named.
        """
        if not self.analysis_engine:
            raise PerceptionEngineNotNamed(PERCEPTION_ENGINE_VARIABLE)
        scene = PlannedTaskDemo.of_document(
            replace(
                self.demo,
                plan=replace(self.demo.plan, objects=self.simulated_objects),
            ),
            port=0,
        )
        world = scene.build_world()
        PhysicalExecution().prepare(world)
        service = QueryService(
            analysis_engine=ModuleLoader().load_ae(
                ros_pkg_name=PACKAGE_NAME, module_name=self.analysis_engine
            ),
            world=world,
        )
        service.start()
        perceiving.callback(service.stop)
