"""
The plan builder's plans, catalog, constraints and demo runs.
"""

from __future__ import annotations

import pytest

from cramera_protocol.browser import BROWSER_ENUMERATIONS
from cramera_protocol.exceptions import (
    InvalidDocumentValue,
    UnsupportedProtocolVersion,
)
from cramera_protocol.geometry import Bounds, Pose, Vector3
from cramera_protocol.live_messages import (
    ConstraintMonitorType,
    MonitorParameterKey,
    MonitorSpecificationKey,
)
from cramera_protocol.plan_builder import (
    ArmReport,
    ArmReportKey,
    RobotPosture,
    RobotPostureKey,
    CLASS_NAME_FORMAT_VERSION,
    ActionCatalog,
    ActionCatalogKey,
    ActionFieldKind,
    ActionFieldKey,
    ActionGroup,
    ActionSchema,
    ActionSchemaKey,
    DemoDocument,
    DemoDocumentKey,
    Destination,
    DestinationKey,
    LegacyStepKey,
    LegacyStepKind,
    MIGRATED_ACTIONS,
    MigratedAction,
    MigratedActionField,
    PLAN_FORMAT_VERSION,
    PlacementMode,
    PlanBuilderCatalog,
    PlanBuilderCatalogKey,
    PlanBuilderLimits,
    PlanBuilderLimitsKey,
    PlanBuilderPageParameter,
    PlanBuilderRoute,
    PlanDocument,
    PlanKey,
    PlannedObjectKey,
    PlannedRobotKey,
    PlanRecord,
    PlanRecordKey,
    PlanStepKey,
    RobotModel,
    SavedNames,
    RobotModelKey,
    RuleValue,
    RunMode,
    RunRequest,
    RunState,
    RunOutputMarker,
    MonitorObservation,
    PerceptionReport,
    PerceptionReportKey,
    ReportedObject,
    ReportedObjectVerdict,
    RunEvent,
    RunEventKey,
    RunEventKind,
    RunMonitor,
    RunStatus,
    StepState,
    SafetyStop,
    SafetyStopKey,
    RunStatusKey,
    StepConstraintKey,
)
from cramera_protocol.robot_profile import (
    ExecutorKind,
    PerceptionSettings,
    SafetyLimits,
)
from cramera_protocol.version import CURRENT_VERSION, UNVERSIONED

# %% plans


def test_a_plan_reads_its_robots_steps_and_objects(read_json):
    data = read_json("plan_builder/plan.json")
    plan = PlanDocument.from_json(data)
    written_robot = data[PlanKey.ROBOTS][0]
    written_steps = written_robot[PlannedRobotKey.STEPS]
    robot, standing = plan.robots
    assert robot.pose == Pose.from_json(written_robot[PlannedRobotKey.POSE])
    assert robot.joint_positions == written_robot[PlannedRobotKey.JOINT_POSITIONS]
    assert [step.action for step in robot.steps] == [
        written[PlanStepKey.ACTION] for written in written_steps
    ]
    assert [step.values for step in robot.steps] == [
        written[PlanStepKey.VALUES] for written in written_steps
    ]
    assert standing.steps == []
    assert plan.selected_robot == data[PlanKey.SELECTED_ROBOT]
    assert plan.objects[0].pose == Pose.from_json(
        data[PlanKey.OBJECTS][0][PlannedObjectKey.POSE]
    )


def test_a_step_keeps_the_monitor_and_the_switch_its_constraints_compiled_to(
    read_json,
):
    data = read_json("plan_builder/plan.json")
    transport = PlanDocument.from_json(data).robots[0].steps[2]
    upright, looking = transport.constraints
    written = data[PlanKey.ROBOTS][0][PlannedRobotKey.STEPS][2][
        PlanStepKey.CONSTRAINTS
    ][0]
    assert upright.monitor.monitor_type is ConstraintMonitorType.VECTORS_ALIGNED
    assert (
        upright.monitor.parameters[MonitorParameterKey.TIP_LINK]
        == written[StepConstraintKey.MONITOR][MonitorSpecificationKey.PARAMETERS][
            MonitorParameterKey.TIP_LINK
        ]
    )
    assert upright.switch is None
    assert looking.monitor is None
    assert looking.switch == written_looking_switch(data)


