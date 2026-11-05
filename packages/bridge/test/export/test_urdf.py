"""
URDF documents written from world bodies and copied from URDF files.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

import numpy
import pytest

from cramera_bridge.exceptions import (
    PathOutsideBundle,
    UnrepresentableJointLimits,
    UrdfSourceNotFound,
)
from cramera_bridge.export.assets import BundledAssets, MeshFormat
from cramera_bridge.export.urdf import (
    COORDINATE_PRECISION,
    ROOT_LINK,
    UrdfAttribute,
    UrdfBundle,
    UrdfDocument,
    UrdfElement,
    UrdfJointType,
)
from cramera_bridge.scenes.environment import SceneEnvironment
from cramera_protocol.bundle.files import BundleFile
from semantic_digital_twin.adapters.mjcf import MJCFParser
from semantic_digital_twin.datastructures.prefixed_name import PrefixedName
from krrood_spatial_math.spatial_types import Vector3
from semantic_digital_twin.world_description.connections import (
    ActiveConnection1DOF,
    PrismaticConnection,
    RevoluteConnection,
)
from semantic_digital_twin.world_description.world_entity import Body

from ..dataset.bundles import BUNDLED_CUP_URDF, HINGED_CABINET_MJCF
from ..dataset.worlds import (
    BRICKS_TEXTURE,
    CUP_MESH,
    UV_MAPPED_MESH,
    WrittenSurface,
)

MESH_DIRECTORY = "test"
"""
The folder below ``meshes`` the tests write meshes to.
"""


def document(world, directory) -> UrdfDocument:
    return UrdfDocument(
        world=world,
        assets=BundledAssets(bundle_root=directory),
        mesh_directory=MESH_DIRECTORY,
    )


def joints_of(path):
    root = ElementTree.parse(path).getroot()
    return {
        joint.attrib[UrdfAttribute.NAME]: joint
        for joint in root.iter(UrdfElement.JOINT)
    }


def links_of(path):
    root = ElementTree.parse(path).getroot()
    return {
        link.attrib[UrdfAttribute.NAME]: link for link in root.iter(UrdfElement.LINK)
    }


# %% world bodies


def test_a_kitchen_round_trips_through_its_urdf(kitchen, tmp_path):
    bodies = kitchen.world.bodies_topologically_sorted
    report = document(kitchen.world, tmp_path).write(bodies, "kitchen")
    parsed = SceneEnvironment.parse_model(tmp_path / report.urdf, prefix="")
    assert {str(body.name) for body in parsed.bodies} == {ROOT_LINK} | {
        str(body.name) for body in bodies
    }
    assert report.links == [ROOT_LINK] + [str(body.name) for body in bodies]


def test_free_bodies_become_floating_joints_with_streamed_poses(kitchen, tmp_path):
    report = document(kitchen.world, tmp_path).write(
        kitchen.world.bodies_topologically_sorted, "kitchen"
    )
    joints = joints_of(tmp_path / report.urdf)
    milk = str(kitchen.milk_connection.name)
    assert joints[milk].attrib[UrdfAttribute.TYPE] == UrdfJointType.FLOATING
    assert milk in report.posed_joints
    assert report.movable_joints == []


def test_a_body_without_shapes_gets_no_geometry(kitchen, tmp_path):
    report = document(kitchen.world, tmp_path).write(
        kitchen.world.bodies_topologically_sorted, "kitchen"
    )
    frame = links_of(tmp_path / report.urdf)[str(kitchen.frame.name)]
    assert list(frame) == []


def test_visuals_carry_the_native_colour(kitchen, tmp_path):
    report = document(kitchen.world, tmp_path).write([kitchen.milk], "milk")
    milk = links_of(tmp_path / report.urdf)[str(kitchen.milk.name)]
    rgba = milk.find("visual/material/color").attrib[UrdfAttribute.RGBA]
    assert [float(value) for value in rgba.split()] == pytest.approx(
        list(kitchen.milk.visual.shapes[0].color.to_rgba()),
        abs=10**-COORDINATE_PRECISION,
    )


def test_a_mesh_is_copied_with_its_material_library(kitchen, tmp_path):
    report = document(kitchen.world, tmp_path).write([kitchen.cup], "cup")
    mesh = links_of(tmp_path / report.urdf)[str(kitchen.cup.name)].find(
        "visual/geometry/mesh"
    )
    bundled = tmp_path / mesh.attrib[UrdfAttribute.FILENAME]
    assert bundled.read_bytes() == CUP_MESH.read_bytes()
    assert (bundled.parent / "cup.mtl").is_file()
    assert report.missing == []


# %% textures


def written_surface(directory, report, body) -> WrittenSurface:
    mesh = links_of(directory / report.urdf)[str(body.name)].find(
        "visual/geometry/mesh"
    )
    return WrittenSurface(directory / mesh.attrib[UrdfAttribute.FILENAME])


def test_a_textured_box_is_written_with_its_image_repeated_per_meter(
    textured, tmp_path
):
    report = document(textured.world, tmp_path).write([textured.wall], "wall")
    surface = written_surface(tmp_path, report, textured.wall)
    [box] = textured.wall.visual.shapes
    assert surface.path.suffix == MeshFormat.GLB
    assert numpy.array_equal(
        surface.image(), WrittenSurface.pixels_of(Path(box.texture.file_path))
    )
    texture_coordinates = surface.mesh().visual.uv
    span = texture_coordinates.max(axis=0) - texture_coordinates.min(axis=0)
    assert span[0] == pytest.approx(box.scale.x * box.texture.repeat[0])
    assert surface.mesh().extents == pytest.approx(box.scale.to_np()[:3])


def test_a_textured_box_stretches_its_image_across_each_face(textured, tmp_path):
    [box] = textured.wall.visual.shapes
    box.texture.uniform = False
    report = document(textured.world, tmp_path).write([textured.wall], "wall")
    texture_coordinates = (
        written_surface(tmp_path, report, textured.wall).mesh().visual.uv
    )
    span = texture_coordinates.max(axis=0) - texture_coordinates.min(axis=0)
    assert span == pytest.approx(box.texture.repeat)


def test_a_mesh_with_a_texture_its_file_does_not_name_carries_it(textured, tmp_path):
    report = document(textured.world, tmp_path).write([textured.panel], "panel")
    surface = written_surface(tmp_path, report, textured.panel)
    assert surface.path.suffix == MeshFormat.GLB
    assert numpy.array_equal(surface.image(), WrittenSurface.pixels_of(BRICKS_TEXTURE))
    assert surface.mesh().extents == pytest.approx(
        textured.panel.visual.shapes[0].mesh.extents
    )


def test_a_mesh_without_a_texture_is_copied_as_its_file(textured, tmp_path):
    report = document(textured.world, tmp_path).write(
        [textured.bare_panel], "bare_panel"
    )
    mesh = links_of(tmp_path / report.urdf)[str(textured.bare_panel.name)].find(
        "visual/geometry/mesh"
    )
    bundled = tmp_path / mesh.attrib[UrdfAttribute.FILENAME]
    assert bundled.read_bytes() == UV_MAPPED_MESH.read_bytes()


def test_a_textured_world_round_trips_through_its_urdf(textured, tmp_path):
    bodies = textured.world.bodies_topologically_sorted
    report = document(textured.world, tmp_path).write(bodies, "textured")
    parsed = SceneEnvironment.parse_model(tmp_path / report.urdf, prefix="")
    assert {str(body.name) for body in parsed.bodies} == {ROOT_LINK} | {
        str(body.name) for body in bodies
    }


def test_a_body_whose_parent_is_left_out_is_fixed_at_its_world_pose(kitchen, tmp_path):
    report = document(kitchen.world, tmp_path).write([kitchen.milk], "milk")
    graft = joints_of(tmp_path / report.urdf)[f"{ROOT_LINK}_to_{kitchen.milk.name}"]
    assert graft.attrib[UrdfAttribute.TYPE] == UrdfJointType.FIXED
    assert (
        graft.find(UrdfElement.ORIGIN).attrib[UrdfAttribute.TRANSLATION]
        == "1.0 0.1 0.9"
    )


def test_bodies_sharing_a_name_get_distinct_links(kitchen, tmp_path):
    twin = Body(name=PrefixedName("milk", "kitchen"))
    with kitchen.world.modify_world():
        kitchen.world.add_connection(
            type(kitchen.table.parent_connection)(parent=kitchen.table, child=twin)
        )
    report = document(kitchen.world, tmp_path).write([kitchen.milk, twin], "milks")
    assert len(set(report.links)) == 3
    assert report.links[2] == f"{twin.name}__{twin.id.hex}"


def test_a_prismatic_joint_without_limits_is_refused(kitchen, tmp_path):
    slide = Body(name=PrefixedName("slide", "kitchen"))
    with kitchen.world.modify_world():
        kitchen.world.add_connection(
            PrismaticConnection.create_with_dofs(
                world=kitchen.world,
                parent=kitchen.table,
                child=slide,
                axis=Vector3.X(reference_frame=kitchen.table),
            )
        )
    with pytest.raises(UnrepresentableJointLimits):
        document(kitchen.world, tmp_path).write([kitchen.table, slide], "slide")


def test_an_unlimited_revolute_joint_is_continuous(kitchen, tmp_path):
    wheel = Body(name=PrefixedName("wheel", "kitchen"))
    with kitchen.world.modify_world():
        connection = RevoluteConnection.create_with_dofs(
            world=kitchen.world,
            parent=kitchen.table,
            child=wheel,
            axis=Vector3.Z(reference_frame=kitchen.table),
        )
        kitchen.world.add_connection(connection)
    report = document(kitchen.world, tmp_path).write([kitchen.table, wheel], "wheel")
    joint = joints_of(tmp_path / report.urdf)[str(connection.name)]
    assert joint.attrib[UrdfAttribute.TYPE] == UrdfJointType.CONTINUOUS
    assert joint.find(UrdfElement.LIMIT) is None
    assert report.movable_joints == [str(connection.name)]


def test_a_robot_keeps_its_joints_and_limits(robot_world, tmp_path):
    world = robot_world.world
    report = document(world, tmp_path).write(
        world.bodies_topologically_sorted, "pr2", identity_root=robot_world.robot.root
    )
    parsed = SceneEnvironment.parse_model(tmp_path / report.urdf, prefix="")
    original = {
        str(connection.name): type(connection).__name__
        for connection in world.connections
    }
    # The parser prefixes every joint with the robot's name; the bare name is the
    # joint's name as the URDF writes it.
    written = {
        connection.name.name: type(connection).__name__
        for connection in parsed.connections
        if connection.name.name in original
    }
    assert written == original
    assert "pr2/torso_lift_joint" in report.movable_joints


@pytest.mark.parametrize("joint_fraction", [0.0, 0.5, 1.0])
def test_joints_away_from_their_child_keep_the_child_where_the_world_has_it(
    joint_fraction, tmp_path
):
    world = MJCFParser.from_file(str(HINGED_CABINET_MJCF)).create_world()
    report = document(world, tmp_path).write(
        world.bodies_topologically_sorted, "cabinet"
    )
    parsed = SceneEnvironment.parse_model(tmp_path / report.urdf, prefix="")
    parsed_connections = {
        connection.name.name: connection for connection in parsed.connections
    }
    for connection in world.get_connections_by_type(ActiveConnection1DOF):
        limits = connection.dof.limits
        position = limits.lower.position + joint_fraction * (
            limits.upper.position - limits.lower.position
        )
        connection.position = position
        parsed_connections[str(connection.name)].position = position
    parsed_bodies = {str(body.name): body for body in parsed.bodies}
    for body in world.bodies:
        numpy.testing.assert_allclose(
            parsed.compute_forward_kinematics_np(
                parsed.root, parsed_bodies[report.body_links[body.id]]
            ),
            world.compute_forward_kinematics_np(world.root, body),
            atol=1e-5,
        )


def test_a_joint_away_from_its_child_keeps_its_name_and_moves_a_frame_link(tmp_path):
    world = MJCFParser.from_file(str(HINGED_CABINET_MJCF)).create_world()
    report = document(world, tmp_path).write(
        world.bodies_topologically_sorted, "cabinet"
    )
    hinges = list(world.get_connections_by_type(ActiveConnection1DOF))
    assert set(report.movable_joints) == {str(connection.name) for connection in hinges}
    joints = joints_of(tmp_path / report.urdf)
    for connection in hinges:
        frame = joints[str(connection.name)].find(UrdfElement.CHILD)
        assert frame.attrib[UrdfAttribute.LINK] in report.links
        assert (
            frame.attrib[UrdfAttribute.LINK] != report.body_links[connection.child.id]
        )


def test_a_document_cannot_be_written_outside_its_bundle(kitchen, tmp_path):
    with pytest.raises(PathOutsideBundle):
        document(kitchen.world, tmp_path / "bundle").write([kitchen.milk], "../escape")


# %% existing URDF files


def test_an_existing_urdf_gets_its_meshes_and_reports_missing_ones(tmp_path):
    report = UrdfBundle(tmp_path, MESH_DIRECTORY).write(BUNDLED_CUP_URDF, "cup")
    meshes = [
        mesh.attrib[UrdfAttribute.FILENAME]
        for mesh in ElementTree.parse(report.urdf).getroot().iter(UrdfElement.MESH)
    ]
    assert meshes[0].startswith(f"{BundleFile.MESHES}/{MESH_DIRECTORY}/")
    assert (tmp_path / meshes[0]).read_bytes() == CUP_MESH.read_bytes()
    assert report.missing == ["package://no_such_cramera_package/meshes/lid.stl"]
    assert report.links == ["base", "lid"]
    assert report.movable_joints == ["lid_hinge"]


def test_a_missing_urdf_is_reported(tmp_path):
    with pytest.raises(UrdfSourceNotFound):
        UrdfBundle(tmp_path, MESH_DIRECTORY).write(tmp_path / "none.urdf", "none")
