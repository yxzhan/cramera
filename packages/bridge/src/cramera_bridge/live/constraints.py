"""
Constraints a person attaches to plan nodes, compiled to native statechart monitors and
observed against the live world while the plan runs.

A monitor only observes; it does not steer the robot. Each attached constraint therefore
runs as a monitor-only statechart of its own, compiled against the live world without
motion control and ticked on the simulation thread with every tick of the plan, so its
native observation is what the plan view shows next to the node.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from numbers import Real

from typing_extensions import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from cramera_bridge.exceptions import InvalidConstraint
from cramera_bridge.live.body_names import BodyNames
from cramera_protocol.live_messages import (
    ConstraintActivation,
    ConstraintMonitorType,
    ConstraintObservation,
    ConstraintRequest,
    MonitorParameterKey,
)
from cramph.context import StatechartContext
from cramph.data_types import ObservationStateValues
from cramph.executor import StatechartExecutor
from cramph.statechart import Statechart
from giskardpy.motion_statechart.context import MotionControlContext
from giskardpy.motion_statechart.graph_node import MotionStatechartNode
from giskardpy.motion_statechart.monitors.cartesian_monitors import (
    InFieldOfView,
    PointingAt,
    VectorsAligned,
)
from giskardpy.motion_statechart.monitors.feature_monitors import (
    DistanceMonitor,
    HeightMonitor,
)
from giskardpy.qp.qp_controller_config import QPControllerConfig
from krrood_spatial_math.spatial_types import Point3, Vector3
from semantic_digital_twin.robots.robot_parts import AbstractRobot, Camera

if TYPE_CHECKING:
    from semantic_digital_twin.world import World
    from semantic_digital_twin.world_description.world_entity import Body

VECTOR_LENGTH = 3
"""
How many numbers a vector or point parameter holds.
"""

DEFAULT_AXIS = (0.0, 0.0, 1.0)
"""
The axis the plan view's constraint compiler means when it names none: up.
"""

DEFAULT_ALIGNMENT_THRESHOLD = 0.1
"""
The alignment tolerance the plan view's compiler uses when it names none.
"""

DEFAULT_POINTING_THRESHOLD = 0.05
"""
The pointing tolerance in metres the plan view's compiler uses when it names none.
"""

DEFAULT_FIELD_OF_VIEW_MARGIN = 0.0
"""
The angle in radians a body kept in view keeps from the edges of the view when the
constraint names none.
"""

DEFAULT_LOWER_LIMIT = 0.0
"""
The lower height or distance limit in metres the plan view's compiler uses by default.
"""

DEFAULT_UPPER_LIMIT = 2.0
"""
The upper height or distance limit in metres the plan view's compiler uses by default.
"""

# %% reading parameters


@dataclass(frozen=True)
class MonitorParameters:
    """
    The parameters of one constraint, resolved against the live world.
    """

    parameters: Dict[str, Any]
    """
    The parameters as the request gave them.
    """

    world: World
    """
    The world body names are resolved in.
    """

    def body(self, key: MonitorParameterKey) -> Body:
        """
        :param key: A body parameter.
        :return: The named body; the world's root when the parameter is absent.
        :raises UnknownBody: When no body carries the name.
        :raises InvalidConstraint: When the parameter is no name.
        """
        name = self.parameters.get(key)
        if name is None:
            return self.world.root
        if not isinstance(name, str):
            raise InvalidConstraint(key, "a body name")
        return BodyNames(self.world).require(name)

    def numbers(
        self, key: MonitorParameterKey, default: Tuple[float, ...]
    ) -> Tuple[float, ...]:
        """
        :param key: A vector or point parameter.
        :param default: Its value when absent.
        :return: Its three numbers.
        :raises InvalidConstraint: When it is not three numbers.
        """
        value = self.parameters.get(key, default)
        if (
            not isinstance(value, (list, tuple))
            or len(value) != VECTOR_LENGTH
            or not all(self.is_number(number) for number in value)
        ):
            raise InvalidConstraint(key, f"{VECTOR_LENGTH} numbers")
        return tuple(float(number) for number in value)

    def number(self, key: MonitorParameterKey, default: float) -> float:
        """
        :param key: A number parameter.
        :param default: Its value when absent.
        :return: The number.
        :raises InvalidConstraint: When it is no number.
        """
        value = self.parameters.get(key, default)
        if not self.is_number(value):
            raise InvalidConstraint(key, "a number")
        return float(value)

    @staticmethod
    def is_number(value: Any) -> bool:
        """
        :param value: A parameter value.
        :return: Whether it is a number and not a truth value.
        """
        return isinstance(value, Real) and not isinstance(value, bool)


@dataclass(frozen=True)
class ConstraintMonitorFactory:
    """
    Builds the native monitor a constraint request names.
    """

    world: World
    """
    The live world.
    """

    def monitor(self, request: ConstraintRequest) -> MotionStatechartNode:
        """
        :param request: A constraint request.
        :return: The monitor with its bodies resolved in the world.
        :raises UnknownBody: When a named body does not exist.
        :raises AmbiguousBody: When a short name names several bodies.
        :raises InvalidConstraint: When a parameter cannot be read.
        """
        parameters = MonitorParameters(request.monitor.parameters, self.world)
        root = parameters.body(MonitorParameterKey.ROOT_LINK)
        tip = parameters.body(MonitorParameterKey.TIP_LINK)
        match request.monitor.monitor_type:
            case ConstraintMonitorType.IN_FIELD_OF_VIEW:
                return InFieldOfView(
                    camera=self.camera(),
                    target=parameters.body(MonitorParameterKey.TARGET),
                    margin=parameters.number(
                        MonitorParameterKey.MARGIN, DEFAULT_FIELD_OF_VIEW_MARGIN
                    ),
                )
            case ConstraintMonitorType.VECTORS_ALIGNED:
                return VectorsAligned(
                    root_link=root,
                    tip_link=tip,
                    goal_normal=Vector3(
                        *parameters.numbers(
                            MonitorParameterKey.GOAL_NORMAL, DEFAULT_AXIS
                        ),
                        reference_frame=root,
                    ),
                    tip_normal=Vector3(
                        *parameters.numbers(
                            MonitorParameterKey.TIP_NORMAL, DEFAULT_AXIS
                        ),
                        reference_frame=tip,
                    ),
                    threshold=parameters.number(
                        MonitorParameterKey.THRESHOLD, DEFAULT_ALIGNMENT_THRESHOLD
                    ),
                )
            case ConstraintMonitorType.POINTING_AT:
                return PointingAt(
                    root_link=root,
                    tip_link=tip,
                    goal_point=self.goal_point(parameters, root),
                    pointing_axis=Vector3(
                        *parameters.numbers(
                            MonitorParameterKey.POINTING_AXIS, DEFAULT_AXIS
                        ),
                        reference_frame=tip,
                    ),
                    threshold=parameters.number(
                        MonitorParameterKey.THRESHOLD, DEFAULT_POINTING_THRESHOLD
                    ),
                )
            case ConstraintMonitorType.HEIGHT:
                monitor_class = HeightMonitor
            case ConstraintMonitorType.DISTANCE:
                monitor_class = DistanceMonitor
        return monitor_class(
            root_link=root,
            tip_link=tip,
            reference_point=Point3(0, 0, 0, reference_frame=root),
            tip_point=Point3(0, 0, 0, reference_frame=tip),
            lower_limit=parameters.number(
                MonitorParameterKey.LOWER_LIMIT, DEFAULT_LOWER_LIMIT
            ),
            upper_limit=parameters.number(
                MonitorParameterKey.UPPER_LIMIT, DEFAULT_UPPER_LIMIT
            ),
        )

    def camera(self) -> Camera:
        """
        :return: The default camera of the live world's first robot.
        :raises InvalidConstraint: When the world holds no robot.
        """
        robots = self.world.get_semantic_annotations_by_type(AbstractRobot)
        if not robots:
            raise InvalidConstraint(
                MonitorParameterKey.TARGET, "a body a robot's camera looks at"
            )
        return robots[0].get_default_camera()

    @staticmethod
    def goal_point(parameters: MonitorParameters, root: Body) -> Point3:
        """
        :param parameters: The constraint's parameters.
        :param root: The body a numeric goal point is given in.
        :return: The given point, else the origin of the named goal body.
        :raises InvalidConstraint: When neither is given.
        """
        if MonitorParameterKey.GOAL_POINT in parameters.parameters:
            return Point3(
                *parameters.numbers(MonitorParameterKey.GOAL_POINT, DEFAULT_AXIS),
                reference_frame=root,
            )
        if MonitorParameterKey.GOAL_POINT_BODY not in parameters.parameters:
            raise InvalidConstraint(MonitorParameterKey.GOAL_POINT, "a point or a body")
        target = parameters.body(MonitorParameterKey.GOAL_POINT_BODY)
        return Point3(0, 0, 0, reference_frame=target)


# %% observing


@dataclass
class AttachedConstraint:
    """
    One constraint attached to a plan node, with its monitor and, once active, the
    monitor-only statechart observing it.
    """

    request: ConstraintRequest
    """
    The request that attached it.
    """

    monitor: MotionStatechartNode
    """
    The native monitor.
    """

    executor: Optional[StatechartExecutor] = None
    """
    The executor ticking the monitor's statechart, once the constraint is active.
    """

    def activate(self, world: World) -> None:
        """
        Compile the monitor into a statechart of its own against the world; the
        constraint counts as active only once it is compiled.

        The statechart's context carries the motion control context the monitors read,
        but no motion control runs, so ticking it moves nothing.

        :param world: The live world.
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
        chart.add_node(self.monitor)
        executor.compile(chart)
        self.executor = executor

    def tick(self) -> None:
        """
        Evaluate the monitor against the world's current state.
        """
        self.executor.tick()

    def observation(self) -> ConstraintObservation:
        """
        :return: The constraint with the native observation its history recorded last,
            or without one before it was activated.
        """
        observation = None
        if self.executor is not None:
            moment = self.executor.statechart.state_history.latest_item
            observation = ObservationStateValues(
                float(moment.observation_state[self.monitor])
            ).name
        return ConstraintObservation(
            text=self.request.text,
            monitor=self.request.monitor.monitor_type,
            observation=observation,
        )


@dataclass
class ConstraintBoard:
    """
    The constraints attached to the plan's nodes; HTTP threads attach, the simulation
    thread activates and ticks them.
    """

    attached: Dict[str, List[AttachedConstraint]] = field(default_factory=dict)
    """
    The attached constraints by the identity of their plan node.
    """

    motion_started: bool = False
    """
    Whether a motion started since the last tick, which activates the constraints
    waiting for the next activation.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    """
    Guards every field.
    """

    def attach(self, node_identifier: str, constraint: AttachedConstraint) -> None:
        """
        :param node_identifier: The identity of the plan node.
        :param constraint: The constraint, not yet active.
        """
        with self.lock:
            self.attached.setdefault(node_identifier, []).append(constraint)

    def note_motion_started(self) -> None:
        """
        Let the constraints waiting for the next motion become active at the next tick.
        """
        with self.lock:
            self.motion_started = True

    def tick(self, world: World) -> None:
        """
        Activate the constraints that are due and evaluate every active one.

        :param world: The live world.
        """
        with self.lock:
            motion_started, self.motion_started = self.motion_started, False
            constraints = [
                constraint
                for node_constraints in self.attached.values()
                for constraint in node_constraints
            ]
        for constraint in constraints:
            if constraint.executor is None and (
                motion_started
                or constraint.request.apply is ConstraintActivation.IMMEDIATE
            ):
                constraint.activate(world)
            if constraint.executor is not None:
                constraint.tick()

    def observations(self, node_identifier: str) -> List[ConstraintObservation]:
        """
        :param node_identifier: The identity of a plan node.
        :return: The node's constraints with their latest observations.
        """
        with self.lock:
            constraints = list(self.attached.get(node_identifier, []))
        return [constraint.observation() for constraint in constraints]

    def clear(self) -> None:
        """
        Drop every constraint, for a new plan.
        """
        with self.lock:
            self.attached.clear()
            self.motion_started = False