def written_looking_switch(data):
    """
    :param data: The test plan's JSON.
    :return: The switch its looking constraint names.
    """
    step = data[PlanKey.ROBOTS][0][PlannedRobotKey.STEPS][2]
    return step[PlanStepKey.CONSTRAINTS][1][StepConstraintKey.SWITCH]


def test_a_plan_round_trips(read_json):
    plan = PlanDocument.from_json(read_json("plan_builder/plan.json"))
    assert PlanDocument.from_json(plan.to_json()) == plan


def test_left_out_fields_take_their_defaults(read_json):
    plan = PlanDocument.from_json(read_json("plan_builder/plan_with_defaults.json"))
    step = plan.robots[0].steps[0]
    assert plan.format_version == PLAN_FORMAT_VERSION
    assert plan.environment is None and plan.selected_robot is None
    assert plan.task == ""
    assert step.values == {} and step.constraints == []
    assert plan.objects[0].color is None


def test_a_written_plan_names_the_plan_format_version():
    written = PlanDocument(name="empty").to_json()
    assert written[PlanKey.FORMAT_VERSION] == PLAN_FORMAT_VERSION.to_json()
    assert PLAN_FORMAT_VERSION.major == CURRENT_VERSION.major
    assert PlanKey.ENVIRONMENT not in written


# %% plans of earlier versions


CLASS_NAMES = SavedNames(
    actions={
        MigratedAction.PARK_ARMS: "park_arms",
        MigratedAction.MOVE_TORSO: "move_torso",
        MigratedAction.NAVIGATE: "navigate",
        MigratedAction.TRANSPORT: "transport",
        MigratedAction.PICK_UP: "pick_up",
        MigratedAction.PLACE: "place",
    },
    robots={"PR2": "pr2", "Stretch": "stretch"},
    members={
        "park_arms": {"arm": {"LEFT": "left", "RIGHT": "right", "BOTH": "both"}},
        "move_torso": {"torso_state": {"HIGH": "high", "MID": "mid", "LOW": "low"}},
        "transport": {"arm": {"LEFT": "left", "RIGHT": "right", "BOTH": "both"}},
        "pick_up": {"arm": {"LEFT": "left", "RIGHT": "right", "BOTH": "both"}},
        "place": {"arm": {"LEFT": "left", "RIGHT": "right", "BOTH": "both"}},
    },
    surface_types={"Table": "table", "CounterTop": "counter_top"},
)
"""
The identifiers a catalog would list the migrated actions and the test robot under.
"""


def test_a_plan_of_step_kinds_is_read_as_the_actions_they_stood_for(read_json):
    legacy = PlanDocument.from_json(read_json("plan_builder/legacy/plan.json"))
    current = PlanDocument.from_json(read_json("plan_builder/plan.json"))
    assert CLASS_NAMES.translated(legacy) == current


def test_a_plan_of_identifiers_is_left_as_it_is(read_json):
    current = PlanDocument.from_json(read_json("plan_builder/plan.json"))
    assert CLASS_NAMES.translated(current) is current


def test_a_plan_of_format_1_3_is_read_as_the_same_plan_of_format_1_4(read_json):
    data = read_json("plan_builder/plan.json")
    current = PlanDocument.from_json(data)
    earlier = PlanDocument.from_json({**data, PlanKey.FORMAT_VERSION: "1.3"})
    assert PLAN_FORMAT_VERSION.to_json() == "1.4"
    assert CLASS_NAMES.translated(earlier) == current


def test_names_a_catalog_does_not_list_stay_for_its_check_to_report(read_json):
    legacy = PlanDocument.from_json(read_json("plan_builder/legacy/plan.json"))
    translated = SavedNames(actions={}, robots={}).translated(legacy)
    assert translated.format_version == PLAN_FORMAT_VERSION
    assert [step.action for step in translated.robots[0].steps] == [
        step.action for step in legacy.robots[0].steps
    ]


def test_saved_names_are_read_from_a_catalog():
    catalog = PlanBuilderCatalog(
        live_url="",
        robots=[RobotModel(name="pr2", label="PR2", module="robots", saved_as=["PR2"])],
        actions=[
            ActionSchema(
                name="pick_up",
                module="actions",
                label="Pick Up",
                group=ActionGroup.CORE,
                saved_as=["PickUpAction"],
            )
        ],
    )
    names = SavedNames.of_catalog(catalog)
    assert (names.actions, names.robots) == (
        {"PickUpAction": "pick_up"},
        {"PR2": "pr2"},
    )


