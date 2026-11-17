"""
Checking plans against the limits and the catalog, and keeping them in the plan
directory.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace

import pytest

from cramera_plan_builder.exceptions import (
    DuplicateIdentifier,
    InvalidIdentifier,
    InvalidPlanName,
    InvalidPlanText,
    InvalidStepValue,
    MissingStepParameter,
    PlanTooLarge,
    PlanWithoutRobot,
    TooManyEntries,
    UnavailableStepAction,
    UnknownPlan,
    UnknownReference,
    UnknownStepAction,
    UnknownStepField,
    UnreadablePlan,
    UnsafeStoragePath,
    UnsupportedByCatalog,
    ValueOutOfRange,
)
from cramera_plan_builder.plans import CatalogCheck, PlanCheck, PlanFile, PlanStore
from cramera_protocol.detection import DetectedObjectReference, DetectionRegion
from cramera_protocol.plan_builder import (
    CLASS_NAME_FORMAT_VERSION,
    ActionCatalog,
    ActionFieldKind,
    Destination,
    DestinationKey,
    PlacementMode,
    PlanBuilderCatalog,
    PlanBuilderLimits,
    PlanDocument,
    PlanKey,
    PlanRecord,
    PlanStep,
    SavedNames,
    StepConstraint,
)
from cramera_protocol.robot_profile import RobotProfile, Skill

from .dataset.given_workspace import DATASET, GivenWorkspace

# %% limits


@pytest.fixture
def check() -> PlanCheck:
    """
    :return: The check with the default limits.
    """
    return PlanCheck(PlanBuilderLimits())


def test_a_complete_plan_passes(check, plan):
    check.check(plan)
    check.complete(plan)


@pytest.mark.parametrize(
    ("file_name", "error"),
    [
        ("invalid_name.json", InvalidPlanName),
        ("invalid_robot_identity.json", InvalidIdentifier),
        ("duplicate_objects.json", DuplicateIdentifier),
        ("unknown_selected_robot.json", UnknownReference),
        ("far_away_object.json", ValueOutOfRange),
        ("label_with_control_character.json", InvalidPlanText),
    ],
)
def test_a_malformed_plan_is_refused(check, read_plan, file_name, error):
    with pytest.raises(error):
        check.check(read_plan(f"invalid/{file_name}"))


@pytest.mark.parametrize("name", ["Red cube to the centre", "sort  cubes 2"])
def test_a_plan_name_may_hold_spaces(check, plan, name):
    plan.name = name
    check.check(plan)


@pytest.mark.parametrize(
    "name", ["../demo", "a/b", " leading", "trailing ", "x" * 65, "", "milk!"]
)
def test_a_plan_name_says_which_characters_it_may_hold(check, plan, name):
    plan.name = name
    with pytest.raises(InvalidPlanName) as refusal:
        check.check(plan)
    assert "letters, digits, spaces" in refusal.value.message()


def test_a_plan_with_more_steps_than_fit_is_refused(plan):
    limits = PlanBuilderLimits(maximum_steps=len(plan.robots[0].steps) - 1)
    with pytest.raises(TooManyEntries) as refusal:
        PlanCheck(limits).check(plan)
    assert refusal.value.limit == limits.maximum_steps


def test_a_plan_larger_than_the_document_limit_is_refused(plan):
    limits = PlanBuilderLimits(maximum_document_bytes=len(json.dumps(plan.to_json())))
    with pytest.raises(PlanTooLarge):
        PlanCheck(limits).check(replace(plan, task=plan.task + " again"))


def test_a_constraint_longer_than_a_text_may_be_is_refused(plan):
    limits = PlanBuilderLimits()
    step = plan.robots[0].steps[0]
    step.constraints = [StepConstraint(text="x" * (limits.maximum_text_length + 1))]
    with pytest.raises(InvalidPlanText):
        PlanCheck(limits).check(plan)


def test_a_plan_without_a_robot_cannot_run(check):
    with pytest.raises(PlanWithoutRobot):
        check.complete(PlanDocument(name="empty"))


# %% catalog


@pytest.fixture
def catalog(read_json) -> PlanBuilderCatalog:
    """
    :return: The test workspace's catalog with its actions, and the test laboratory as
        environment.
    """
    catalog = PlanBuilderCatalog.from_json(read_json("workspace.json"))
    actions = ActionCatalog.from_json(read_json("actions.json"))
    catalog.actions = actions.actions
    catalog.failed_action_modules = actions.failed_modules
    catalog.environments = PlanBuilderCatalog.from_json(
        {"liveUrl": catalog.live_url, "environments": [{"name": "lab"}]}
    ).environments
    return catalog


@pytest.fixture
def catalog_check(catalog) -> CatalogCheck:
    """
    :return: The check against the test catalog with the default limits.
    """
    return CatalogCheck(catalog, PlanBuilderLimits())


def test_a_plan_of_offered_entries_passes_the_catalog(catalog_check, plan):
    catalog_check.check(plan)


def test_an_arm_the_robot_does_not_have_is_refused(catalog_check, catalog, plan):
    plan.robots[0].steps[0].values["arm"] = "middle"
    with pytest.raises(UnsupportedByCatalog) as refusal:
        catalog_check.check(plan)
    assert refusal.value.offered == catalog.robots[0].choices["arms"]
    assert refusal.value.location.endswith("step_1.values.arm")


def test_an_unfinished_plan_can_be_saved_but_not_run(check, catalog_check, read_plan):
    unfinished = read_plan("unfinished.json")
    check.check(unfinished)
    with pytest.raises(MissingStepParameter) as refusal:
        catalog_check.check(unfinished)
    step = unfinished.robots[0].steps[0]
    schema = next(
        action
        for action in GivenWorkspace().action_catalog().actions
        if action.name == step.action
    )
    first_missing = next(
        offered.name
        for offered in schema.fields
        if offered.required
        and offered.kind is not ActionFieldKind.GRASP
        and offered.name not in step.values
    )
    assert refusal.value.parameter == first_missing


def test_a_put_down_at_a_pose_needs_the_pose(catalog_check, plan):
    place = plan.robots[0].steps[5]
    place.values["target_location"] = Destination(
        placement=PlacementMode.POSE
    ).to_json()
    with pytest.raises(MissingStepParameter) as refusal:
        catalog_check.check(plan)
    assert refusal.value.parameter == DestinationKey.POSE


def test_a_put_down_on_a_surface_needs_its_type(catalog_check, plan):
    transport = plan.robots[0].steps[3]
    transport.values["target_location"] = Destination(
        placement=PlacementMode.SURFACE
    ).to_json()
    with pytest.raises(MissingStepParameter) as refusal:
        catalog_check.check(plan)
    assert refusal.value.parameter == DestinationKey.SURFACE_TYPE


@pytest.mark.parametrize(
    ("file_name", "error"),
    [
        ("unknown_action.json", UnknownStepAction),
        ("unavailable_action.json", UnavailableStepAction),
        ("unknown_field.json", UnknownStepField),
        ("gone_member.json", UnsupportedByCatalog),
        ("member_the_robot_lacks.json", UnsupportedByCatalog),
        ("body_of_wrong_type.json", InvalidStepValue),
        ("given_grasp.json", InvalidStepValue),
        ("pose_of_wrong_shape.json", InvalidStepValue),
        ("surface_the_catalog_lacks.json", UnsupportedByCatalog),
        ("number_of_wrong_type.json", InvalidStepValue),
        ("boolean_of_wrong_type.json", InvalidStepValue),
        ("annotation_of_wrong_type.json", InvalidStepValue),
        ("integer_of_wrong_type.json", InvalidStepValue),
    ],
)
def test_a_step_that_no_longer_fits_its_action_loads_but_does_not_run(
    check, catalog_check, read_plan, file_name, error
):
    stale = read_plan(f"stale/{file_name}")
    check.check(stale)
    with pytest.raises(error) as refusal:
        catalog_check.check(stale)
    assert "robots.robot_1.steps.step_1" in refusal.value.message()


@pytest.mark.parametrize(
    ("file_name", "sentence"),
    [
        ("missing_field.json", "Step 1 (Set Gripper) needs a value for “motion”."),
        (
            "destination_without_pose.json",
            "Step 1 (Place) needs a pose for “target location”.",
        ),
    ],
)
def test_a_step_missing_a_value_is_named_by_its_number_title_and_field(
    check, catalog_check, read_plan, file_name, sentence
):
    stale = read_plan(f"stale/{file_name}")
    check.check(stale)
    with pytest.raises(MissingStepParameter) as refusal:
        catalog_check.check(stale)
    assert refusal.value.message() == sentence
    assert refusal.value.location.startswith("robots.robot_1.steps.step_1")


def test_a_step_of_a_skill_is_named_by_the_skills_label(catalog_check, plan):
    catalog_check.catalog.profile = RobotProfile(
        robot="pr2",
        label="PR2",
        skills=[Skill(identifier="carry", label="Carry the milk", action="transport")],
    )
    transport = plan.robots[0].steps[3]
    transport.skill = "carry"
    transport.values["target_location"] = Destination(
        placement=PlacementMode.SURFACE
    ).to_json()
    with pytest.raises(MissingStepParameter) as refusal:
        catalog_check.check(plan)
    assert refusal.value.message() == (
        "Step 4 (Carry the milk) needs a surface for “target location”."
    )


def test_a_step_acting_on_an_object_the_plan_lacks_is_refused(catalog_check, read_plan):
    with pytest.raises(UnknownReference) as refusal:
        catalog_check.check(read_plan("invalid/step_with_unknown_object.json"))
    assert refusal.value.reference == "bowl"


@pytest.mark.parametrize(
    "change",
    [
        lambda plan: setattr(plan, "environment", "nowhere"),
        lambda plan: setattr(plan.objects[0], "model", "anvil.stl"),
        lambda plan: setattr(plan.robots[0], "model", "R2D2"),
        lambda plan: plan.robots[0].steps[1].values.update(torso_state="TALL"),
    ],
)
def test_an_entry_the_catalog_does_not_offer_is_refused(catalog_check, plan, change):
    change(plan)
    with pytest.raises(UnsupportedByCatalog):
        catalog_check.check(plan)


def test_without_cram_only_the_environment_is_checked(catalog, plan):
    catalog.robots, catalog.actions, catalog.missing = [], [], ["coraplex"]
    CatalogCheck(catalog, PlanBuilderLimits()).check(plan)
    plan.environment = "nowhere"
    with pytest.raises(UnsupportedByCatalog) as refusal:
        CatalogCheck(catalog, PlanBuilderLimits()).check(plan)
    assert refusal.value.location == PlanKey.ENVIRONMENT


# %% storage


@pytest.fixture
def store(tmp_path) -> PlanStore:
    """
    :return: An empty plan directory in the test's directory.
    """
    return PlanStore(tmp_path / "plans")


def test_a_saved_plan_is_listed_and_loaded(store, plan):
    saved = store.save(PlanRecord(document=plan))
    assert [summary.identifier for summary in store.summaries()] == [saved.identifier]
    assert store.summaries()[0].name == plan.name
    assert store.load(saved.identifier) == saved


def test_saving_under_an_identity_replaces_the_plan(store, plan):
    saved = store.save(PlanRecord(document=plan))
    store.save(PlanRecord(identifier=saved.identifier, document=replace(plan, task="")))
    assert store.load(saved.identifier).document.task == ""
    assert len(store.summaries()) == 1


def test_a_damaged_plan_does_not_hide_the_others(store, plan):
    saved = store.save(PlanRecord(document=plan))
    (store.directory / f"damaged{PlanFile.SUFFIX}").write_text("{", encoding="utf-8")
    assert [summary.identifier for summary in store.summaries()] == [saved.identifier]
    with pytest.raises(UnreadablePlan):
        store.load("damaged")


def test_an_unknown_plan_is_answered_as_such(store):
    with pytest.raises(UnknownPlan):
        store.load("missing")


@pytest.mark.parametrize("identifier", ["../escape", "", "a/b", "x" * 97])
def test_a_malformed_plan_identity_is_refused(store, identifier):
    with pytest.raises(InvalidIdentifier):
        store.path(identifier)


def test_a_plan_directory_that_is_a_link_is_not_written(tmp_path, plan):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "plans").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(UnsafeStoragePath):
        PlanStore(tmp_path / "plans").save(PlanRecord(document=plan))


def test_no_plan_directory_lists_no_plans(store):
    assert store.summaries() == []


# %% plans of earlier versions


@pytest.fixture
def legacy_store(store) -> PlanStore:
    """
    :return: The plan directory holding the plans the builder saved before steps named
        actions, under their file names.
    """
    shutil.copytree(DATASET / "plans" / "legacy", store.directory)
    return store


def test_a_saved_plan_of_step_kinds_loads_as_the_same_plan_of_actions(
    legacy_store, read_plan, catalog
):
    loaded = legacy_store.load("milk_to_table").document
    translated = SavedNames.of_catalog(catalog).translated(loaded)
    assert translated == read_plan("milk_to_table.json")


def test_a_plan_of_class_names_is_translated_to_the_catalogs_identifiers(
    legacy_store, read_plan, catalog
):
    loaded = legacy_store.load("class_names").document
    assert loaded.format_version == CLASS_NAME_FORMAT_VERSION
    translated = SavedNames.of_catalog(catalog).translated(loaded)
    assert translated == read_plan("milk_to_table.json")


def test_a_migrated_plan_passes_the_catalog(
    legacy_store, catalog_check, check, catalog
):
    for summary in legacy_store.summaries():
        loaded = legacy_store.load(summary.identifier).document
        migrated = SavedNames.of_catalog(catalog).translated(loaded)
        check.check(migrated)
        catalog_check.check(migrated)


# %% objects found by an earlier step


def with_detection(plan: PlanDocument, index: int) -> PlanStep:
    """
    Put a step looking for objects on the whole table into the plan's first robot.

    :param plan: The plan.
    :param index: Where the step goes among the robot's steps.
    :return: The step.
    """
    detect = PlanStep(
        identifier="look",
        action="detect",
        values={"region": DetectionRegion().to_json(), "color": "red"},
    )
    plan.robots[0].steps.insert(index, detect)
    return detect


def pick_step(plan: PlanDocument) -> PlanStep:
    """
    :return: The step of the plan's first robot that picks an object up.
    """
    [pick] = [step for step in plan.robots[0].steps if step.action == "pick_up"]
    return pick


def test_a_step_names_the_object_an_earlier_detection_finds(catalog_check, plan):
    detect = with_detection(plan, 0)
    pick = pick_step(plan)
    pick.values["object_designator"] = DetectedObjectReference(detect.identifier).text
    catalog_check.check(plan)


def test_a_step_cannot_name_the_object_a_later_detection_finds(catalog_check, plan):
    detect = with_detection(plan, len(plan.robots[0].steps))
    pick = pick_step(plan)
    pick.values["object_designator"] = DetectedObjectReference(detect.identifier).text
    with pytest.raises(UnknownReference) as refusal:
        catalog_check.check(plan)
    assert refusal.value.reference == pick.values["object_designator"]


def test_a_region_that_is_no_region_is_refused(catalog_check, plan):
    detect = with_detection(plan, 0)
    detect.values["region"] = "the table"
    with pytest.raises(InvalidStepValue):
        catalog_check.check(plan)
