"""
The safety limits of a robot profile as the constraints every motion of a run is held
to: a workspace box per arm, written in the frame of the pose the plan stands the robot
at, and the speed no tool may exceed; and every arm joint's velocity limit, which the
robot's description sets. A plan is validated against the same workspace boxes, held a
margin inside their edges, so a validated plan does not ride the edge the run is stopped
at.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
from typing_extensions import List, Optional

from cramera_bridge.exceptions import ArmOutsideItsWorkspace
from cramera_bridge.names import ENUM_NAMES
from cramera_bridge.performing import TaskContext
from cramera_bridge.plans.arms import Arms, RobotArms
from cramera_bridge.plans.site import RobotSite
from cramera_protocol.robot_profile import ArmWorkspace, SafetyLimits
from giskardpy.motion_statechart.monitors.safety_monitors import (
    MaximumJointVelocity,
    MaximumToolVelocity,
    SafetyConstraint,
    SafetyEnvelope,
    WorkspaceBox,
)
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix, Point3
from semantic_digital_twin.world_description.connections import ActiveConnection1DOF

WORKSPACE_MARGIN = 0.01
"""
How far, in metres, inside the edges of its workspace box a validated plan keeps an arm's
tool, so that the real arm, which follows its motion less exactly than the twin, keeps
inside the box the run is stopped at.
"""


@dataclass(frozen=True)
class RobotSafety:
    """
    The safety limits a robot profile holds the performing robot of a plan to.
    """

    limits: SafetyLimits
    """
    The profile's safety limits.
    """

    site: RobotSite = field(default_factory=RobotSite)
    """
    Where the plan stands the robot, whose frame the workspaces are written in.
    """

    on_the_real_robot: bool = False
    """
    Whether the run moves the real robot, which holds its tools to the limits' speed for
    the real robot where they set one.
    """

    def envelope(self, context: TaskContext) -> SafetyEnvelope:
        """
        :param context: The world and the performing robot.
        :return: The constraints of every workspace and, for every arm, of the joint
            and tool velocities.
        """
        return SafetyEnvelope(
            constraints=[
                *[self.workspace_box(context, each) for each in self.limits.workspaces],
                *self.velocity_limits(context),
            ]
        )

    def workspace_check(
        self, context: TaskContext, margin: float = WORKSPACE_MARGIN
    ) -> SafetyEnvelope:
        """
        :param context: The world and the performing robot, standing where the plan
            starts.
        :param margin: How far, in metres, inside each box's edges the tool stays.
        :return: The constraints a plan is validated against: every workspace box of
            :meth:`envelope`, its edges moved the margin inwards, except an edge the
            arm's tool already stands nearer to than the margin: that edge stays where
            the run's monitor watches it, so a plan starting there validates as the
            monitor lets it run, moving the tool inwards from there or along the edge.
        """
        constraints = []
        for workspace in self.limits.workspaces:
            edges = self.workspace_box(context, workspace)
            tool = self.tool_position(context, edges)
            minimum = edges.minimum.to_np()[:3]
            maximum = edges.maximum.to_np()[:3]
            constraints.append(
                WorkspaceBox(
                    constraint=(
                        f"{workspace.label}, kept {margin * 1000:.0f} mm inside its "
                        f"edges"
                    ),
                    tool=edges.tool,
                    minimum=Point3(
                        *np.where(tool < minimum + margin, minimum, minimum + margin),
                        reference_frame=edges.minimum.reference_frame,
                    ),
                    maximum=Point3(
                        *np.where(tool > maximum - margin, maximum, maximum - margin),
                        reference_frame=edges.minimum.reference_frame,
                    ),
                )
            )
        return SafetyEnvelope(constraints=constraints)

    def arm_outside_its_workspace(
        self, context: TaskContext
    ) -> Optional[ArmOutsideItsWorkspace]:
        """
        :param context: The world and the performing robot, standing where the plan
            starts.
        :return: The first arm whose tool stands outside the workspace box the run's
            monitor holds it to, which stops the run at its first motion, with how far
            outside along the axis it is farthest out; None when every tool stands
            inside its box.
        """
        for workspace in self.limits.workspaces:
            edges = self.workspace_box(context, workspace)
            tool = self.tool_position(context, edges)
            below = edges.minimum.to_np()[:3] - tool
            above = tool - edges.maximum.to_np()[:3]
            if max(below.max(), above.max()) <= 0.0:
                continue
            plan_tool = np.linalg.inv(self.world_T_plan()) @ (
                context.world.compute_forward_kinematics_np(
                    context.world.root, edges.tool
                )[:, 3]
            )
            box = workspace.box
            axis = int(np.argmax(np.maximum(below, above)))
            lower = (box.min_x, box.min_y, box.min_z)[axis]
            upper = (box.max_x, box.max_y, box.max_z)[axis]
            return ArmOutsideItsWorkspace(
                arm=workspace.arm,
                axis="xyz"[axis],
                position=float(plan_tool[axis]),
                bound=lower if below[axis] > above[axis] else upper,
            )
        return None

    @staticmethod
    def tool_position(context: TaskContext, box: WorkspaceBox) -> np.ndarray:
        """
        :param context: The world and the performing robot.
        :param box: A workspace box of one of its arms.
        :return: Where the box's tool stands now, in the frame of the box.
        """
        return context.world.compute_forward_kinematics_np(
            box.minimum.reference_frame, box.tool
        )[:3, 3]

    def world_T_plan(self) -> np.ndarray:
        """
        :return: The pose the plan stands the robot at, in the world's frame.
        """
        pose = self.site.pose
        return HomogeneousTransformationMatrix.from_xyz_quaternion(
            pose.x, pose.y, pose.z, pose.qx, pose.qy, pose.qz, pose.qw
        ).to_np()

    def workspace_box(
        self, context: TaskContext, workspace: ArmWorkspace
    ) -> WorkspaceBox:
        """
        :param context: The world and the performing robot.
        :param workspace: The workspace of one of its arms.
        :return: The box the arm's tool stays inside: the smallest box in the frame of
            the robot's root holding the workspace, exactly it when both frames share
            their axes.
        """
        world, robot = context.world, context.robot
        [arm] = RobotArms(robot).arms(ENUM_NAMES.member(Arms, workspace.arm))
        root_T_plan = (
            np.linalg.inv(world.compute_forward_kinematics_np(world.root, robot.root))
            @ self.world_T_plan()
        )
        box = workspace.box
        corners = np.array(
            [
                (root_T_plan @ [x, y, z, 1.0])[:3]
                for x, y, z in itertools.product(
                    (box.min_x, box.max_x),
                    (box.min_y, box.max_y),
                    (box.min_z, box.max_z),
                )
            ]
        )
        return WorkspaceBox(
            constraint=workspace.label,
            tool=arm.end_effector.tool_frame,
            minimum=Point3(*corners.min(axis=0), reference_frame=robot.root),
            maximum=Point3(*corners.max(axis=0), reference_frame=robot.root),
        )

    @property
    def maximum_tool_velocity(self) -> Optional[float]:
        """
        The speed, in metres per second, no tool of the run may exceed; None sets none.
        """
        if (
            self.on_the_real_robot
            and self.limits.maximum_real_robot_tool_velocity is not None
        ):
            return self.limits.maximum_real_robot_tool_velocity
        return self.limits.maximum_tool_velocity

    def velocity_limits(self, context: TaskContext) -> List[SafetyConstraint]:
        """
        :param context: The world and the performing robot.
        :return: For every arm, its joints held to the velocity limits the robot's
            description sets each of them, and the speed its tool may not exceed, as
            far as it is set.
        """
        robot = context.robot
        arms = RobotArms(robot)
        tool_velocity = self.maximum_tool_velocity
        constraints: List[SafetyConstraint] = []
        for arm in robot.all_arms:
            side = ENUM_NAMES.member_identifier(Arms, arms.find_side(arm))
            constraints.append(
                MaximumJointVelocity(
                    constraint=f"{side} arm joint velocity",
                    connections=[
                        connection
                        for connection in arm.connections
                        if isinstance(connection, ActiveConnection1DOF)
                    ],
                )
            )
            if tool_velocity is not None:
                constraints.append(
                    MaximumToolVelocity(
                        constraint=f"{side} tool velocity ({tool_velocity} m/s)",
                        root_link=robot.root,
                        tool=arm.end_effector.tool_frame,
                        maximum_velocity=tool_velocity,
                    )
                )
        return constraints