def test_every_step_kind_names_an_action():
    assert set(MIGRATED_ACTIONS) == set(LegacyStepKind)
    assert set(MIGRATED_ACTIONS.values()) == set(MigratedAction)


def test_a_migrated_put_down_keeps_its_pose_as_a_destination(read_json):
    data = read_json("plan_builder/legacy/unfinished_place.json")
    place, pick_up = PlanDocument.from_json(data).robots[0].steps
    written = data[PlanKey.ROBOTS][0][PlannedRobotKey.STEPS][0]
    assert place.action == MigratedAction.PLACE
    assert place.values == {
        MigratedActionField.OBJECT: written[LegacyStepKey.OBJECT],
        MigratedActionField.ARM: written[LegacyStepKey.ARM],
    }
    assert pick_up.action == MigratedAction.PICK_UP


def test_a_migrated_pose_destination_reads_as_a_destination(read_json):
    data = read_json("plan_builder/legacy/plan.json")
    transport = PlanDocument.from_json(data).robots[0].steps[2]
    destination = Destination.from_json(transport.values[MigratedActionField.TARGET])
    assert destination.placement is PlacementMode.SURFACE
    assert destination.pose is None
    assert (
        destination.surface_type
        == data[PlanKey.ROBOTS][0][PlannedRobotKey.STEPS][2][LegacyStepKey.SURFACE_TYPE]
    )


def test_a_migrated_plan_carries_the_class_name_format_version(read_json):
    data = read_json("plan_builder/legacy/plan.json")
    assert data[PlanKey.FORMAT_VERSION] == CURRENT_VERSION.to_json()
    plan = PlanDocument.from_json(data)
    assert plan.format_version == CLASS_NAME_FORMAT_VERSION
    unversioned = PlanDocument.from_json(
        {PlanKey.FORMAT_VERSION: UNVERSIONED.to_json(), PlanKey.NAME: "old"}
    )
    assert unversioned.format_version == CLASS_NAME_FORMAT_VERSION


def test_a_destination_names_its_placement_pose_and_surface():
    destination = Destination(surface_type="Table", placement=PlacementMode.SURFACE)
    written = destination.to_json()
    assert written[DestinationKey.PLACEMENT] == PlacementMode.SURFACE
    assert DestinationKey.POSE not in written
    assert Destination.from_json(written) == destination


def test_a_zone_on_top_of_or_marker_destination_is_only_known_once_grounded():
    zone = Destination(placement=PlacementMode.ZONE, zone="left_tray")
    on_top = Destination(placement=PlacementMode.ON_TOP_OF, object="detected:step_2")
    marker = Destination(
        placement=PlacementMode.MARKER, pose=Pose(0.6, 0.2, 0.88, 0, 0, 0, 1)
    )
    for destination in (zone, on_top, marker):
        assert destination.is_grounded_late
        assert Destination.from_json(destination.to_json()) == destination
    assert on_top.to_json()[DestinationKey.OBJECT] == "detected:step_2"
    assert not Destination(pose=Pose(0, 0, 0, 0, 0, 0, 1)).is_grounded_late


def test_a_plan_of_another_major_version_is_refused(read_json):
    with pytest.raises(UnsupportedProtocolVersion):
        PlanDocument.from_json(
            read_json("plan_builder/invalid/plan_from_next_major_version.json")
        )


@pytest.mark.parametrize(
    "file_name",
    [
        "plan_with_unknown_step_kind.json",
        "plan_with_short_pose.json",
        "plan_with_values_that_are_no_object.json",
    ],
)
def test_a_plan_with_a_malformed_value_is_refused(read_json, file_name):
    with pytest.raises(InvalidDocumentValue):
        PlanDocument.from_json(read_json(f"plan_builder/invalid/{file_name}"))


def test_a_record_without_identity_asks_for_a_new_plan():
    record = PlanRecord(document=PlanDocument(name="new"))
    written = record.to_json()
    assert PlanRecordKey.IDENTIFIER not in written
    assert PlanRecord.from_json(written) == record


