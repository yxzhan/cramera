# Precision laboratory assets

This portable bundle contains the authored laboratory's environment, movable
objects, collision meshes, materials, initial poses, and rack-slot metadata.
All asset references are relative to this directory. Coordinates use metres,
Z up, and pose quaternions in XYZW order.

The runtime bundle ships with the `cramera` Python package and requires no
Blender installation, local scene cache, browser, server, or PR2 recording.
`trajectory.json` contains one initial-state frame for scene-viewer compatibility;
it is not an executed robot plan.

The scene was generated with `cramera/tools/laboratory/build_blender.py` and
`cramera/tools/laboratory/bundle.py` from
`cramera/tools/laboratory/specification.json`. The source Blender project and
preview renders are authoring outputs and are excluded from this runtime bundle.
The assets are distributed under the repository's GPL-3.0-only license.

The GLB files retain the authored visual materials. URDF collision geometry is
separate from those visuals: rack openings use square approximations, glass
walls use compound boxes, and the rounded glass bottom uses a shell mesh.
The URDF models include estimated inertias. Loading the world provides geometry,
kinematics, and collision queries; contact dynamics, liquid dynamics, and glass
fracture require a separately configured simulation backend. The packaged
validation metadata describes the source scene, not a robot execution result.
