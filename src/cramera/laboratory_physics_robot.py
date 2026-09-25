"""Force-limited PR2 articulation in the laboratory's MuJoCo contact world."""

from __future__ import annotations

import copy
import json
import math
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation
from typing_extensions import Any

from cramera import paths
from cramera.laboratory_physics import LaboratoryCollisionModel, LaboratoryPhysics


# %% robot assets and control limits
class RobotPhysicsAsset(StrEnum):
    """Local PR2 descriptions and recording metadata."""

    RECORDING = "precision_lab_pr2"
    """Native CRAM recording supplying the initial joint posture."""
    SOURCE_SCENE = "pr2_breakfast_c"
    """Optional scene bundle containing original PR2 collision descriptions."""
    DESCRIPTION = "pr2_with_ft2_cableguide.urdf"
    """Original robot description retaining masses and collision meshes."""
    SCENE = "scene.json"
    """Visual recording manifest."""
    TRAJECTORY = "trajectory.json"
    """Native reference joint motion."""
    FRAMES = "frames"
    """Actual joint positions returned to the renderer."""


@dataclass(frozen=True)
class RobotPhysicsParameters:
    """Bounded position servos for the stationary-base manipulation prototype."""

    arm_stiffness: float = 6000.0
    """Arm servo stiffness in Newton meters per radian."""
    arm_damping: float = 70.0
    """Arm servo damping in Newton meter seconds per radian."""
    arm_torque: float = 120.0
    """Maximum torque of each arm servo in Newton meters."""
    torso_stiffness: float = 20000.0
    """Torso servo stiffness in Newtons per meter."""
    torso_damping: float = 300.0
    """Torso servo damping in Newton seconds per meter."""
    torso_force: float = 1500.0
    """Maximum torso servo force in Newtons."""
    finger_stiffness: float = 2.0
    """Finger servo stiffness in Newton meters per radian."""
    finger_damping: float = 0.04
    """Finger servo damping in Newton meter seconds per radian."""
    finger_torque: float = 0.1
    """Maximum torque of each canonical finger servo in Newton meters."""
    finger_friction: float = 0.8
    """Sliding friction coefficient of the rubber fingertip collision surfaces."""
    noslip_iterations: int = 3
    """Friction correction passes suppressing slow drift of a held glass."""


@dataclass(frozen=True)
class RobotJoint:
    """Compiled coordinates and actuator for one physical robot joint."""

    name: str
    """Joint name shared by the visual recording and MuJoCo."""
    position_index: int
    """Scalar position offset in MuJoCo's dynamic state."""
    actuator_index: int | None
    """Position actuator index, absent for mechanically coupled mimic joints."""
    lower: float
    """Lower command limit, or negative infinity for continuous rotation."""
    upper: float
    """Upper command limit, or infinity for continuous rotation."""


