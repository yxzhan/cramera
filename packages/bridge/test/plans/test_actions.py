"""
Discovering the native actions: the schemas read from the action dataclasses, against
stand-in actions for each pattern and against coraplex's own actions package.
"""

from __future__ import annotations

import pytest

from coraplex.datastructures.trajectory import PoseTrajectory
from coraplex.robot_plans.actions.composite.searching import SearchAction
from coraplex.robot_plans.actions.core.misc import DetectAction
from coraplex.robot_plans.actions.core.navigation import LookAtAction
from coraplex.robot_plans.actions.core.pick_up import PickUpAction
from coraplex.robot_plans.actions.core.placing import PlaceAction
from coraplex.robot_plans.actions.core.robot_body import (
    FollowToolCenterPointPathAction,
    MoveTorsoAction,
    ParkArmsAction,
    SetGripperAction,
)
from cramera_bridge.names import ACTION_NAMES, ANNOTATION_NAMES, ENUM_NAMES
from cramera_bridge.plans.actions import (
    ActionConvention,
    ActionDiscovery,
    ActionModules,
    DiscoveredActions,
)
from cramera_bridge.plans.arms import Arms
from cramera_bridge.plans.object_types import ObjectType
from cramera_bridge.exceptions import UnknownAction
from cramera_protocol.plan_builder import ActionFieldKind, ActionGroup
from semantic_digital_twin.semantic_annotations.semantic_annotations import Tool
from semantic_digital_twin.world_description.color_names import ColorName

from ..dataset import mimic_actions
from ..dataset.mimic_actions.gestures import waving

BROKEN_MODULE = f"{mimic_actions.__name__}.broken"
"""
The stand-in module that fails to import.
"""

SEARCHING_MODULE = "coraplex.robot_plans.actions.composite.searching"
"""
The action module that failed to import in earlier checkouts.
"""

STATECHART_FIELD = "name"
"""
A field every statechart node has, which is no parameter of an action.
"""


@pytest.fixture(scope="module")
def mimics() -> DiscoveredActions:
    """
    :return: The stand-in actions.
    """
    return ActionDiscovery(modules=ActionModules(package=mimic_actions)).discover()


@pytest.fixture(scope="module")
def native() -> DiscoveredActions:
    """
    :return: The actions of coraplex's actions package.
    """
    return ActionDiscovery().discover()


# %% stand-in actions


def test_a_module_failing_to_import_is_reported_and_hides_only_itself(mimics):
    assert [failed.module for failed in mimics.failed_modules] == [BROKEN_MODULE]
    assert mimics.failed_modules[0].error.startswith(ImportError.__name__)
    assert [action.schema.name for action in mimics.actions] == [
        "hand_over",
        "point",
        "shrug",
        "wave",
    ]


def test_each_field_type_maps_to_its_kind(mimics):
    wave = mimics.named(waving.WaveAction.__name__).schema
    kinds = {offered.name: offered.kind for offered in wave.fields}
    assert kinds == {
        "arm": ActionFieldKind.ENUM,
        "speed": ActionFieldKind.ENUM,
        "mood": ActionFieldKind.ENUM,
        "times": ActionFieldKind.INTEGER,
        "height": ActionFieldKind.NUMBER,
        "shout": ActionFieldKind.BOOLEAN,
        "words": ActionFieldKind.TEXT,
        "toward": ActionFieldKind.POSE,
    }
    arm = wave.field_named("arm")
    assert (arm.enum_type, arm.members) == (
        ENUM_NAMES.identifier_of(Arms),
        [ENUM_NAMES.member_identifier(Arms, member) for member in Arms],
    )
    speed = wave.field_named("speed")
    assert speed.members == [member.name.lower() for member in waving.Speed]
    assert speed.default == waving.Speed.SLOW.name.lower()
    assert wave.field_named("arm").required is True
    assert wave.field_named("mood").required is False


def test_fields_without_a_control_but_with_a_default_are_hidden(mimics):
    wave = mimics.named(waving.WaveAction.__name__).schema
    assert {hidden.name for hidden in wave.hidden} == {"note", "effector", "rhythm"}
    assert wave.available is True


def test_the_schema_reads_the_label_group_and_docstrings(mimics):
    wave = mimics.named(waving.WaveAction.__name__).schema
    assert wave.label == "Wave"
    assert wave.group == "gestures"
    assert wave.documentation == "Wave with an arm."
    assert wave.field_named("arm").documentation == "The arm that waves."


def test_a_grasp_is_picked_as_the_object_it_is_on(mimics):
    hand_over = mimics.named(waving.HandOverAction.__name__).schema
    assert hand_over.field_named("grasp").kind is ActionFieldKind.BODY
    assert hand_over.field_named("arms").enum_type == ENUM_NAMES.identifier_of(Arms)
    assert hand_over.field_named("tool").annotation == (
        ANNOTATION_NAMES.identifier_of(Tool)
    )
    assert hand_over.field_named(ActionConvention.DESTINATION).kind is (
        ActionFieldKind.POSE_OR_SURFACE
    )
    assert hand_over.field_named(ActionConvention.DESTINATION).accepts_surface


def test_a_hand_is_picked_by_its_arm(mimics):
    point = mimics.named(waving.PointAction.__name__).schema
    assert point.available is True
    assert point.field_named("hand").enum_type == ENUM_NAMES.identifier_of(Arms)
    assert point.field_named(ActionConvention.DESTINATION).accepts_surface is False


def test_a_required_field_whose_type_cannot_be_resolved_is_named(mimics):
    shrug = mimics.named(waving.ShrugAction.__name__).schema
    assert shrug.available is False
    assert NameError.__name__ in shrug.unavailable_reason
    assert "'thought'" in shrug.unavailable_reason


def test_an_unknown_action_is_refused(mimics):
    with pytest.raises(UnknownAction):
        mimics.named("DanceAction")


# %% coraplex's actions


@pytest.mark.parametrize(
    "action",
    [
        ParkArmsAction,
        MoveTorsoAction,
        PickUpAction,
        PlaceAction,
        SetGripperAction,
        LookAtAction,
    ],
)
def test_the_native_actions_are_available(native, action):
    discovered = native.named(ACTION_NAMES.identifier_of(action))
    assert discovered.action_class is action
    assert discovered.schema.available is True


def test_the_searching_module_imports_in_this_checkout(native):
    assert SEARCHING_MODULE not in [failed.module for failed in native.failed_modules]
    discovered = native.named(ACTION_NAMES.identifier_of(SearchAction))
    assert discovered.action_class is SearchAction


def test_the_fields_of_the_statechart_node_are_no_parameters(native):
    for discovered in native.actions:
        schema = discovered.schema
        names = [offered.name for offered in schema.fields] + [
            hidden.name for hidden in schema.hidden
        ]
        assert STATECHART_FIELD not in names, schema.name


def test_an_action_with_a_required_trajectory_is_unavailable(native):
    schema = native.named(FollowToolCenterPointPathAction.__name__).schema
    assert schema.available is False
    assert PoseTrajectory.__name__ in schema.unavailable_reason


def test_set_gripper_offers_its_configuration_as_two_enumerations(native):
    discovered = native.named(SetGripperAction.__name__)
    schema = discovered.schema
    assert [offered.enum_type for offered in schema.fields] == [
        ENUM_NAMES.identifier_of(Arms),
        "gripper_state",
    ]
    assert discovered.configuration_parts() == {
        offered.name for offered in schema.fields
    }


def test_detect_offers_the_color_to_look_for_as_an_enumeration(native):
    schema = native.named(ACTION_NAMES.identifier_of(DetectAction)).schema
    color = schema.field_named("color")
    assert color.kind is ActionFieldKind.ENUM
    assert color.enum_type == ENUM_NAMES.identifier_of(ColorName)
    assert color.members == [
        ENUM_NAMES.member_identifier(ColorName, member) for member in ColorName
    ]
    assert color.required is False


def test_detect_offers_the_region_it_looks_in(native):
    schema = native.named(ACTION_NAMES.identifier_of(DetectAction)).schema
    assert schema.field_named("region").kind is ActionFieldKind.REGION


def test_detect_offers_the_kind_of_object_to_look_for_as_an_enumeration(native):
    schema = native.named(ACTION_NAMES.identifier_of(DetectAction)).schema
    kind = schema.field_named("object_sem_annotation")
    assert kind.kind is ActionFieldKind.ENUM
    assert kind.enum_type == ENUM_NAMES.identifier_of(ObjectType)
    assert kind.members == [
        ENUM_NAMES.member_identifier(ObjectType, member) for member in ObjectType
    ]


def test_core_actions_come_first(native):
    groups = [discovered.schema.group for discovered in native.actions]
    assert groups == sorted(groups, key=lambda group: group != ActionGroup.CORE)


def test_the_catalog_lists_every_schema(native):
    catalog = native.catalog()
    assert [schema.name for schema in catalog.actions] == [
        discovered.schema.name for discovered in native.actions
    ]
    assert catalog.failed_modules == native.failed_modules
