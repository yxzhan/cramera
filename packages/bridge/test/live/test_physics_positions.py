"""
Showing the joints a physics simulation is left to move where the physics has them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from typing_extensions import Dict

from cramera_bridge.live.physics_positions import PhysicsPositions
from cramera_bridge.live.world_model import WorldModelView
from cramera_bridge.live.world_state import WorldStatePublisher
from krrood_spatial_math.spatial_types import Vector3
from semantic_digital_twin.callbacks.callback import ModelChangeCallback
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import PrismaticConnection
from semantic_digital_twin.world_description.degree_of_freedom import DegreeOfFreedom
from semantic_digital_twin.world_description.world_entity import Body

WORLD_POSITION = 0.1
"""
Where the world keeps the drawer's degree of freedom.
"""

PHYSICS_POSITION = 0.3
"""
Where the physics has moved the drawer's degree of freedom.
"""

MULTIPLIER = 2.0
"""
How much the drawer moves per unit of its degree of freedom.
"""

OFFSET = 0.05
"""
Where the drawer is when its degree of freedom is zero.
"""


@dataclass
class Cabinet:
    """
    A world with one drawer, and the handles a test needs.
    """

    world: World
    """
    The world itself.
    """

    drawer: PrismaticConnection
    """
    The drawer's joint.
    """


@dataclass(eq=False)
class FakeSynchronizer(ModelChangeCallback):
    """
    Stands in for a MuJoCo synchronizer that leaves joints to the physics.
    """

    positions: Dict[UUID, float] = field(default_factory=dict)
    """
    What the physics has, by the id of a degree of freedom.
    """

    def on_model_change(self, **kwargs) -> None: ...

    def physics_positions_left_to_the_physics(self) -> Dict[UUID, float]:
        return dict(self.positions)


@pytest.fixture
def cabinet() -> Cabinet:
    world = World()
    with world.modify_world():
        root = Body(name=PrefixedName("cabinet"))
        drawer_body = Body(name=PrefixedName("drawer"))
        world.add_body(root)
        world.add_body(drawer_body)
        degree_of_freedom = DegreeOfFreedom(name=PrefixedName("drawer"))
        world.add_degree_of_freedom(degree_of_freedom)
        drawer = PrismaticConnection(
            name=PrefixedName("drawer"),
            parent=root,
            child=drawer_body,
            axis=Vector3.X(reference_frame=root),
            raw_dof=degree_of_freedom,
            multiplier=MULTIPLIER,
            offset=OFFSET,
        )
        world.add_connection(drawer)
    world.state[drawer.raw_dof.id].position = WORLD_POSITION
    world.notify_state_change()
    return Cabinet(world=world, drawer=drawer)


def drawer_frame(cabinet: Cabinet) -> float:
    """
    :return: Where a new snapshot of the cabinet has the drawer.
    """
    state = WorldStatePublisher().refresh(
        WorldModelView.of_world(cabinet.world), None, markers_version=0
    )
    return state.frames[str(cabinet.drawer.name)]


def test_a_joint_left_to_the_physics_is_shown_where_the_physics_has_it(cabinet):
    synchronizer = FakeSynchronizer(
        _world=cabinet.world,
        positions={cabinet.drawer.raw_dof.id: PHYSICS_POSITION},
    )
    try:
        assert drawer_frame(cabinet) == pytest.approx(
            PHYSICS_POSITION * MULTIPLIER + OFFSET
        )
    finally:
        synchronizer.stop()


def test_a_joint_not_left_to_the_physics_is_shown_where_the_world_has_it(cabinet):
    synchronizer = FakeSynchronizer(_world=cabinet.world)
    try:
        assert drawer_frame(cabinet) == pytest.approx(cabinet.drawer.position)
    finally:
        synchronizer.stop()


def test_a_world_without_physics_has_no_physics_positions(cabinet):
    assert PhysicsPositions.of(cabinet.world).by_degree_of_freedom == {}