# %% catalog


def test_a_robot_model_stands_on_the_floor_unless_it_names_a_height():
    standing = RobotModel(name="unitree_g1", module="robots", standing_height=0.7923)
    written = standing.to_json()
    assert written[RobotModelKey.STANDING_HEIGHT] == 0.7923
    assert RobotModel.from_json(written) == standing
    assert (
        RobotModel.from_json({"name": "pr2", "module": "robots"}).standing_height == 0
    )


def test_the_catalog_reads_robots_environments_objects_and_rules(read_json):
    data = read_json("plan_builder/catalog.json")
    catalog = PlanBuilderCatalog.from_json(data)
    robot, arm_only = catalog.robots
    rule = catalog.constraint_rules[0]
    written = data[PlanBuilderCatalogKey.ROBOTS]
    assert robot.choices == written[0][RobotModelKey.CHOICES]
    assert arm_only.choices == written[1][RobotModelKey.CHOICES]
    assert [entry.name for entry in catalog.environments] == [
        entry["name"] for entry in data[PlanBuilderCatalogKey.ENVIRONMENTS]
    ]
    assert rule.monitor_type is ConstraintMonitorType.HEIGHT
    assert rule.parameters[MonitorParameterKey.LOWER_LIMIT] == RuleValue.LENGTH
    assert rule.observed is True
    assert (
        catalog.limits.maximum_steps
        == data[PlanBuilderCatalogKey.LIMITS][PlanBuilderLimitsKey.MAXIMUM_STEPS]
    )
    assert catalog.limits.maximum_robots == PlanBuilderLimits().maximum_robots
    assert PlanBuilderCatalog.from_json(catalog.to_json()) == catalog


def test_a_catalog_without_cram_names_what_is_missing():
    catalog = PlanBuilderCatalog(live_url="http://127.0.0.1:1", missing=["coraplex"])
    written = catalog.to_json()
    assert written[PlanBuilderCatalogKey.MISSING] == catalog.missing
    assert written[PlanBuilderCatalogKey.ROBOTS] == []
    assert written[PlanBuilderCatalogKey.LIMITS] == PlanBuilderLimits().to_json()


# %% actions


def test_an_action_schema_reads_its_fields_and_enumerations(read_json):
    data = read_json("plan_builder/actions.json")
    gripper = ActionCatalog.from_json(data).actions[0]
    written = data[ActionCatalogKey.ACTIONS][0][ActionSchemaKey.FIELDS][1]
    motion = gripper.field_named(written[ActionFieldKey.NAME])
    assert gripper.group == ActionGroup.CORE
    assert gripper.available is True and gripper.unavailable_reason is None
    assert motion.kind is ActionFieldKind.ENUM
    assert motion.members == written[ActionFieldKey.MEMBERS]
    assert motion.enum_type == written[ActionFieldKey.ENUM_TYPE]
    assert gripper.field_named("nothing") is None


def test_a_derived_grasp_names_the_fields_it_is_derived_from(read_json):
    pick_up = ActionCatalog.from_json(read_json("plan_builder/actions.json")).actions[1]
    grasp = [entry for entry in pick_up.fields if entry.kind is ActionFieldKind.GRASP]
    assert [entry.derived_from for entry in grasp] == [["arm", "object_designator"]]
    assert pick_up.field_named("tolerate_grasp_stall").default is False


def test_an_unavailable_action_says_why_and_a_hidden_field_its_type(read_json):
    data = read_json("plan_builder/actions.json")
    catalog = ActionCatalog.from_json(data)
    unavailable, swirling = catalog.actions[2], catalog.actions[3]
    assert unavailable.available is False
    assert (
        unavailable.unavailable_reason
        == data[ActionCatalogKey.ACTIONS][2][ActionSchemaKey.UNAVAILABLE_REASON]
    )
    assert [hidden.name for hidden in swirling.hidden] == ["pivot"]
    assert swirling.group == ActionGroup.COMPOSITE


def test_the_action_catalog_names_the_modules_that_failed_to_import(read_json):
    data = read_json("plan_builder/actions.json")
    catalog = ActionCatalog.from_json(data)
    assert [failed.module for failed in catalog.failed_modules] == [
        written["module"] for written in data[ActionCatalogKey.FAILED_MODULES]
    ]
    assert ActionCatalog.from_json(catalog.to_json()) == catalog


