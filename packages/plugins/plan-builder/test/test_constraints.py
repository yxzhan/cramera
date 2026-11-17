"""
Compiling constraints written in plain language with the rule book.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from cramera_plan_builder.constraints import ConstraintCompiler, NamedObjects
from cramera_plan_builder.exceptions import (
    ConstraintReferenceMissing,
    ConstraintSubjectMissing,
)
from cramera_protocol.live_messages import ConstraintMonitorType, MonitorParameterKey
from cramera_protocol.plan_builder import (
    ActionCatalog,
    ActionSchema,
    ConstraintText,
    RuleValue,
)
from cramera_protocol.robot_profile import CameraMount

from .dataset.given_workspace import GivenWorkspace

EXAMPLE_SEPARATOR = ", "
"""
Separates the wordings a rule lists as its examples.
"""


@pytest.fixture(scope="module")
def compiler() -> ConstraintCompiler:
    """
    :return: The compiler of the rule book the builder ships.
    """
    return ConstraintCompiler.of_file()


@pytest.fixture(scope="module")
def actions() -> ActionCatalog:
    """
    :return: The test workspace's action schemas.
    """
    return GivenWorkspace().action_catalog()


def schema_of(actions: ActionCatalog, name: str) -> ActionSchema:
    """
    :param actions: Action schemas.
    :param name: An action's class name.
    :return: The schema of that name.
    """
    return next(schema for schema in actions.actions if schema.name == name)


def attached(text: str, step_action: str = "pick_up", **fields):
    """
    :param text: A constraint.
    :param step_action: The action of the step it is attached to.
    :param fields: The other fields of the request.
    :return: The request compiling the constraint on such a step.
    """
    return ConstraintText(text=text, step_action=step_action, **fields)


# %% the rule book


def test_every_example_of_a_rule_is_understood_as_that_rule(compiler):
    for rule in compiler.book.rules:
        offered_to = replace(compiler, camera_mount=rule.camera_mount)
        for example in rule.examples.split(EXAMPLE_SEPARATOR):
            assert offered_to.rule_for(example) is rule, example


def test_every_rules_sentence_is_understood_as_that_rule(compiler):
    for rule in compiler.book.rules:
        offered_to = replace(compiler, camera_mount=rule.camera_mount)
        assert offered_to.rule_for(rule.sentence.lower()) is rule, rule.sentence


def test_every_rule_measuring_a_length_has_a_default_one(compiler):
    for rule in compiler.book.rules:
        if RuleValue.LENGTH in rule.parameters.values():
            assert rule.default_length is not None, rule.monitor_type


# %% compiling


def test_a_sentence_no_rule_matches_compiles_to_nothing(compiler):
    compiled = compiler.compile(attached("Sing while working", step_object="milk"))
    assert compiled.monitor_type is None
    assert compiled.monitor is None and compiled.switch is None


def test_a_constraint_not_attached_yet_only_names_its_monitor(compiler):
    compiled = compiler.compile(ConstraintText(text="Milk must stay upright"))
    assert compiled.monitor_type is ConstraintMonitorType.VECTORS_ALIGNED
    assert compiled.monitor is None


def test_the_object_a_sentence_names_is_its_subject(compiler):
    compiled = compiler.compile(
        attached("Keep the milk upright", step_object="cup", objects=["cup", "milk"])
    )
    assert compiled.monitor.parameters[MonitorParameterKey.TIP_LINK] == "milk"


def test_without_a_named_object_the_steps_object_is_the_subject(compiler):
    compiled = compiler.compile(attached("Do not tilt it", step_object="cup"))
    assert compiled.monitor.parameters[MonitorParameterKey.TIP_LINK] == "cup"


def test_an_identity_of_several_words_is_named_by_its_words(compiler):
    compiled = compiler.compile(
        attached("Keep the milk box level", objects=["milk_box", "cup"])
    )
    assert compiled.monitor.parameters[MonitorParameterKey.TIP_LINK] == "milk_box"


@pytest.mark.parametrize(
    ("text", "metres"),
    [
        ("Keep the milk above the table by 10 cm", 0.1),
        ("Keep the milk 250 mm higher", 0.25),
        ("Keep the milk 1.5 metres above the floor", 1.5),
    ],
)
def test_a_length_in_the_sentence_sets_the_limit(compiler, text, metres):
    compiled = compiler.compile(attached(text, objects=["milk"]))
    assert compiled.monitor.monitor_type is ConstraintMonitorType.HEIGHT
    assert compiled.monitor.parameters[
        MonitorParameterKey.LOWER_LIMIT
    ] == pytest.approx(metres)


def test_without_a_length_the_rule_default_applies(compiler):
    compiled = compiler.compile(attached("Keep the milk below", objects=["milk"]))
    rule = compiler.rule_for("keep the milk below")
    assert compiled.monitor.parameters[MonitorParameterKey.UPPER_LIMIT] == (
        rule.default_length
    )


def test_a_distance_is_kept_from_the_second_named_object(compiler):
    compiled = compiler.compile(
        attached(
            "Keep the milk 20 cm away from the cup",
            step_object="cup",
            objects=["cup", "milk"],
        )
    )
    parameters = compiled.monitor.parameters
    assert compiled.monitor.monitor_type is ConstraintMonitorType.DISTANCE
    assert parameters[MonitorParameterKey.TIP_LINK] == "milk"
    assert parameters[MonitorParameterKey.ROOT_LINK] == "cup"
    assert parameters[MonitorParameterKey.LOWER_LIMIT] == pytest.approx(0.2)


def test_a_distance_from_nothing_named_is_refused(compiler):
    with pytest.raises(ConstraintReferenceMissing):
        compiler.compile(attached("Keep away from it", step_object="milk"))


def test_an_attached_constraint_about_no_object_is_refused(compiler):
    with pytest.raises(ConstraintSubjectMissing):
        compiler.compile(attached("Stay upright", step_action="navigate"))


def test_looking_turns_on_the_boolean_field_the_rule_names(compiler, actions):
    transport = schema_of(actions, "transport")
    compiled = compiler.compile(
        attached("Look where it operates", step_action=transport.name), transport
    )
    rule = compiler.rule_for("look where it operates")
    assert compiled.switch == rule.switch
    assert transport.field_named(rule.switch) is not None
    assert compiled.monitor is None


def test_looking_on_an_action_without_the_field_changes_nothing(compiler, actions):
    navigate = schema_of(actions, "navigate")
    compiled = compiler.compile(
        attached("Look where it operates", step_action=navigate.name), navigate
    )
    assert compiled.monitor_type is ConstraintMonitorType.POINTING_AT
    assert compiled.switch is None and compiled.monitor is None


def test_a_rule_without_a_switch_turns_on_nothing(compiler, actions):
    transport = schema_of(actions, "transport")
    compiled = compiler.compile(
        attached("Keep the milk upright", objects=["milk"]), transport
    )
    assert compiled.switch is None and compiled.monitor is not None


def test_a_switch_naming_a_field_that_is_no_boolean_turns_on_nothing(compiler, actions):
    transport = schema_of(actions, "transport")
    rule = compiler.rule_for("look where it operates")
    assert compiler.has_switch(replace(rule, switch="arm"), transport) is False


# %% rules offered per camera mount


def test_a_robot_of_one_camera_mount_is_offered_no_rule_of_another(compiler):
    fixed = replace(compiler, camera_mount=CameraMount.FIXED)
    assert {rule.camera_mount for rule in fixed.offered_rules()} == {
        None,
        CameraMount.FIXED,
    }


def test_a_robot_of_unknown_camera_mount_is_offered_every_rule(compiler):
    assert compiler.offered_rules() == compiler.book.rules


def test_looking_with_a_fixed_camera_observes_the_steps_object_in_view(compiler):
    fixed = replace(compiler, camera_mount=CameraMount.FIXED)
    compiled = fixed.compile(attached("Look where it operates", step_object="red_cube"))
    rule = fixed.rule_for("look where it operates")
    assert compiled.monitor.monitor_type is ConstraintMonitorType.IN_FIELD_OF_VIEW
    assert compiled.monitor.parameters == {
        MonitorParameterKey.TARGET: "red_cube",
        MonitorParameterKey.MARGIN: rule.parameters[MonitorParameterKey.MARGIN],
    }


def test_objects_are_ordered_by_where_the_sentence_names_them():
    named = NamedObjects("put the cup next to the milk", ["milk", "cup", "bowl"])
    assert named.in_order() == ["cup", "milk"]
