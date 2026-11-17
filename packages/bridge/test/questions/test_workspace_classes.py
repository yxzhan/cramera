"""
Indexing the workspace's classes and naming them in queries.
"""

from __future__ import annotations

import pytest

from krrood.symbol_graph.symbol_graph import Symbol

from cramera_bridge.questions.workspace_classes import (
    ClassLocation,
    SourceTreeScanner,
    WorkspaceClassIndex,
    WorkspaceClassNamespace,
    WorkspacePackage,
)
from .dataset.files import CLASS_TREE

SYMBOL = ClassLocation(
    name=Symbol.__name__, module=Symbol.__module__, package=WorkspacePackage.KRROOD
)
"""
A real class the index can import.
"""

# %% scanning


def test_the_scan_finds_hand_written_top_level_classes_with_their_modules():
    found = SourceTreeScanner(CLASS_TREE, WorkspacePackage.SEGMIND).classes()
    assert {(location.name, location.module) for location in found} == {
        ("IndexedRobot", f"{CLASS_TREE.name}.robots"),
        ("IndexedArm", f"{CLASS_TREE.name}.nested.arms"),
    }


def test_a_class_is_summarized_by_its_docstrings_first_line():
    index = WorkspaceClassIndex.of_package_directories(
        {WorkspacePackage.SEGMIND: CLASS_TREE}
    )
    assert index.candidates("IndexedRobot")[0].docstring_summary == (
        "A robot class found by reading the source."
    )
    assert index.candidates("IndexedArm")[0].docstring_summary == ""


def test_a_shared_name_goes_to_the_package_ranked_first():
    shared = [
        ClassLocation("Robot", "late.module", WorkspacePackage.ROBOKUDO),
        ClassLocation("Robot", "early.module", WorkspacePackage.SEMANTIC_DIGITAL_TWIN),
    ]
    index = WorkspaceClassIndex.of_classes(shared)
    assert (
        index.candidates("Robot")[0].package is WorkspacePackage.SEMANTIC_DIGITAL_TWIN
    )
    assert index.names() == ["Robot"]


def test_installed_packages_are_read_and_missing_ones_left_out():
    index = WorkspaceClassIndex.of_installed_packages([WorkspacePackage.KRROOD])
    assert index.resolve(Symbol.__name__) is Symbol


# %% naming


def test_a_name_resolves_to_its_class_on_first_use():
    namespace = WorkspaceClassNamespace(index=WorkspaceClassIndex.of_classes([SYMBOL]))
    assert namespace[Symbol.__name__] is Symbol
    assert Symbol.__name__ in namespace


def test_an_unknown_name_is_missing():
    with pytest.raises(KeyError):
        WorkspaceClassNamespace()["Nothing"]
    assert WorkspaceClassIndex().resolve("Nothing") is None


# %% the installed packages' index, read once


def test_the_installed_packages_are_indexed_once_per_process():
    assert WorkspaceClassIndex.installed() is WorkspaceClassIndex.installed()
    assert (
        WorkspaceClassIndex.installed() == WorkspaceClassIndex.of_installed_packages()
    )