# %% articulated collision import
@dataclass
class LaboratoryRobotCollisionModel(LaboratoryCollisionModel):
    """Combine authored laboratory collisions with the PR2's physical link tree.

    The base is stationary. Robot self-collision is excluded in this prototype;
    all imported robot collision surfaces interact with laboratory objects.
    """

    robot_directory: Path
    """Recording directory containing renderer-compatible link transforms."""
    robot_description: Path
    """Original URDF retaining inertials, collision geometry and mimic relations."""
    robot_parameters: RobotPhysicsParameters
    """Bounded servo and fingertip material configuration."""
    initial_positions: dict[str, float]
    """Native initial posture in recording joint names."""
    actuators: ElementTree.Element = field(init=False)
    """Generated bounded position actuators."""
    equalities: ElementTree.Element = field(init=False)
    """Mechanical coupling constraints for the parallel gripper fingers."""

    def build(self) -> mujoco.MjModel:
        """Compile robot articulation and loose laboratory objects together."""
        super().build()
        self.root.find("compiler").set("autolimits", "true")
        self.root.find("compiler").set("balanceinertia", "true")
        self.root.find("compiler").set("boundmass", "0.000001")
        self.root.find("compiler").set("boundinertia", "0.000000001")
        self.root.find("option").set(
            "noslip_iterations", str(self.robot_parameters.noslip_iterations)
        )
        self.actuators = ElementTree.SubElement(self.root, "actuator")
        self.equalities = ElementTree.SubElement(self.root, "equality")
        source = ElementTree.parse(self.robot_description).getroot()
        source_links = {link.attrib["name"]: link for link in source.findall("link")}
        source_joints = {
            joint.attrib["name"]: joint for joint in source.findall("joint")
        }
        manifest = json.loads(
            (self.robot_directory / RobotPhysicsAsset.SCENE).read_text()
        )
        [entry] = [item for item in manifest["models"] if item["robot"]]
        visual = ElementTree.parse(self.robot_directory / entry["urdf"]).getroot()
        children = {
            joint.find("child").attrib["link"]: joint
            for joint in visual.findall("joint")
        }
        roots = (
            set(link.attrib["name"] for link in visual.findall("link"))
            - children.keys()
        )
        pose = entry["pose"]
        base = ElementTree.SubElement(
            self.world,
            "body",
            name="robot_mount",
            pos=" ".join(map(str, pose[:3])),
            quat=" ".join(map(str, [pose[6], *pose[3:6]])),
        )
        for root in roots:
            self._add_robot_link(root, children, source_links, source_joints, base)
        return mujoco.MjModel.from_xml_string(
            ElementTree.tostring(self.root, encoding="unicode")
        )

    @staticmethod
    def _articulated(name: str) -> bool:
        """Identify the torso, arms and mechanically coupled finger joints."""
        suffix = name.removeprefix("pr2/")
        if suffix == "torso_lift_joint":
            return True
        if not suffix.startswith(("l_", "r_")):
            return False
        return suffix[2:] in {
            "shoulder_pan_joint",
            "shoulder_lift_joint",
            "upper_arm_roll_joint",
            "elbow_flex_joint",
            "forearm_roll_joint",
            "wrist_flex_joint",
            "wrist_roll_joint",
            "gripper_l_finger_joint",
            "gripper_r_finger_joint",
            "gripper_l_finger_tip_joint",
            "gripper_r_finger_tip_joint",
        }

    def _add_robot_link(
        self,
        name: str,
        children: dict[str, ElementTree.Element],
        source_links: dict[str, ElementTree.Element],
        source_joints: dict[str, ElementTree.Element],
        parent: ElementTree.Element,
    ) -> None:
        """Preserve visual kinematics while adding original link inertials and contacts."""
        joint = children.get(name)
        pose = self._pose(joint.find("origin") if joint is not None else None)
        body = ElementTree.SubElement(parent, "body", name=name, gravcomp="1", **pose)
        source_name = name.removeprefix("pr2/")
        if source_name in source_links:
            source_link = copy.deepcopy(source_links[source_name])
            source_link.set("name", name)
            self._add_inertial(body, source_link)
            for index, collision in enumerate(source_link.findall("collision")):
                collision.set("name", str(index))
            self._add_collisions(body, source_link, self.robot_description)
            for geometry in body.findall("geom"):
                geometry.set("contype", "2")
                geometry.set("conaffinity", "1")
                if "finger_tip" in name:
                    geometry.set(
                        "friction",
                        f"{self.robot_parameters.finger_friction} 0.005 0.0001",
                    )
        if joint is not None and self._articulated(joint.attrib["name"]):
            self._add_robot_joint(body, joint, source_joints)
        elif joint is not None and joint.attrib["type"] != "fixed":
            self._freeze_joint(body, joint)
        for child, connection in children.items():
            if connection.find("parent").attrib["link"] == name:
                self._add_robot_link(child, children, source_links, source_joints, body)

    def _freeze_joint(
        self, body: ElementTree.Element, joint: ElementTree.Element
    ) -> None:
        """Keep undriven wheels, sensors and transmissions at their initial posture."""
        value = self.initial_positions.get(joint.attrib["name"], 0.0)
        if value == 0:
            return
        axis = np.fromstring(joint.find("axis").attrib["xyz"], sep=" ")
        rotation = Rotation.from_quat(
            np.fromstring(body.attrib["quat"], sep=" "), scalar_first=True
        )
        if joint.attrib["type"] == "prismatic":
            position = np.fromstring(body.attrib["pos"], sep=" ") + rotation.apply(
                axis * value
            )
            body.set("pos", " ".join(map(str, position)))
        else:
            rotation = rotation * Rotation.from_rotvec(axis * value)
            body.set("quat", " ".join(map(str, rotation.as_quat(scalar_first=True))))

    @staticmethod
    def _add_inertial(body: ElementTree.Element, link: ElementTree.Element) -> None:
        """Rotate an original URDF inertia tensor into its link coordinate frame."""
        inertial = link.find("inertial")
        if inertial is None:
            return
        values = {
            key: float(value) for key, value in inertial.find("inertia").attrib.items()
        }
        tensor = np.array(
            [
                [values["ixx"], values["ixy"], values["ixz"]],
                [values["ixy"], values["iyy"], values["iyz"]],
                [values["ixz"], values["iyz"], values["izz"]],
            ]
        )
        origin = inertial.find("origin")
        angles = np.fromstring(
            origin.attrib.get("rpy", "0 0 0") if origin is not None else "0 0 0",
            sep=" ",
        )
        rotation = Rotation.from_euler("xyz", angles).as_matrix()
        tensor = rotation @ tensor @ rotation.T
        ElementTree.SubElement(
            body,
            "inertial",
            mass=inertial.find("mass").attrib["value"],
            pos=origin.attrib.get("xyz", "0 0 0") if origin is not None else "0 0 0",
            fullinertia=" ".join(
                map(
                    str,
                    [
                        tensor[0, 0],
                        tensor[1, 1],
                        tensor[2, 2],
                        tensor[0, 1],
                        tensor[0, 2],
                        tensor[1, 2],
                    ],
                )
            ),
        )

    def _add_robot_joint(
        self,
        body: ElementTree.Element,
        joint: ElementTree.Element,
        source_joints: dict[str, ElementTree.Element],
    ) -> None:
        """Add a scalar articulation and its servo or mechanical mimic constraint."""
        name = joint.attrib["name"]
        properties = {
            "name": name,
            "type": "slide" if joint.attrib["type"] == "prismatic" else "hinge",
            "axis": joint.find("axis").attrib["xyz"],
            "armature": "0.00001" if "finger" in name else "0.01",
            "damping": "0.001",
        }
        limit = joint.find("limit")
        if limit is not None:
            properties["range"] = f"{limit.attrib['lower']} {limit.attrib['upper']}"
        ElementTree.SubElement(body, "joint", **properties)
        source_joint = source_joints[name.removeprefix("pr2/")]
        mimic = source_joint.find("mimic")
        if mimic is not None:
            ElementTree.SubElement(
                self.equalities,
                "joint",
                joint1=name,
                joint2=f"pr2/{mimic.attrib['joint']}",
                polycoef=f"{mimic.attrib.get('offset', '0')} {mimic.attrib.get('multiplier', '1')} 0 0 0",
                solref="0.002 1",
                solimp="0.99 0.999 0.0001",
            )
            return
        parameters = self.robot_parameters
        if "finger" in name:
            stiffness, damping, maximum = (
                parameters.finger_stiffness,
                parameters.finger_damping,
                parameters.finger_torque,
            )
        elif "torso" in name:
            stiffness, damping, maximum = (
                parameters.torso_stiffness,
                parameters.torso_damping,
                parameters.torso_force,
            )
        else:
            stiffness, damping, maximum = (
                parameters.arm_stiffness,
                parameters.arm_damping,
                parameters.arm_torque,
            )
        actuator = {
            "name": name,
            "joint": name,
            "kp": str(stiffness),
            "kv": str(damping),
            "forcerange": f"{-maximum} {maximum}",
        }
        if "range" in properties:
            actuator["ctrlrange"] = properties["range"]
        ElementTree.SubElement(self.actuators, "position", **actuator)


