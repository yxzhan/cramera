"""
Plans, statecharts and marker messages for the live publisher tests, built from native
CRAM classes and plain message stand-ins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import List, Optional

from coraplex.plans.executors import KinematicallySimulatedPlanExecutor
from coraplex.robot_plans.actions.base import SequentialAction
from coraplex.robot_plans.actions.core.robot_body import MoveTorsoAction
from cramera_bridge.performing import TaskContext
from cramph.composites import Sequence
from cramph.context import StatechartContext
from cramph.data_types import LifeCycleValues
from cramph.executor import StatechartExecutor
from cramph.monitors import CountTicks
from cramph.node import StatechartNode
from cramph.statechart import Statechart
from giskardpy.motion_statechart.context import MotionControlContext
from giskardpy.motion_statechart.monitors.feature_monitors import HeightMonitor
from giskardpy.qp.qp_controller_config import QPControllerConfig
from semantic_digital_twin.datastructures.definitions import TorsoState
from krrood_spatial_math.spatial_types import Point3
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.world_entity import Body

# %% plans


@dataclass(eq=False, repr=False, kw_only=True)
class WaitAction(SequentialAction):
    """
    Waits a number of ticks, an action any robot performs.
    """

    ticks: int = 1
    """
    How many ticks it waits.
    """

    def create_steps(self) -> List[StatechartNode]:
        return [CountTicks(name="wait", ticks=self.ticks)]


@dataclass
class SmallPlan:
    """
    A plan of a root holding one action, and the step the action expanded into, in a
    statechart prepared by its executor but not run, so a test sets each node's life
    cycle itself.
    """

    executor: KinematicallySimulatedPlanExecutor
    """
    The executor that prepared the plan.
    """

    root: Sequence
    """
    The plan's top-level node.
    """

    action: StatechartNode
    """
    Its action.
    """

    motion: StatechartNode
    """
    The step the action expanded into.
    """

    @property
    def statechart(self) -> Statechart:
        """
        :return: The statechart the plan runs as.
        """
        return self.executor.statechart

    @property
    def nodes(self) -> List[StatechartNode]:
        """
        :return: The plan's nodes: the root, the action and its step.
        """
        return [self.root, self.action, self.motion]

    def set(self, node: StatechartNode, life_cycle: LifeCycleValues) -> None:
        """
        :param node: A node of the plan.
        :param life_cycle: The life cycle state it is in from now on.
        """
        self.statechart.life_cycle_state[node] = life_cycle

    @classmethod
    def prepare(cls, action: StatechartNode, context: TaskContext) -> SmallPlan:
        """
        :param action: The plan's action.
        :param context: The world and the robot that performs the plan.
        :return: The plan, its action expanded; no node has run.
        """
        executor = KinematicallySimulatedPlanExecutor(context.take_statechart_context())
        root = Sequence(nodes=[action])
        statechart = Statechart(context=executor.context)
        statechart.add_node(root)
        executor.prepare(statechart)
        return cls(
            executor=executor, root=root, action=action, motion=action.children[0]
        )

    @classmethod
    def raising_the_torso(cls, context: TaskContext) -> SmallPlan:
        """
        :param context: The world and a robot with a torso.
        :return: A plan raising the robot's torso, whose step is a motion.
        """
        return cls.prepare(MoveTorsoAction(torso_state=TorsoState.HIGH), context)

    @classmethod
    def waiting(cls, context: TaskContext) -> SmallPlan:
        """
        :param context: The world and any robot.
        :return: A plan waiting a tick, whose step is no motion.
        """
        return cls.prepare(WaitAction(), context)


# %% statecharts


@dataclass
class HeightChart:
    """
    A monitor-only statechart observing a body's height, ticked without motion control.
    """

    chart: Statechart
    """
    The chart.
    """

    executor: StatechartExecutor
    """
    Its executor.
    """

    @classmethod
    def build(cls, world: World, body: Body) -> HeightChart:
        """
        :param world: The world.
        :param body: The observed body.
        :return: The chart, compiled.
        """
        executor = StatechartExecutor(
            context=StatechartContext.with_extensions(
                world,
                [
                    MotionControlContext(
                        qp_controller_config=QPControllerConfig.create_with_simulation_defaults()
                    )
                ],
            )
        )
        chart = Statechart(context=executor.context)
        chart.add_node(
            HeightMonitor(
                root_link=world.root,
                tip_link=body,
                reference_point=Point3(0, 0, 0, reference_frame=world.root),
                tip_point=Point3(0, 0, 0, reference_frame=body),
                lower_limit=0.5,
                upper_limit=2.0,
            )
        )
        executor.compile(chart)
        return cls(chart=chart, executor=executor)


# %% marker messages


@dataclass
class Vector:
    """
    Stands in for a message with ``x``, ``y`` and ``z``.
    """

    x: float = 0.0
    """
    The x component.
    """

    y: float = 0.0
    """
    The y component.
    """

    z: float = 0.0
    """
    The z component.
    """


@dataclass
class Orientation:
    """
    Stands in for a quaternion message.
    """

    x: float = 0.0
    """
    The x component.
    """

    y: float = 0.0
    """
    The y component.
    """

    z: float = 0.0
    """
    The z component.
    """

    w: float = 1.0
    """
    The scalar component.
    """


@dataclass
class MarkerPose:
    """
    Stands in for a pose message.
    """

    position: Vector = field(default_factory=Vector)
    """
    The position.
    """

    orientation: Orientation = field(default_factory=Orientation)
    """
    The orientation.
    """


@dataclass
class MarkerColor:
    """
    Stands in for a colour message.
    """

    r: float = 1.0
    """
    The red channel.
    """

    g: float = 0.0
    """
    The green channel.
    """

    b: float = 0.0
    """
    The blue channel.
    """

    a: float = 1.0
    """
    The opacity.
    """


@dataclass
class Header:
    """
    Stands in for a message header.
    """

    frame_id: str = "map"
    """
    The frame the message is given in.
    """


@dataclass
class MarkerMessage:
    """
    Stands in for a ``visualization_msgs/Marker``.
    """

    ns: str = "points"
    """
    The namespace.
    """

    id: int = 0
    """
    The identity within the namespace.
    """

    type: int = 1
    """
    The marker type number.
    """

    action: int = 0
    """
    The marker action number.
    """

    header: Header = field(default_factory=Header)
    """
    The header.
    """

    pose: MarkerPose = field(default_factory=MarkerPose)
    """
    The pose.
    """

    scale: Vector = field(default_factory=lambda: Vector(0.1, 0.1, 0.1))
    """
    The scale.
    """

    color: MarkerColor = field(default_factory=MarkerColor)
    """
    The colour.
    """

    points: List[Vector] = field(default_factory=list)
    """
    The points.
    """

    text: str = ""
    """
    The text.
    """

    colors: List[MarkerColor] = field(default_factory=list)
    """
    The colour of each point, or none.
    """