def test_the_plan_builder_catalog_carries_the_actions(read_json):
    actions = ActionCatalog.from_json(read_json("plan_builder/actions.json"))
    catalog = PlanBuilderCatalog(
        live_url="http://127.0.0.1:1",
        actions=actions.actions,
        failed_action_modules=actions.failed_modules,
    )
    assert PlanBuilderCatalog.from_json(catalog.to_json()) == catalog
    assert catalog.to_json()[PlanBuilderCatalogKey.ACTIONS][0] == (
        actions.actions[0].to_json()
    )


# %% runs


def test_a_run_request_stages_by_default():
    request = RunRequest(plan=PlanDocument(name="x"))
    assert RunRequest.from_json(request.to_json()).mode is RunMode.STAGE


def test_a_run_request_with_an_unknown_mode_is_refused(read_json):
    with pytest.raises(InvalidDocumentValue):
        RunRequest.from_json(
            read_json("plan_builder/invalid/run_request_with_unknown_mode.json")
        )


def test_each_mode_that_performs_executes_on_its_default_executor():
    assert RunMode.STAGE.default_executor is None
    assert RunMode.PERFORM.default_executor is ExecutorKind.KINEMATIC
    assert RunMode.SIMULATE.default_executor is ExecutorKind.PHYSICS


def test_the_run_status_reads_the_exit_and_the_log(read_json):
    data = read_json("plan_builder/run_status.json")
    status = RunStatus.from_json(data)
    assert status.state is RunState.FAILED
    assert status.mode is RunMode.PERFORM
    assert status.return_code == data[RunStatusKey.RETURN_CODE]
    assert status.log == data[RunStatusKey.LOG]


def test_a_safety_stop_is_read_back_from_the_line_reporting_it():
    stop = SafetyStop(constraint="Left arm workspace (tool 0.004 m outside)")
    output = f"building the world\n{stop.to_output_line()}\nTraceback ...\n"

    assert SafetyStop.from_output(output) == stop
    assert stop.to_output_line().startswith(RunOutputMarker.SAFETY_STOP)


def test_output_reporting_no_safety_stop_reads_as_none():
    assert SafetyStop.from_output("the plan finished\n") is None


def test_a_run_status_carries_its_safety_stop():
    status = RunStatus(
        state=RunState.SAFETY_STOPPED,
        safety_stop=SafetyStop(constraint="Left arm workspace", acknowledged=True),
    )
    assert RunStatus.from_json(status.to_json()) == status
    assert SafetyStopKey in BROWSER_ENUMERATIONS


# %% run events


def test_a_run_event_is_read_back_from_the_line_reporting_it():
    event = RunEvent(
        kind=RunEventKind.STEP,
        step="step_3",
        state=StepState.RETRYING,
        attempt=1,
        attempts=3,
        arm="left",
        reason="red_cube moved 0.05 m since the plan was grounded.",
        next_attempt="detects its objects again and grounds the plan anew",
    )

    line = event.to_output_line()

    assert line.startswith(RunOutputMarker.RUN_EVENT)
    assert "\n" not in line
    assert RunEvent.from_output_line(line) == event


def test_a_perception_event_is_read_back_with_every_object_reported():
    event = RunEvent(
        kind=RunEventKind.PERCEPTION,
        step="step_2",
        perception=PerceptionReport(
            target="red_cube",
            type="plannedgraspable",
            color="red",
            box=Bounds(Vector3(0.475, 0.025, 0.83), Vector3(0.625, 0.175, 0.98)),
            grounded=Vector3(0.55, 0.1, 0.905),
            tolerance=0.02,
            objects=[
                ReportedObject(
                    position=Vector3(0.79, 0.145, 0.905),
                    size=Vector3(0.066, 0.061, 0.022),
                    colors=["red"],
                    verdict=ReportedObjectVerdict.OUTSIDE_BOX,
                    distance=0.19,
                )
            ],
            summary="red_cube was seen 19 cm from where the plan was validated.",
        ),
    )

    read = RunEvent.from_output_line(event.to_output_line())

    assert read == event
    assert PerceptionReportKey in BROWSER_ENUMERATIONS


