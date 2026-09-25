"""
Resolve PR2 finger closure from the reached object's collision geometry.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass

import numpy as np

from semantic_digital_twin.datastructures.definitions import GripperState
from semantic_digital_twin.datastructures.joint_state import JointState
from semantic_digital_twin.robots.pr2 import PR2LeftGripper, PR2RightGripper
from semantic_digital_twin.world import World
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF
from semantic_digital_twin.world_description.world_entity import Body


# %% contact measurements
class Pr2GraspGeometryError(ValueError):
    """
    The reached object cannot be held between the PR2's two fingertips.
    """


@dataclass(frozen=True)
class FingerContactDistances:
    """
    Signed object clearances for both fingertips, including collision margins.
    """

    first: float
    """
    First fingertip's closest object clearance, in metres.
    """

    second: float
    """
    Second fingertip's closest object clearance, in metres.
    """

    @property
    def minimum(self) -> float:
        """
        Return the more restrictive of the two fingertip clearances.
        """
        return min(self.first, self.second)

    @property
    def maximum(self) -> float:
        """
        Return the more distant fingertip's object clearance.
        """
        return max(self.first, self.second)


@dataclass
class Pr2GripperContact:
    """
    Find partial closure at the current grasp pose without moving the live world.

    This models geometry and a kinematic stop. It does not model tactile sensing, grasp
    force, friction, compliance, or glass fracture.
    """

    world: World
    """
    World containing the reached robot and object.
    """

    end_effector: PR2LeftGripper | PR2RightGripper
    """
    PR2 gripper whose two fingertips surround the object.
    """

    body: Body
    """
    Object whose current collision geometry determines the stopping point.
    """

    clearance: float = 0.00025
    """
    Required nonnegative signed clearance from either fingertip, in metres.
    """

    maximum_contact_difference: float = 0.001
    """
    Largest accepted clearance difference between the two pads, in metres.
    """

    query_distance: float = 0.2
    """
    Maximum distance requested from the collision detector, in metres.
    """

    joint_tolerance: float = 1e-9
    """
    Maximum remaining joint interval after the contact search, in radians.
    """

    def __post_init__(self) -> None:
        """
        Reject invalid margins and objects from unrelated worlds.
        """
        if (
            not isinstance(self.end_effector, (PR2LeftGripper, PR2RightGripper))
            or self.end_effector._world is not self.world
            or self.body._world is not self.world
        ):
            raise Pr2GraspGeometryError("Gripper and object must share the PR2 world")
        if (
            not math.isfinite(self.clearance)
            or self.clearance < 0
            or not math.isfinite(self.maximum_contact_difference)
            or self.maximum_contact_difference < 0
            or not math.isfinite(self.query_distance)
            or self.query_distance <= self.clearance
            or not math.isfinite(self.joint_tolerance)
            or self.joint_tolerance <= 0
        ):
            raise Pr2GraspGeometryError("Contact search tolerances are invalid")

    @property
    def connection(self) -> ActiveConnection1DOF:
        """
        Return the canonical joint shared by the PR2's mimicking fingers.
        """
        finger_connection = self.end_effector.fingers[0].root.parent_connection
        return self.world.get_connection_by_name(finger_connection.raw_dof.name)

    @property
    def fingertips(self) -> list[Body]:
        """
        Return the two contact pads from the robot's semantic description.
        """
        return [finger.tip for finger in self.end_effector.fingers]

    def distances(self) -> FingerContactDistances:
        """
        Measure both pads directly, independent of allowed grasp collisions.
        """
        detector = self.world.collision_manager.collision_detector
        values = []
        for fingertip in self.fingertips:
            contact = detector.check_collision_between_bodies(
                fingertip, self.body, distance=self.query_distance
            )
            values.append(contact.distance if contact is not None else math.inf)
        return FingerContactDistances(*values)

    def distance(self) -> float:
        """
        Return the current minimum signed pad-to-object clearance, in metres.
        """
        return self.distances().minimum

    def aperture(self) -> float:
        """
        Measure separation of the pad mesh projections along the tool's y-axis.
        """
        intervals = []
        for fingertip in self.fingertips:
            points = np.concatenate(
                [
                    shape.mesh_in_frame(self.end_effector.tool_frame).vertices
                    for shape in fingertip.collision
                ]
            )
            intervals.append((float(points[:, 1].min()), float(points[:, 1].max())))
        intervals.sort()
        return intervals[1][0] - intervals[0][1]

    def goal_state(self) -> JointState:
        """
        Search an isolated world copy and return a goal for the original joint.
        """
        copied_world = deepcopy(self.world)
        search = Pr2GripperContact(
            world=copied_world,
            end_effector=copied_world.get_semantic_annotation_by_id(
                self.end_effector.id
            ),
            body=copied_world.get_kinematic_structure_entity_by_id(self.body.id),
            clearance=self.clearance,
            maximum_contact_difference=self.maximum_contact_difference,
            query_distance=self.query_distance,
            joint_tolerance=self.joint_tolerance,
        )
        return JointState.from_mapping({self.connection: search._contact_position()})

    def _semantic_position(self, state: GripperState) -> float:
        """
        Resolve an open or closed semantic state to the canonical finger joint.
        """
        joint_state = self.end_effector.get_joint_state_by_type(state)
        for connection, value in joint_state.items():
            if connection.raw_dof is self.connection.raw_dof:
                raw_position = (value - connection.offset) / connection.multiplier
                return (
                    raw_position * self.connection.multiplier + self.connection.offset
                )
        raise Pr2GraspGeometryError("Gripper state does not command its fingers")

    def _contact_position(self) -> float:
        """
        Find the nearest safe stopping position between closed and open states.
        """
        closed = self._semantic_position(GripperState.CLOSE)
        opened = self._semantic_position(GripperState.OPEN)
        if not math.isfinite(closed) or not math.isfinite(opened) or closed >= opened:
            raise Pr2GraspGeometryError(
                "Gripper opening does not define a valid interval"
            )
        self.connection.position = closed
        closed_distance = self.distance()
        self.connection.position = opened
        opened_distance = self.distance()
        if closed_distance >= self.clearance or opened_distance < self.clearance:
            raise Pr2GraspGeometryError(
                "The object has no safe contact between the closed and open gripper"
            )
        while opened - closed > self.joint_tolerance:
            midpoint = (closed + opened) / 2
            self.connection.position = midpoint
            if self.distance() >= self.clearance:
                opened = midpoint
            else:
                closed = midpoint
        self.connection.position = opened
        distances = self.distances()
        if distances.maximum - distances.minimum > self.maximum_contact_difference:
            raise Pr2GraspGeometryError("The object is not centered between both pads")
        detector = self.world.collision_manager.collision_detector
        for body in self.end_effector.bodies_with_collision:
            if body in self.fingertips:
                continue
            contact = detector.check_collision_between_bodies(body, self.body)
            if contact is not None and contact.distance < 0:
                raise Pr2GraspGeometryError(
                    "The object intersects the gripper outside its pads"
                )
        return opened
