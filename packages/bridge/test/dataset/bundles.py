"""
Locations of the bundle and URDF files the export and scene tests read.
"""

from __future__ import annotations

from pathlib import Path

DATASET = Path(__file__).parent
"""
The test dataset directory.
"""

ANNOTATED_BUNDLE = DATASET / "scenes" / "annotated_bundle"
"""
A bundle converted from a USD stage, with semantic attributes on its prims.
"""

SHELF_WITH_TOTES_BUNDLE = DATASET / "scenes" / "shelf_with_totes"
"""
A bundle converted with free objects: a shelf with a collision box and a tote whose own
URDF holds its shape and collision boxes, with the prims it carries placed on it.
"""

SEMANTICS_WITH_MISSING_LINK = (
    DATASET / "scenes" / "semantics" / "semantics_with_missing_link.json"
)
"""
The annotated bundle's semantics with one more prim on a link the bundle lacks.
"""

BROKEN_INDEX = DATASET / "scenes" / "broken_index.json"
"""
An ``index.json`` that is not valid JSON.
"""

BUNDLED_CUP_URDF = DATASET / "urdf" / "bundled_cup.urdf"
"""
A URDF referencing a mesh relative to itself, a mesh of a package that is not installed
and a file that is no mesh.
"""

DRAWER_CABINET_URDF = DATASET / "urdf" / "drawer_cabinet.urdf"
"""
A cabinet with one drawer and its handle, named as coraplex's apartment names them, in
which CRAM's world reasoner finds a cabinet, a drawer and a handle.
"""

HINGED_CABINET_MJCF = DATASET / "mjcf" / "hinged_cabinet.xml"
"""
A cabinet whose door and drawer joints sit away from the bodies they move, so their
connections carry a pose of the child relative to the joint.
"""
