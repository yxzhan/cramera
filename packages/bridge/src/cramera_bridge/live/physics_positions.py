"""
The positions the physics has moved the joints left to it to.

While a plan runs, a MuJoCo synchronizer can leave the joints that are not the robot's
to the physics: the world then keeps its own positions of them, which the physics no
longer follows, so a snapshot of the world alone shows a drawer or door stand still
while the robot pushes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from typing_extensions import Dict

from semantic_digital_twin.callbacks.callback import ModelChangeCallback
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF

PHYSICS_POSITIONS_READER = "physics_positions_left_to_the_physics"
"""
The method of a synchronizer, a model change callback of the world, that reads the
physics positions of the joints left to the physics, by the id of their degree of
freedom.

A CRAM without it leaves no joint to the physics.
"""


@dataclass
class PhysicsPositions:
    """
    The physics positions of the degrees of freedom left to the physics at one moment.
    """

    by_degree_of_freedom: Dict[UUID, float] = field(default_factory=dict)
    """
    The position of each degree of freedom left to the physics, by its id.
    """

    @classmethod
    def of(cls, world: World) -> PhysicsPositions:
        """
        :param world: A world, maybe synchronized with physics simulations.
        :return: The positions every simulation the world is synchronized with has
            moved the joints left to it to.
        """
        positions = cls()
        for callback in ModelChangeCallback.all_callbacks_of_this_type_from_world(
            world
        ):
            read = getattr(callback, PHYSICS_POSITIONS_READER, None)
            if read is not None:
                positions.by_degree_of_freedom.update(read())
        return positions

    def position_of(self, joint: ActiveConnection1DOF) -> float:
        """
        :param joint: A joint of the world.
        :return: Where the physics has the joint if it is left to the physics, else
            where the world has it.
        """
        position = self.by_degree_of_freedom.get(joint.raw_dof.id)
        if position is None:
            return joint.position
        return position * joint.multiplier + joint.offset