def test_a_line_reporting_no_event_reads_as_none():
    assert RunEvent.from_output_line("[run log] step_1: attempt 1/3 succeeded") is None


def test_an_event_leaves_out_what_does_not_concern_it():
    event = RunEvent(
        kind=RunEventKind.MONITOR,
        step="step_3",
        monitor=RunMonitor.LOOK_AT,
        observation=MonitorObservation.HOLDS,
        target="red_cube",
    )
    assert set(event.to_json()) == {
        RunEventKey.KIND,
        RunEventKey.STEP,
        RunEventKey.MONITOR,
        RunEventKey.OBSERVATION,
        RunEventKey.TARGET,
    }


def test_a_run_status_carries_its_events():
    status = RunStatus(
        state=RunState.RUNNING,
        events=[
            RunEvent(kind=RunEventKind.STEP, step="step_1", state=StepState.PENDING),
            RunEvent(kind=RunEventKind.SAFETY_STOP, constraint="Left arm workspace"),
        ],
    )
    assert RunStatus.from_json(status.to_json()) == status
    for enumeration in (StepState, RunEventKind, RunMonitor, MonitorObservation):
        assert enumeration in BROWSER_ENUMERATIONS


def test_a_posture_carries_when_each_arm_last_reported():
    posture = RobotPosture(
        joint_positions={"left_shoulder_pan_joint": 0.3},
        arms=[
            ArmReport(arm="left_arm", age_seconds=0.2, live=True),
            ArmReport(arm="right_arm"),
        ],
        mirrored=True,
    )
    assert RobotPosture.from_json(posture.to_json()) == posture
    assert {ArmReportKey, RobotPostureKey} <= set(BROWSER_ENUMERATIONS)


def test_an_arm_that_never_reported_reads_as_not_live():
    arm = ArmReport.from_json({ArmReportKey.ARM: "left_arm"})
    assert arm.age_seconds is None
    assert not arm.live


def test_a_posture_reads_as_not_mirrored_and_without_arms_by_default():
    posture = RobotPosture.from_json({})
    assert not posture.mirrored
    assert posture.arms == []


def test_an_idle_status_names_no_mode_and_no_exit():
    written = RunStatus().to_json()
    assert written[RunStatusKey.STATE] == RunState.IDLE
    assert RunStatusKey.MODE not in written
    assert RunStatusKey.RETURN_CODE not in written


# %% browser


@pytest.mark.parametrize(
    "enumeration",
    [
        PlanBuilderRoute,
        PlanBuilderPageParameter,
        ActionFieldKind,
        ActionFieldKey,
        ActionSchemaKey,
        ActionCatalogKey,
        DestinationKey,
        RuleValue,
        RunMode,
        RunState,
        MonitorParameterKey,
    ],
)
def test_the_plan_builder_names_reach_the_browser(enumeration):
    assert enumeration in BROWSER_ENUMERATIONS


# %% demos


def test_a_demo_document_carries_the_plan_and_its_environment(read_json):
    plan = PlanDocument.from_json(read_json("plan_builder/plan.json"))
    demo = DemoDocument(plan=plan, environment_directory="/scenes/aicor_medical_lab")
    assert DemoDocument.from_json(demo.to_json()) == demo


def test_a_demo_on_an_empty_floor_names_no_environment_directory():
    written = DemoDocument(plan=PlanDocument(name="x")).to_json()
    assert DemoDocumentKey.ENVIRONMENT_DIRECTORY not in written


def test_a_demo_document_carries_the_profiles_perception_settings():
    demo = DemoDocument(
        plan=PlanDocument(name="x"), perception=PerceptionSettings(answer_timeout=5.0)
    )

    assert DemoDocument.from_json(demo.to_json()) == demo


def test_a_demo_document_carries_the_profiles_safety_limits():
    demo = DemoDocument(
        plan=PlanDocument(name="x"),
        safety=SafetyLimits(
            maximum_tool_velocity=0.5, maximum_real_robot_tool_velocity=0.1
        ),
    )
    assert DemoDocument.from_json(demo.to_json()) == demo
    assert DemoDocumentKey.SAFETY not in DemoDocument(plan=demo.plan).to_json()
