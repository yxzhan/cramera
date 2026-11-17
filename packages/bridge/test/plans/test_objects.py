"""
Object libraries: finding their models, naming them, and resolving a model's identifier
to its file, without the CRAM workspace.
"""

from __future__ import annotations

import os
from pathlib import Path

from cramera_bridge.plans.objects import (
    LIBRARY_VARIABLE,
    REPOSITORY_RESOURCES,
    DescribedModel,
    ObjectGrasp,
    ObjectLibraries,
    ObjectLibrary,
)
from cramera_protocol.plan_builder import ObjectModel

from ..dataset.bundles import DATASET

LIBRARY = DATASET / "object_library"
"""
A stand-in library of two models.
"""

CUP = "object_library/objects/kitchen/012-cup_with_lid/model.urdf"
"""
The identifier of the model its collection names.
"""

BOX = "object_library/objects/kitchen/plain_box/model.urdf"
"""
The identifier of the model only its folder names.
"""


def test_a_library_offers_every_model_by_its_collection_name_or_its_folder():
    assert ObjectLibrary(LIBRARY).models() == [
        ObjectModel(identifier=CUP, name="Cup with Lid"),
        ObjectModel(identifier=BOX, name="Plain box"),
    ]


def test_a_directory_without_models_is_no_library(tmp_path):
    assert not ObjectLibrary(tmp_path).is_library()
    assert ObjectLibraries.of_environment({}, resources=tmp_path).libraries == []


def test_libraries_are_named_in_the_environment_or_found_in_the_resources(tmp_path):
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "empty").mkdir()
    (resources / LIBRARY.name).symlink_to(LIBRARY, target_is_directory=True)
    found = ObjectLibraries.of_environment({}, resources=resources)
    assert [library.name for library in found.libraries] == [LIBRARY.name]
    named = ObjectLibraries.of_environment(
        {LIBRARY_VARIABLE: os.pathsep.join([str(LIBRARY), str(tmp_path / "none")])},
        resources=resources,
    )
    assert named.roots() == {LIBRARY.name: LIBRARY.resolve()}


def test_an_identifier_resolves_into_its_library_or_the_mesh_directory(tmp_path):
    libraries = ObjectLibraries([ObjectLibrary(LIBRARY)])
    assert (
        libraries.path_of(CUP, tmp_path)
        == LIBRARY / "objects/kitchen/012-cup_with_lid/model.urdf"
    )
    assert libraries.path_of("milk.stl", tmp_path) == tmp_path / "milk.stl"
    assert libraries.path_of("other/milk.stl", tmp_path) == tmp_path / "other/milk.stl"
    assert [model.identifier for model in libraries.models()] == [CUP, BOX]


def test_a_model_is_taken_from_any_side_unless_its_collection_says_otherwise():
    libraries = ObjectLibraries([ObjectLibrary(LIBRARY)])
    assert libraries.grasp_of(CUP) is ObjectGrasp.FROM_ABOVE
    assert libraries.grasp_of(BOX) is ObjectGrasp.ANY_SIDE
    assert libraries.grasp_of("milk.stl") is ObjectGrasp.ANY_SIDE
    assert libraries.grasp_of("other/objects/cup/model.urdf") is ObjectGrasp.ANY_SIDE


def test_the_repository_resources_are_beside_the_packages():
    assert REPOSITORY_RESOURCES == Path(__file__).resolve().parents[4] / "resources"


def test_a_model_shows_the_color_its_collection_names():
    cubes = DATASET / "cube_library"
    libraries = ObjectLibraries([ObjectLibrary(cubes)])
    assert libraries.color_of("cube_library/objects/cubes/red_cube/model.urdf") == "red"
    assert libraries.color_of(CUP) is None
    assert libraries.color_of("milk.stl") is None


def test_every_model_is_described_as_its_identifier_names_its_color_and_grasp():
    libraries = ObjectLibraries(
        [ObjectLibrary(DATASET / "cube_library"), ObjectLibrary(LIBRARY)]
    )
    assert libraries.described_models() == [
        DescribedModel(
            model=model,
            color=libraries.color_of(model.identifier),
            grasp=libraries.grasp_of(model.identifier),
        )
        for model in libraries.models()
    ]
