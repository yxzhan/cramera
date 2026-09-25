"""Build the precision laboratory and its independently movable GLB assets.

Run with Blender 4.0.2::

    blender --background --python cramera/tools/laboratory/build_blender.py -- \
        --output /home/hassouna/.cramera/scenes/precision_lab --samples 160
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from math import cos, pi, sin
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import LatheMesh, ProfilePoint, TubeDimensions


# %% Asset contract
class AssetName(StrEnum):
    """Files loaded as separate rigid bodies by the laboratory scene."""

    ENVIRONMENT = "environment"
    DRAWER = "drawer"
    TUBE_CLEAR = "tube_clear"
    TUBE_AMBER = "tube_amber"
    TUBE_TEAL = "tube_teal"
    STOPPER = "stopper"


class RenderView(StrEnum):
    """Reference cameras available for selective rerendering."""

    ALL = "all"
    OVERVIEW = "overview"
    CLOSEUP = "closeup"


class MaterialName(StrEnum):
    """Shared finishes in the authored laboratory."""

    WHITE = "Warm white enamel"
    TEAL = "Petrol powder coat"
    PALE_TEAL = "Pale teal powder coat"
    STEEL = "Satin stainless steel"
    DARK = "Graphite elastomer"
    TOP = "Porcelain work surface"
    FLOOR = "Warm grey flooring"
    WALL = "Ivory wall"
    GLASS = "Borosilicate glass"
    AMBER = "Amber solution"
    LIQUID_TEAL = "Teal solution"
    LABEL = "Ceramic graduation ink"
    LIGHT = "Diffuser"


@dataclass(frozen=True)
class BuildOptions:
    """Output and render quality controls for reproducible generation."""

    output: Path
    """Directory containing the scene and its assets."""
    samples: int = 160
    """Maximum Cycles samples for each reference image."""
    width: int = 1600
    """Horizontal output resolution."""
    render: bool = True
    """Whether to render the two authored cameras."""
    view: RenderView = RenderView.ALL
    """Reference view to render after asset generation."""


@dataclass
class MaterialLibrary:
    """Exportable Principled BSDF finishes with shared instances."""

    materials: dict[MaterialName, bpy.types.Material] = field(default_factory=dict)
    """Materials indexed by their visible finish name."""

    def create(
        self,
        name: MaterialName,
        colour: tuple[float, float, float],
        roughness: float,
        metalness: float = 0,
        transmission: float = 0,
        index_of_refraction: float = 1.5,
    ) -> bpy.types.Material:
        """Create a physically based finish suitable for GLB and Cycles."""
        material = bpy.data.materials.new(name.value)
        material.use_nodes = True
        material.diffuse_color = (*colour, 1)
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*colour, 1)
        shader.inputs["Roughness"].default_value = roughness
        shader.inputs["Metallic"].default_value = metalness
        shader.inputs["Transmission Weight"].default_value = transmission
        shader.inputs["IOR"].default_value = index_of_refraction
        self.materials[name] = material
        return material

    def build(self) -> None:
        """Create the laboratory palette and optical materials."""
        self.create(MaterialName.WHITE, (0.78, 0.83, 0.82), 0.27)
        self.create(MaterialName.TEAL, (0.027, 0.145, 0.154), 0.28, 0.18)
        self.create(MaterialName.PALE_TEAL, (0.33, 0.54, 0.52), 0.28, 0.15)
        self.create(MaterialName.STEEL, (0.46, 0.52, 0.53), 0.26, 0.9)
        self.create(MaterialName.DARK, (0.017, 0.025, 0.027), 0.44)
        self.create(MaterialName.TOP, (0.80, 0.85, 0.83), 0.23)
        self.create(MaterialName.FLOOR, (0.44, 0.47, 0.45), 0.63)
        self.create(MaterialName.WALL, (0.67, 0.73, 0.70), 0.73)
        self.create(MaterialName.GLASS, (0.94, 0.985, 0.99), 0.045, 0, 1, 1.47)
        self.create(MaterialName.AMBER, (0.82, 0.32, 0.055), 0.045, 0, 0.92, 1.333)
        self.create(MaterialName.LIQUID_TEAL, (0.12, 0.56, 0.52), 0.04, 0, 0.93, 1.333)
        self.create(MaterialName.LABEL, (0.63, 0.70, 0.69), 0.45)
        diffuser = self.create(MaterialName.LIGHT, (0.94, 0.98, 1), 0.28)
        shader = diffuser.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Emission Color"].default_value = (0.75, 0.9, 1, 1)
        shader.inputs["Emission Strength"].default_value = 1.5


# %% Modelling operations
@dataclass
class LaboratoryBuilder:
    """Construct measured meshes, exported rigid bodies and reference cameras."""

    options: BuildOptions
    """Generation and render settings."""
    library: MaterialLibrary = field(default_factory=MaterialLibrary)
    """Reusable laboratory finishes."""
    objects: dict[AssetName, list[bpy.types.Object]] = field(default_factory=dict)
    """Meshes belonging to each independently exported body."""
    current: AssetName = AssetName.ENVIRONMENT
    """Body receiving newly created meshes."""
    measurements: dict[str, dict] = field(default_factory=dict)
    """Bounds captured before the bodies are posed for rendering."""

    def register(
        self, scene_object: bpy.types.Object, name: str, material: MaterialName
    ) -> bpy.types.Object:
        """Name and register a mesh with one shared material."""
        scene_object.name = name
        scene_object.data.materials.append(self.library.materials[material])
        self.objects.setdefault(self.current, []).append(scene_object)
        return scene_object

    def box(
        self,
        name: str,
        centre: tuple[float, float, float],
        size: tuple[float, float, float],
        material: MaterialName,
        bevel: float = 0.002,
    ) -> bpy.types.Object:
        """Create a manufactured rectangular body with rounded edge highlights."""
        bpy.ops.mesh.primitive_cube_add(size=1, location=centre)
        scene_object = self.register(bpy.context.object, name, material)
        scene_object.dimensions = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        if bevel:
            modifier = scene_object.modifiers.new("Machined edges", "BEVEL")
            modifier.width = bevel
            modifier.segments = 3
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            scene_object.data.use_auto_smooth = True
            modifier = scene_object.modifiers.new("Surface normals", "WEIGHTED_NORMAL")
            modifier.keep_sharp = True
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        return scene_object

    def cylinder(
        self,
        name: str,
        centre: tuple[float, float, float],
        radius: float,
        height: float,
        material: MaterialName,
    ) -> bpy.types.Object:
        """Create a cylindrical component with clean side normals."""
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=64, radius=radius, depth=height, location=centre
        )
        scene_object = self.register(bpy.context.object, name, material)
        for face in scene_object.data.polygons:
            face.use_smooth = len(face.vertices) == 4
        return scene_object

    def lathe(
        self,
        name: str,
        profile: tuple[ProfilePoint, ...],
        material: MaterialName,
        location: tuple[float, float, float] = (0, 0, 0),
        segments: int = 96,
    ) -> bpy.types.Object:
        """Create a smooth vessel from an explicitly dimensioned cross section."""
        surface = LatheMesh.from_profile(profile, segments)
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(surface.vertices, [], surface.faces)
        mesh.update()
        scene_object = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(scene_object)
        scene_object.location = location
        for face in mesh.polygons:
            face.use_smooth = True
        return self.register(scene_object, name, material)

    def text(
        self,
        name: str,
        content: str,
        location: tuple[float, float, float],
        size: float,
        material: MaterialName,
        rotation: tuple[float, float, float] = (pi / 2, 0, 0),
    ) -> bpy.types.Object:
        """Add physical lettering which remains visible after GLB export."""
        bpy.ops.object.text_add(location=location, rotation=rotation)
        scene_object = bpy.context.object
        scene_object.data.body = content
        scene_object.data.size = size
        scene_object.data.extrude = 0.00003
        scene_object.data.space_character = 1.1
        bpy.ops.object.convert(target="MESH")
        return self.register(bpy.context.object, name, material)

    def handle(self, name: str, centre: tuple[float, float, float]) -> None:
        """Build a U-shaped horizontal cabinet pull."""
        x, y, z = centre
        self.box(
            name, (x, y - 0.02, z), (0.14, 0.014, 0.014), MaterialName.STEEL, 0.004
        )
        for offset in (-0.061, 0.061):
            self.box(
                name + " mounting",
                (x + offset, y - 0.007, z),
                (0.012, 0.025, 0.014),
                MaterialName.STEEL,
            )

    def drill(
        self, plate: bpy.types.Object, centre: tuple[float, float, float], radius: float
    ) -> None:
        """Cut a true vertical through-hole into a rack plate."""
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=64, radius=radius, depth=0.03, location=centre
        )
        cutter = bpy.context.object
        bpy.context.view_layer.objects.active = plate
        modifier = plate.modifiers.new("Open tube socket", "BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.object = cutter
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(cutter, do_unlink=True)

    def build_rack(self) -> None:
        """Build six unobstructed sockets above a shallow support plate."""
        x, y, z = 0.22, -0.04, 0.900
        self.box(
            "Rack base", (x, y, z + 0.003), (0.19, 0.11, 0.006), MaterialName.PALE_TEAL
        )
        plate = self.box(
            "Rack perforated plate",
            (x, y, z + 0.065),
            (0.19, 0.11, 0.006),
            MaterialName.TEAL,
        )
        for column, offset_x in enumerate((-0.06, 0, 0.06)):
            for row, offset_y in enumerate((-0.025, 0.025)):
                self.drill(plate, (x + offset_x, y + offset_y, z + 0.065), 0.0115)
                self.text(
                    "Socket identifier",
                    f"{chr(65 + row)}{column + 1}",
                    (x + offset_x - 0.009, y + offset_y - 0.022, z + 0.0681),
                    0.006,
                    MaterialName.WHITE,
                    (0, 0, 0),
                )
        for offset_x in (-0.083, 0.083):
            for offset_y in (-0.045, 0.045):
                self.cylinder(
                    "Rack standoff",
                    (x + offset_x, y + offset_y, z + 0.034),
                    0.004,
                    0.056,
                    MaterialName.STEEL,
                )

    def build_bench(self) -> None:
        """Build a worktop and cabinets around the open sliding-drawer bay."""
        self.box(
            "Porcelain worktop",
            (0, 0, 0.876),
            (1.8, 0.75, 0.048),
            MaterialName.TOP,
            0.006,
        )
        self.box(
            "Rear splashback",
            (0, 0.352, 0.958),
            (1.78, 0.025, 0.116),
            MaterialName.TOP,
            0.003,
        )
        self.box(
            "Recessed plinth", (0, 0.027, 0.083), (1.66, 0.59, 0.12), MaterialName.DARK
        )
        for x in (-0.75, 0.75):
            for y in (-0.23, 0.23):
                self.cylinder(
                    "Adjustable foot", (x, y, 0.03), 0.025, 0.06, MaterialName.DARK
                )
        for x in (-0.81, 0.81):
            self.box(
                "Cabinet side",
                (x, 0.02, 0.468),
                (0.026, 0.654, 0.684),
                MaterialName.WHITE,
            )
        self.box(
            "Cabinet rear", (0, 0.33, 0.47), (1.62, 0.023, 0.7), MaterialName.WHITE
        )
        self.box(
            "Upper cross member",
            (0, 0.02, 0.813),
            (1.62, 0.64, 0.05),
            MaterialName.WHITE,
        )
        for x in (-0.225, 0.24):
            self.box(
                "Cabinet partition",
                (x, 0.015, 0.464),
                (0.021, 0.63, 0.675),
                MaterialName.WHITE,
            )
        self.box(
            "Drawer lower shelf",
            (-0.52, 0.006, 0.597),
            (0.558, 0.616, 0.018),
            MaterialName.WHITE,
        )
        self.box(
            "Lower left cabinet",
            (-0.52, -0.313, 0.352),
            (0.558, 0.023, 0.451),
            MaterialName.TEAL,
        )
        self.handle("Lower cabinet pull", (-0.52, -0.326, 0.546))
        for x in (0.005, 0.523):
            self.box(
                "Cabinet door",
                (x, -0.313, 0.469),
                (0.436 if x < 0.1 else 0.552, 0.024, 0.681),
                MaterialName.WHITE,
            )
            self.handle("Cabinet door pull", (x, -0.326, 0.744))
        for x in (-0.79, -0.25):
            self.box(
                "Drawer guide",
                (x, 0.015, 0.669),
                (0.009, 0.582, 0.014),
                MaterialName.STEEL,
            )
        self.text(
            "Workstation number",
            "LAB / 01",
            (0.536, -0.327, 0.17),
            0.023,
            MaterialName.TEAL,
        )

    def build_room(self) -> None:
        """Dress the bench with a restrained architectural laboratory setting."""
        self.box("Floor", (0, 0.2, -0.035), (4.6, 4.4, 0.07), MaterialName.FLOOR)
        self.box("Back wall", (0, 1.15, 1.4), (4.6, 0.08, 2.8), MaterialName.WALL)
        self.box("Left wall", (-1.85, 0.05, 1.4), (0.08, 2.2, 2.8), MaterialName.WALL)
        for x in (-1.5, -0.75, 0, 0.75, 1.5):
            self.box(
                "Wall panel seam",
                (x, 1.107, 1.16),
                (0.003, 0.004, 2.3),
                MaterialName.WHITE,
                0,
            )
        self.box(
            "Wall skirting", (0, 1.093, 0.074), (3.66, 0.016, 0.12), MaterialName.STEEL
        )
        self.box("Shelf", (-0.26, 0.93, 1.5), (1.69, 0.29, 0.022), MaterialName.WHITE)
        self.box(
            "Shelf light",
            (-0.26, 0.854, 1.487),
            (1.49, 0.033, 0.006),
            MaterialName.LIGHT,
        )
        for x in (-0.95, 0.40):
            self.box(
                "Shelf bracket",
                (x, 1.035, 1.406),
                (0.012, 0.16, 0.17),
                MaterialName.STEEL,
            )
        self.text(
            "Laboratory title",
            "PRECISION",
            (-0.86, 1.102, 1.96),
            0.135,
            MaterialName.TEAL,
        )
        self.text(
            "Laboratory subtitle",
            "L A B O R A T O R Y   /   0 1",
            (-0.852, 1.101, 1.897),
            0.033,
            MaterialName.TEAL,
        )
        for x, height in ((-0.86, 0.16), (-0.69, 0.21), (-0.49, 0.16)):
            self.build_bottle(x, 0.955, 1.512, height)
        self.box(
            "Storage box",
            (0.02, 0.95, 1.595),
            (0.38, 0.235, 0.16),
            MaterialName.PALE_TEAL,
        )
        self.box(
            "Storage box lid",
            (0.02, 0.95, 1.68),
            (0.392, 0.245, 0.017),
            MaterialName.TEAL,
        )
        self.text(
            "Storage label",
            "GLASSWARE",
            (-0.1, 0.831, 1.583),
            0.021,
            MaterialName.WHITE,
        )
        self.box(
            "Ceiling task light",
            (0, 0.05, 2.42),
            (1.5, 0.22, 0.036),
            MaterialName.WHITE,
        )
        self.box(
            "Task light diffuser",
            (0, 0.05, 2.399),
            (1.44, 0.19, 0.004),
            MaterialName.LIGHT,
        )
        for x in (-0.6, 0.6):
            self.cylinder(
                "Light suspension", (x, 0.05, 2.61), 0.003, 0.35, MaterialName.STEEL
            )
        self.build_rack()
        self.build_bottle(-0.69, 0.21, 0.900, 0.175)
        self.build_bottle(-0.53, 0.22, 0.900, 0.135)
        self.box(
            "Instrument base",
            (0.65, 0.19, 0.931),
            (0.30, 0.22, 0.058),
            MaterialName.WHITE,
            0.014,
        )
        self.cylinder(
            "Balance weighing pan",
            (0.65, 0.21, 0.968),
            0.085,
            0.012,
            MaterialName.STEEL,
        )
        self.box(
            "Balance display",
            (0.65, 0.078, 0.94),
            (0.125, 0.003, 0.023),
            MaterialName.DARK,
        )
        self.text(
            "Balance digits",
            "0.000 g",
            (0.6, 0.075, 0.933),
            0.015,
            MaterialName.PALE_TEAL,
        )
        self.box(
            "Work tray",
            (-0.22, 0.05, 0.908),
            (0.25, 0.18, 0.012),
            MaterialName.WHITE,
            0.008,
        )
        for x in (-0.285, -0.235, -0.185):
            self.cylinder(
                "Pipette body", (x, 0.04, 0.928), 0.005, 0.041, MaterialName.WHITE
            )
        self.text(
            "Worktop station label",
            "01  /  SAMPLE PREPARATION",
            (-0.76, -0.376, 0.863),
            0.018,
            MaterialName.TEAL,
        )

    def build_bottle(self, x: float, y: float, base: float, height: float) -> None:
        """Create capped reagent bottles for static room dressing."""
        radius = height * 0.23
        profile = (
            ProfilePoint(0, 0),
            ProfilePoint(radius * 0.9, 0),
            ProfilePoint(radius, 0.006),
            ProfilePoint(radius, height * 0.68),
            ProfilePoint(radius * 0.55, height * 0.81),
            ProfilePoint(radius * 0.55, height * 0.91),
            ProfilePoint(0, height * 0.91),
        )
        self.lathe("Reagent bottle", profile, MaterialName.WHITE, (x, y, base))
        self.cylinder(
            "Bottle screw cap",
            (x, y, base + height * 0.935),
            radius * 0.64,
            height * 0.13,
            MaterialName.TEAL,
        )
        self.box(
            "Bottle label",
            (x, y - radius - 0.0004, base + height * 0.41),
            (radius * 1.33, 0.0007, height * 0.32),
            MaterialName.PALE_TEAL,
            0.0002,
        )
        self.text(
            "Bottle lettering",
            "BUFFER",
            (x - radius * 0.53, y - radius - 0.001, base + height * 0.45),
            height * 0.071,
            MaterialName.WHITE,
        )
        self.text(
            "Bottle specification",
            "pH 7.4",
            (x - radius * 0.45, y - radius - 0.001, base + height * 0.32),
            height * 0.057,
            MaterialName.WHITE,
        )

    def build_drawer(self) -> None:
        """Build a hollow tray centred on the prismatic joint origin."""
        self.current = AssetName.DRAWER
        self.box(
            "Drawer floor", (0, 0, -0.064), (0.526, 0.596, 0.012), MaterialName.STEEL
        )
        for x in (-0.269, 0.269):
            self.box("Drawer side", (x, 0, 0), (0.012, 0.62, 0.14), MaterialName.STEEL)
        self.box("Drawer back", (0, 0.304, 0), (0.55, 0.012, 0.14), MaterialName.STEEL)
        self.box("Drawer face", (0, -0.304, 0), (0.55, 0.012, 0.14), MaterialName.TEAL)
        self.box("Drawer mat", (0, 0, -0.056), (0.517, 0.581, 0.003), MaterialName.DARK)
        for x in (-0.09, 0.09):
            self.box(
                "Tray divider", (x, 0, -0.021), (0.005, 0.57, 0.065), MaterialName.STEEL
            )
        self.handle("Drawer pull", (0, -0.316, 0.015))

    def build_tube(self, asset: AssetName, liquid: MaterialName | None) -> None:
        """Create one hollow glass vessel with separate optional contents."""
        self.current = asset
        dimensions = TubeDimensions()
        self.lathe("Hollow borosilicate tube", dimensions.profile(), MaterialName.GLASS)
        for number in range(1, 7):
            height = 0.03 + number * 0.014
            self.box(
                "Volume graduation",
                (-0.001, -0.00903, height),
                (0.004 if number % 2 else 0.006, 0.00008, 0.00045),
                MaterialName.LABEL,
                0,
            )
        if liquid is None:
            return
        height = 0.074 if liquid == MaterialName.AMBER else 0.095
        radius = dimensions.inner_radius - 0.0001
        profile = [ProfilePoint(0, 0.0014)]
        for index in range(1, 17):
            angle = -pi / 2 + pi / 2 * index / 16
            profile.append(
                ProfilePoint(radius * cos(angle), 0.0091 + radius * sin(angle))
            )
        profile.extend(
            (
                ProfilePoint(radius, height + 0.0007),
                ProfilePoint(radius * 0.9, height),
                ProfilePoint(0, height),
            )
        )
        self.lathe("Visible liquid fill", tuple(profile), liquid)

    def build_stopper(self) -> None:
        """Create a removable stem which fits the glass inner diameter."""
        self.current = AssetName.STOPPER
        profile = (
            ProfilePoint(0, 0),
            ProfilePoint(0.00735, 0),
            ProfilePoint(0.0076, 0.0115),
            ProfilePoint(0.0098, 0.0115),
            ProfilePoint(0.0105, 0.0122),
            ProfilePoint(0.0105, 0.020),
            ProfilePoint(0.0098, 0.021),
            ProfilePoint(0, 0.021),
        )
        self.lathe("Silicone stopper", profile, MaterialName.TEAL)

    def export_asset(
        self, asset: AssetName, placement: tuple[float, float, float]
    ) -> None:
        """Bake mesh transforms and export the local Z-up rigid body."""
        bpy.ops.object.select_all(action="DESELECT")
        objects = self.objects[asset]
        for scene_object in objects:
            scene_object.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        vertices = [
            scene_object.matrix_world @ vertex.co
            for scene_object in objects
            for vertex in scene_object.data.vertices
        ]
        lower = [min(point[axis] for point in vertices) for axis in range(3)]
        upper = [max(point[axis] for point in vertices) for axis in range(3)]
        self.measurements[asset.value] = {
            "minimum": lower,
            "maximum": upper,
            "dimensions": [upper[axis] - lower[axis] for axis in range(3)],
            "meshes": len(objects),
            "vertices": len(vertices),
        }
        bpy.ops.export_scene.gltf(
            filepath=str(self.options.output / "assets" / f"{asset.value}.glb"),
            export_format="GLB",
            use_selection=True,
            export_yup=False,
            export_apply=True,
            export_animations=False,
            export_cameras=False,
            export_lights=False,
            export_materials="EXPORT",
        )
        body = bpy.data.objects.new(asset.value, None)
        bpy.context.collection.objects.link(body)
        body.empty_display_size = 0.035
        body.location = placement
        for scene_object in objects:
            scene_object.parent = body
        if asset == AssetName.DRAWER:
            constraint = body.constraints.new("LIMIT_LOCATION")
            constraint.name = "Prismatic drawer travel"
            constraint.use_min_x = constraint.use_max_x = True
            constraint.use_min_y = constraint.use_max_y = True
            constraint.use_min_z = constraint.use_max_z = True
            constraint.min_x = constraint.max_x = placement[0]
            constraint.min_y, constraint.max_y = -0.36, 0
            constraint.min_z = constraint.max_z = placement[2]
            constraint.use_transform_limit = True

    def area_light(
        self,
        name: str,
        position: tuple[float, float, float],
        target: tuple[float, float, float],
        energy: float,
        size: float,
        colour: tuple[float, float, float] = (1, 1, 1),
    ) -> None:
        """Place a soft studio luminaire aimed at the work surface."""
        light = bpy.data.lights.new(name, "AREA")
        light.energy = energy
        light.shape = "DISK"
        light.size = size
        light.color = colour
        scene_object = bpy.data.objects.new(name, light)
        bpy.context.collection.objects.link(scene_object)
        scene_object.location = position
        scene_object.rotation_euler = (
            (Vector(target) - scene_object.location).to_track_quat("-Z", "Y").to_euler()
        )

    def camera(
        self,
        name: str,
        position: tuple[float, float, float],
        target: tuple[float, float, float],
        lens: float,
        aperture: float,
    ) -> bpy.types.Object:
        """Create a perspective camera focused on the relevant work area."""
        camera = bpy.data.cameras.new(name)
        scene_object = bpy.data.objects.new(name, camera)
        bpy.context.collection.objects.link(scene_object)
        scene_object.location = position
        scene_object.rotation_euler = (
            (Vector(target) - scene_object.location).to_track_quat("-Z", "Y").to_euler()
        )
        camera.lens = lens
        camera.clip_start = 0.005
        camera.dof.use_dof = True
        camera.dof.focus_distance = (Vector(target) - scene_object.location).length
        camera.dof.aperture_fstop = aperture
        return scene_object

    def setup_render(self) -> tuple[bpy.types.Object, bpy.types.Object]:
        """Configure Cycles, broad window light and detail photography."""
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "CUDA"
        preferences.get_devices()
        accelerators = [
            device for device in preferences.devices if device.type == "CUDA"
        ]
        if accelerators:
            for device in preferences.devices:
                device.use = device.type == "CUDA"
            scene.cycles.device = "GPU"
        scene.cycles.samples = self.options.samples
        scene.cycles.use_denoising = False
        scene.cycles.max_bounces = 16
        scene.cycles.transmission_bounces = 12
        scene.cycles.transparent_max_bounces = 12
        scene.cycles.adaptive_threshold = 0.015
        scene.render.resolution_x = self.options.width
        scene.render.resolution_y = round(self.options.width * 0.68)
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (
            0.72,
            0.82,
            0.92,
            1,
        )
        scene.world.node_tree.nodes["Background"].inputs[
            "Strength"
        ].default_value = 0.28
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
        scene.view_settings.exposure = -1.8
        self.area_light(
            "Window key", (-1.35, -1.05, 2.5), (0, 0, 0.8), 360, 2.2, (0.88, 0.95, 1)
        )
        self.area_light(
            "Right reflection",
            (1.35, 0.25, 2),
            (0.1, 0, 0.95),
            170,
            1.2,
            (1, 0.90, 0.79),
        )
        self.area_light(
            "Front glass reflection", (0.2, -1.9, 1.4), (0.2, 0, 1), 90, 1.0
        )
        self.area_light("Overhead practical", (0, 0.02, 2.37), (0, 0, 0.9), 100, 1.2)
        overview = self.camera(
            "Overview", (2.65, -3.6, 2.42), (-0.04, 0.12, 1.02), 44, 8
        )
        detail = self.camera(
            "Glassware detail", (0.69, -0.77, 1.40), (0.275, -0.055, 0.946), 65, 11
        )
        return overview, detail

    def build(self) -> None:
        """Generate every body, save the source and render both reference views."""
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.context.scene.unit_settings.system = "METRIC"
        bpy.context.scene.unit_settings.scale_length = 1
        (self.options.output / "assets").mkdir(parents=True, exist_ok=True)
        self.library.build()
        self.build_bench()
        self.build_room()
        self.export_asset(AssetName.ENVIRONMENT, (0, 0, 0))
        self.build_drawer()
        self.export_asset(AssetName.DRAWER, (-0.52, -0.18, 0.69))
        for asset, material, position in (
            (AssetName.TUBE_CLEAR, None, (0.16, -0.065, 0.906)),
            (AssetName.TUBE_AMBER, MaterialName.AMBER, (0.22, -0.065, 0.906)),
            (AssetName.TUBE_TEAL, MaterialName.LIQUID_TEAL, (0.16, -0.015, 0.906)),
        ):
            self.build_tube(asset, material)
            self.export_asset(asset, position)
        self.build_stopper()
        self.export_asset(AssetName.STOPPER, (0.50, -0.12, 0.900))
        cameras = self.setup_render()
        bpy.context.scene.camera = cameras[0]
        bpy.context.scene.render.filepath = str(
            self.options.output / "render_overview.png"
        )
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(
            filepath=str(self.options.output / "laboratory.blend")
        )
        (self.options.output / "assets" / "measurements.json").write_text(
            json.dumps(self.measurements, indent=2) + "\n"
        )
        if self.options.render:
            for camera, view in zip(cameras, (RenderView.OVERVIEW, RenderView.CLOSEUP)):
                if self.options.view not in (RenderView.ALL, view):
                    continue
                bpy.context.scene.camera = camera
                bpy.context.scene.render.filepath = str(
                    self.options.output / f"render_{view.value}.png"
                )
                bpy.ops.render.render(write_still=True)


# %% Command line
def main() -> None:
    """Read Blender's trailing arguments and build the laboratory bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / ".cramera" / "scenes" / "precision_lab",
    )
    parser.add_argument("--samples", type=int, default=BuildOptions.samples)
    parser.add_argument("--width", type=int, default=BuildOptions.width)
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument(
        "--view", type=RenderView, choices=list(RenderView), default=RenderView.ALL
    )
    arguments = parser.parse_args(
        sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    )
    LaboratoryBuilder(
        BuildOptions(
            arguments.output,
            arguments.samples,
            arguments.width,
            not arguments.skip_render,
            arguments.view,
        )
    ).build()


if __name__ == "__main__":
    main()
