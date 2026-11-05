"""
Textured surfaces a bundle cannot carry as a copy of a shape's own file: a primitive
with a texture, and a mesh painted with a texture its file does not name. Each is
written as binary glTF, which holds the geometry, the texture coordinates and the image
in one file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy
import PIL.Image
import trimesh
from PIL.ImageFile import ImageFile
from trimesh.visual.material import SimpleMaterial
from trimesh.visual.texture import TextureVisuals
from typing_extensions import Optional, Sequence

from cramera_bridge.export.assets import (
    DIGEST_LENGTH,
    BundledAssets,
    MaterialLibrary,
    MeshFormat,
)
from semantic_digital_twin.world_description.geometry import Mesh, Shape, Texture

# %% primitives


@dataclass(frozen=True)
class PrimitiveTexturing:
    """
    Lays a texture onto a primitive's surface.

    Every face is projected onto the plane across the axis its normal points along most,
    so each side of a box shows the image upright.
    """

    texture: Texture
    """
    The texture.
    """

    def textured(self, surface: trimesh.Trimesh) -> trimesh.Trimesh:
        """
        :param surface: The primitive's surface in its own frame.
        :return: The surface with one vertex per face corner, carrying texture
            coordinates that repeat the image as the texture states, and the image.
        """
        corners = surface.vertices[surface.faces.reshape(-1)]
        normal_axes = numpy.repeat(
            numpy.abs(surface.face_normals).argmax(axis=1), surface.faces.shape[1]
        )
        lower, upper = surface.bounds
        if self.texture.uniform:
            # repeated per meter, independent of the surface's size
            lower = numpy.zeros(len(lower))
            extent = numpy.ones(len(upper))
        else:
            extent = upper - lower
        coordinates = numpy.zeros((len(corners), len(self.texture.repeat)))
        for normal_axis in range(corners.shape[1]):
            facing = normal_axes == normal_axis
            across = [axis for axis in range(corners.shape[1]) if axis != normal_axis]
            coordinates[facing] = (corners[facing][:, across] - lower[across]) / extent[
                across
            ]
        textured = trimesh.Trimesh(
            vertices=corners,
            faces=numpy.arange(len(corners)).reshape(-1, surface.faces.shape[1]),
            process=False,
        )
        textured.visual = TextureVisuals(
            uv=coordinates * numpy.asarray(self.texture.repeat),
            material=SimpleMaterial(image=PIL.Image.open(self.texture.file_path)),
        )
        return textured


# %% shapes


@dataclass(frozen=True)
class TexturedSurface:
    """
    The textured surface of a shape whose own file, if it has one, cannot show its
    texture.
    """

    mesh: trimesh.Trimesh
    """
    The surface in the shape's own frame and at its scale, carrying its texture
    coordinates and image.
    """

    source: Path
    """
    The file the surface is drawn from: a mesh's own file, or a primitive's texture.
    """

    @classmethod
    def of(cls, shape: Shape) -> Optional[TexturedSurface]:
        """
        :param shape: A shape.
        :return: Its textured surface, or None for a shape without such a texture.
        """
        if isinstance(shape, Mesh):
            if not cls.has_applied_texture(shape):
                return None
            return cls(mesh=shape.mesh, source=shape.local_file)
        if shape.texture is None:
            return None
        return cls(
            mesh=PrimitiveTexturing(shape.texture).textured(shape.mesh),
            source=Path(shape.texture.file_path),
        )

    @classmethod
    def has_applied_texture(cls, mesh: Mesh) -> bool:
        """
        :param mesh: A mesh shape.
        :return: Whether it is painted with an image file that its own file does not
            name, so a copy of the file would show it untextured.
        """
        if cls.names_textures(mesh.local_file):
            return False
        visual = mesh.mesh.visual
        # trimesh gives a mesh with texture coordinates but no material a generated
        # placeholder image, which is no texture
        return (
            isinstance(visual, TextureVisuals)
            and isinstance(visual.material, SimpleMaterial)
            and isinstance(visual.material.image, ImageFile)
        )

    @staticmethod
    def names_textures(mesh_file: Path) -> bool:
        """
        :param mesh_file: A mesh file.
        :return: Whether a copy of it shows textures of its own: an OBJ whose material
            libraries name a texture, or a COLLADA or glTF document.
        """
        mesh_format = MeshFormat.of_path(str(mesh_file))
        if mesh_format is MeshFormat.STL:
            return False
        if mesh_format is not MeshFormat.OBJ:
            return True
        libraries = [
            mesh_file.parent / library
            for library in MaterialLibrary.declared_by(mesh_file)
        ]
        return any(
            MaterialLibrary.textures_of(library)
            for library in libraries
            if library.is_file()
        )


# %% writing


@dataclass(frozen=True)
class SurfaceFile:
    """
    Writes surfaces as one binary glTF file into a bundle.
    """

    assets: BundledAssets
    """
    The bundle's files.
    """

    def write(
        self, surfaces: Sequence[trimesh.Trimesh], directory: Path, name: str
    ) -> Path:
        """
        :param surfaces: The surfaces, each placed in the file's frame.
        :param directory: The bundle folder the file goes to.
        :param name: The start of the file's name; a digest of its content follows, so
            different surfaces of one name never overwrite each other.
        :return: The written file.
        :raises PathOutsideBundle: When the file would leave the bundle.
        """
        content = trimesh.Scene(list(surfaces)).export(file_type=MeshFormat.GLB)
        digest = hashlib.sha1(content).hexdigest()[:DIGEST_LENGTH]
        destination = directory / f"{name}-{digest}{MeshFormat.GLB}"
        self.assets.require_inside(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination
