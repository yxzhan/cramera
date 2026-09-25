"""
Close the simulated PR2 fingers at the reached glass geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import ClassVar, Optional

from coraplex.datastructures.enums import Arms
from coraplex.datastructures.grasp import GraspDescription
from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from coraplex.robot_plans.motions.gripper import MoveGripperMotion
from coraplex.view_manager import ViewManager
from cramera.pr2_gripper import Pr2GripperContact
from giskardpy.motion_statechart.context import MotionStatechartContext
from giskardpy.motion_statechart.goals.templates import Sequence
from giskardpy.motion_statechart.graph_node import MotionStatechartNode
from semantic_digital_twin.datastructures.definitions import GripperState
from semantic_digital_twin.datastructures.joint_state import JointState
from semantic_digital_twin.world_description.world_entity import Body


# %% geometry-limited closing
@dataclass(eq=False, repr=False)
class ContactGripperGoal(Sequence):
    """
    Expand finger control using the grasp pose reached by the preceding stage.
    """

    motion: ContactGripperMotion = field(kw_only=True)
    """
    Closing motion retaining the object and native controller settings.
    """

    def expand(self, context: MotionStatechartContext) -> None:
        """
        Resolve the current contact geometry before compiling finger movement.
        """
        self.nodes = [self.motion.resolved_motion().motion_chart]
        super().expand(context)


@dataclass
class ContactGripperMotion(MoveGripperMotion):
    """
    Resolve a collision-clear finger stop after the object approach finishes.
    """

    requires_individual_execution: ClassVar[bool] = True
    """
    Wait for preceding motion before evaluating the reached grasp geometry.
    """

    object_designator: Body = field(kw_only=True)
    """
    Glass between the fingertips at the current reached pose.
    """

    contact_clearance: float = field(default=0.00025, kw_only=True)
    """
    Small non-penetrating clearance in the collision geometry, in metres.
    """

    @property
    def motion_chart(self) -> MotionStatechartNode:
        """
        Defer geometry resolution until this motion's individual execution stage.
        """
        return ContactGripperGoal(motion=self)

    def resolved_goal_state(self) -> JointState:
        """
        Find a finger stop using the current gripper-to-glass transformation.
        """
        end_effector = ViewManager.get_end_effector_view(self.gripper, self.robot)
        self.goal_state = Pr2GripperContact(
            self.world,
            end_effector,
            self.object_designator,
            clearance=self.contact_clearance,
        ).goal_state()
        return super().resolved_goal_state()

    def resolved_motion(self) -> MoveGripperMotion:
        """
        Construct the native finger controller for the measured stopping position.
        """
        motion = MoveGripperMotion(
            motion=self.motion,
            gripper=self.gripper,
            goal_state=self.resolved_goal_state(),
            joint_position_threshold=self.joint_position_threshold,
            finger_velocity=self.finger_velocity,
            allow_gripper_collision=self.allow_gripper_collision,
        )
        motion.plan_node = self.plan_node
        return motion


@dataclass
class LaboratoryPickUpAction(PickUpAction):
    """
    Use native pickup with a geometric stop for closing around laboratory glass.
    """

    grasp_joint_position_threshold: float = field(
        default=MoveGripperMotion.joint_position_threshold, kw_only=True
    )
    """
    Allowed closing position error in radians at the PR2 finger joint.
    """

    contact_clearance: float = field(default=0.00025, kw_only=True)
    """
    Minimum planned fingertip-to-glass collision clearance, in metres.
    """

    def create_grasp_motion(self) -> ContactGripperMotion:
        """
        Close slowly to the glass-specific stop before attaching the object.
        """
        return ContactGripperMotion(
            motion=GripperState.CLOSE,
            gripper=self.arm,
            object_designator=self.object_designator,
            contact_clearance=self.contact_clearance,
            finger_velocity=self.grasp_closing_velocity,
            joint_position_threshold=self.grasp_joint_position_threshold,
        )