# %% robot physics state
@dataclass
class LaboratoryRobotPhysics(LaboratoryPhysics):
    """Joint-servo robot and freely moving glass bodies in one contact simulation."""

    robot_directory: Path | None = None
    """Native PR2 recording providing visual kinematics and initial joint values."""
    robot_description: Path | None = None
    """Original collision URDF, resolved from the optional bundled source scene."""
    robot_parameters: RobotPhysicsParameters = field(
        default_factory=RobotPhysicsParameters
    )
    """Robot servo limits and contact material settings."""
    initial_positions: dict[str, float] = field(init=False)
    """Recorded starting values including passive visual joints."""
    robot_joints: dict[str, RobotJoint] = field(init=False, default_factory=dict)
    """Compiled physical joint and actuator indices."""
    joint_targets: dict[str, float] = field(init=False, default_factory=dict)
    """Current requested positions of independently actuated joints."""

    def __post_init__(self) -> None:
        """Resolve local robot descriptions before compiling the shared world."""
        self.robot_directory = self.robot_directory or paths.resolve_scene_directory(
            RobotPhysicsAsset.RECORDING
        )
        if self.robot_directory is None:
            raise FileNotFoundError(
                "The native PR2 laboratory recording is unavailable"
            )
        self.robot_directory = Path(self.robot_directory).resolve()
        self.robot_description = (
            Path(self.robot_description)
            if self.robot_description is not None
            else (
                paths.SCENES_SUBMODULE
                / RobotPhysicsAsset.SOURCE_SCENE
                / RobotPhysicsAsset.DESCRIPTION
            )
        )
        if not self.robot_description.is_file():
            raise FileNotFoundError(
                f"PR2 collision description is unavailable: {self.robot_description}"
            )
        trajectory = json.loads(
            (self.robot_directory / RobotPhysicsAsset.TRAJECTORY).read_text()
        )
        self.initial_positions = dict(trajectory[RobotPhysicsAsset.FRAMES][0])
        super().__post_init__()

    def _build_model(self) -> mujoco.MjModel:
        """Compile the full articulated contact model with a stationary robot base."""
        return LaboratoryRobotCollisionModel(
            self.bundle_directory,
            self.scene,
            self.parameters,
            self.robot_directory,
            self.robot_description,
            self.robot_parameters,
            self.initial_positions,
        ).build()

    def reset(self) -> None:
        """Restore authored glass poses and the recorded initial robot posture."""
        super().reset()
        self.robot_joints = {}
        self.joint_targets = {}
        for index in range(self.model.njnt):
            name = self.model.joint(index).name
            if not name.startswith("pr2/"):
                continue
            position_index = int(self.model.jnt_qposadr[index])
            actuator = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            lower, upper = (
                self.model.jnt_range[index]
                if self.model.jnt_limited[index]
                else (-math.inf, math.inf)
            )
            self.robot_joints[name] = RobotJoint(
                name,
                position_index,
                actuator if actuator >= 0 else None,
                float(lower),
                float(upper),
            )
            value = float(np.clip(self.initial_positions.get(name, 0.0), lower, upper))
            self.data.qpos[position_index] = value
            if actuator >= 0:
                self.joint_targets[name] = value
                self.data.ctrl[actuator] = value
        mujoco.mj_forward(self.model, self.data)

    def set_joint_targets(self, positions: dict[str, float]) -> None:
        """Validate a complete command before changing any bounded actuator target."""
        targets = {}
        for name, value in positions.items():
            if (
                name not in self.robot_joints
                or self.robot_joints[name].actuator_index is None
            ):
                raise ValueError(f"Joint has no independent position actuator: {name}")
            joint = self.robot_joints[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"Joint target must be finite: {name}")
            if not joint.lower <= value <= joint.upper:
                raise ValueError(f"Joint target is outside its position limits: {name}")
            targets[name] = float(value)
        for name, value in targets.items():
            self.joint_targets[name] = value
            self.data.ctrl[self.robot_joints[name].actuator_index] = value

    def robot_state(self) -> dict[str, float]:
        """Return actual physical positions with stationary visual joint values."""
        result = self.initial_positions.copy()
        result.update(
            {
                name: float(self.data.qpos[joint.position_index])
                for name, joint in self.robot_joints.items()
            }
        )
        return result

    def snapshot(self) -> dict[str, Any]:
        """Publish authoritative articulated positions with loose-object contacts."""
        result = super().snapshot()
        result[RobotPhysicsAsset.FRAMES] = self.robot_state()
        return result
