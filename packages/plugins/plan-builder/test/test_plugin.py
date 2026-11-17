"""
The plan builder's routes as the viewer serves them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from pathlib import Path
from importlib.machinery import ModuleSpec

import pytest
from typing_extensions import Callable, List


from cramera.application import ViewerApplication
from cramera.http.messages import HttpMethod, MediaType
from cramera.http.origin import OriginPolicy
from cramera.http.server import RequestAnswerer
from cramera.locations import DataLocations
from cramera.plugins import PluginContext
from cramera.scenes import SceneCatalog, SceneDirectory, SceneSource
from cramera_plan_builder.constraints import ConstraintCompiler
from cramera_plan_builder.demos import DEMO_SUFFIX, DemoTemplate
from cramera_plan_builder.exceptions import (
    PlanNotValidated,
    PlanRunning,
    RunModeNotOffered,
)
from cramera_plan_builder.mirroring import STALE_AFTER_SECONDS, PostureMirror
from cramera_plan_builder.profiles import ConfiguredProfile
from cramera_plan_builder.reaches import RobotReaches
from cramera_plan_builder.scans import SceneScans
from cramera_plan_builder.validations import PlanValidations
from cramera_plan_builder.plugin import (
    PlanBuilderPlugin,
    PlanBuilderStorage,
    StorageDirectory,
)
from cramera_plan_builder.workspace import (
    CramModule,
    CramModules,
    MissingWorkspace,
    WorkspaceLoader,
)
from cramera_protocol.detection import ScanRequest, SceneScan
from cramera_protocol.live import LIVE_HOST, LIVE_PORT
from cramera_protocol.geometry import Pose
from cramera_protocol.plan_builder import (
    ArmReport,
    ConstraintText,
    DemoDocument,
    Destination,
    PlacementMode,
    LoadedEnvironment,
    DemoSource,
    PlanBuilderCatalog,
    PlanBuilderLimits,
    PlanBuilderRoute,
    PlanBuilderVariable,
    PlanList,
    PlanRecord,
    RobotPosture,
    RunMode,
    RunRequest,
    RunState,
    RunStatus,
    SavedDemo,
    StepConstraint,
    UrdfEnvironment,
)
from cramera_protocol.robot_profile import (
    ExecutorKind,
    RobotConnection,
    RobotProfile,
    RobotProfileKey,
    RobotProfileVariable,
)
from cramera_protocol.reach import ReachRequest, RobotReach
from cramera_protocol.validation import (
    StepFailureReason,
    StepVerdict,
    ValidationReport,
    ValidationRequest,
)
from cramera_protocol.viewer import ErrorKey

from .conftest import DEMO_PORT, send
from .dataset.demo_source import demo_document
from .dataset.given_workspace import DATASET
from cramera_plan_builder.plugin import NAVIGATION_TITLE
from cramera_protocol.viewer import NavigationGroup, ViewerRoute
from cramera_protocol.plan_builder import PlanBuilderFile

# %% configuration


def test_demos_go_below_the_data_directory_unless_configured(tmp_path):
    locations = DataLocations(data=tmp_path, shared_scenes=None)
    default = PlanBuilderStorage.of(locations, {})
    configured = PlanBuilderStorage.of(
        locations, {PlanBuilderVariable.OUTPUT: str(tmp_path / "elsewhere")}
    )
    assert default.demos == tmp_path / StorageDirectory.BUILDER / StorageDirectory.DEMOS
    assert configured.demos == tmp_path / "elsewhere"
    assert default.plans == configured.plans


def test_demos_publish_on_the_live_port_unless_configured():
    assert PlanBuilderPlugin(environment={}).port() == LIVE_PORT
    configured = PlanBuilderPlugin(environment={PlanBuilderVariable.PORT: DEMO_PORT})
    assert configured.port() == int(DEMO_PORT)


# %% catalog


def test_the_catalog_lists_the_workspace_and_the_environments(get, given_workspace):
    answer = get(PlanBuilderRoute.CATALOG)
    catalog = PlanBuilderCatalog.from_json(json.loads(answer.body))
    assert answer.status == HTTPStatus.OK
    assert catalog.robots == given_workspace.robots()
    assert catalog.objects == given_workspace.objects()
    assert [entry.name for entry in catalog.environments] == ["lab"]
    assert catalog.live_url == f"http://{LIVE_HOST}:{DEMO_PORT}"
    assert catalog.missing == []
    assert catalog.limits == PlanBuilderLimits()


def test_the_catalog_lists_the_actions_and_the_modules_that_failed(
    get, given_workspace
):
    catalog = PlanBuilderCatalog.from_json(
        json.loads(get(PlanBuilderRoute.CATALOG).body)
    )
    discovered = given_workspace.action_catalog()
    assert catalog.actions == discovered.actions
    assert catalog.failed_action_modules == discovered.failed_modules


def test_without_cram_the_catalog_names_what_is_missing(serve, output):
    plugin = PlanBuilderPlugin(
        environment={PlanBuilderVariable.OUTPUT: str(output)},
        workspace=WorkspaceLoader(modules=CramModules(finder=lambda name: None)),
    )
    answer = send(serve(plugin), HttpMethod.GET, PlanBuilderRoute.CATALOG)
    catalog = PlanBuilderCatalog.from_json(json.loads(answer.body))
    assert catalog.missing == [module.value for module in CramModule]
    assert catalog.robots == [] and catalog.objects == []
    assert catalog.actions == [] and catalog.failed_action_modules == []
    assert [entry.name for entry in catalog.environments] == ["lab"]


def test_the_workspace_is_read_as_the_plan_builder_starts(serve, output):
    workspace = WorkspaceLoader(modules=CramModules(finder=lambda name: None))
    plugin = PlanBuilderPlugin(
        environment={PlanBuilderVariable.OUTPUT: str(output)}, workspace=workspace
    )
    serve(plugin)
    deadline = time.monotonic() + 5.0
    while workspace.loaded is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert workspace.loaded == MissingWorkspace([module.value for module in CramModule])


def test_the_workspace_is_not_read_while_the_routes_are_built(
    output, scenes, locations
):
    workspace = WorkspaceLoader(modules=CramModules(finder=lambda name: None))
    plugin = PlanBuilderPlugin(
        environment={PlanBuilderVariable.OUTPUT: str(output)}, workspace=workspace
    )
    plugin.routes(PluginContext(catalog=scenes, locations=locations))
    time.sleep(0.2)
    assert workspace.loaded is None


def test_an_object_models_file_is_served(get):
    answer = get(f"{PlanBuilderRoute.OBJECT_FILES}milk.stl")
    assert answer.status == HTTPStatus.OK
    assert answer.media_type is MediaType.STL
    assert answer.body == (DATASET / "objects" / "milk.stl").read_bytes()


def test_a_robot_models_description_is_served(get):
    answer = get(f"{PlanBuilderRoute.ROBOT_FILES}pr2/pr2.urdf")
    assert answer.status == HTTPStatus.OK
    assert b'<robot name="pr2">' in answer.body


def test_without_cram_no_object_file_is_served(serve, output):
    plugin = PlanBuilderPlugin(environment={PlanBuilderVariable.OUTPUT: str(output)})
    plugin.workspace.loaded = MissingWorkspace([CramModule.CORAPLEX.value])
    answer = send(
        serve(plugin), HttpMethod.GET, f"{PlanBuilderRoute.OBJECT_FILES}milk.stl"
    )
    assert answer.status == HTTPStatus.SERVICE_UNAVAILABLE


# %% environments

URDF = '<robot name="apartment"><link name="floor"/></robot>'
"""
A URDF of one link, loaded as an environment.
"""


def answerer_with_local_scenes(plugin, scenes, locations) -> RequestAnswerer:
    """
    :return: The viewer's request answerer whose catalog holds the local scenes before
        the test scenes, as the viewer's command line builds it.
    """
    catalog = SceneCatalog(
        [SceneDirectory(locations.local_scenes, SceneSource.LOCAL), *scenes.directories]
    )
    application = ViewerApplication(
        catalog=catalog, locations=locations, plugins=[plugin]
    )
    return RequestAnswerer(router=application.router(), policy=OriginPolicy())


def load(answerer, path) -> object:
    """
    :return: The answer to loading the path as an environment.
    """
    body = json.dumps(UrdfEnvironment(path=str(path)).to_json()).encode("utf-8")
    return send(answerer, HttpMethod.POST, PlanBuilderRoute.ENVIRONMENTS, body)


def test_a_urdf_becomes_an_environment_the_catalog_lists(
    plugin, scenes, locations, tmp_path
):
    source = tmp_path / "apartment.urdf"
    source.write_text(URDF, encoding="utf-8")
    answerer = answerer_with_local_scenes(plugin, scenes, locations)
    answer = load(answerer, source)
    loaded = LoadedEnvironment.from_json(json.loads(answer.body))
    assert answer.status == HTTPStatus.OK
    assert loaded.entry.name == "apartment"
    assert (locations.local_scenes / "apartment" / "apartment.urdf").is_file()
    catalog = PlanBuilderCatalog.from_json(
        json.loads(send(answerer, HttpMethod.GET, PlanBuilderRoute.CATALOG).body)
    )
    assert [entry.name for entry in catalog.environments] == ["apartment", "lab"]


@pytest.mark.parametrize("name", ["apartment.stl", "none.urdf"])
def test_only_an_existing_urdf_is_loaded(post, tmp_path, name):
    (tmp_path / "apartment.stl").write_text("solid", encoding="utf-8")
    answer = post(
        PlanBuilderRoute.ENVIRONMENTS,
        UrdfEnvironment(path=str(tmp_path / name)).to_json(),
    )
    assert answer.status == HTTPStatus.NOT_FOUND
    assert name in json.loads(answer.body)[ErrorKey.ERROR]


def test_without_cram_no_urdf_is_loaded(serve, output, tmp_path):
    source = tmp_path / "apartment.urdf"
    source.write_text(URDF, encoding="utf-8")
    plugin = PlanBuilderPlugin(
        environment={PlanBuilderVariable.OUTPUT: str(output)},
        workspace=WorkspaceLoader(modules=CramModules(finder=lambda name: None)),
    )
    assert load(serve(plugin), source).status == HTTPStatus.SERVICE_UNAVAILABLE


# %% plans


def test_a_saved_plan_is_listed_and_loaded(get, post, plan):
    saved = PlanRecord.from_json(
        json.loads(
            post(PlanBuilderRoute.PLANS, PlanRecord(document=plan).to_json()).body
        )
    )
    listed = PlanList.from_json(json.loads(get(PlanBuilderRoute.PLANS).body))
    loaded = get(f"{PlanBuilderRoute.PLAN}{saved.identifier}")
    assert [summary.identifier for summary in listed.plans] == [saved.identifier]
    assert PlanRecord.from_json(json.loads(loaded.body)) == saved
    catalog = PlanBuilderCatalog.from_json(
        json.loads(get(PlanBuilderRoute.CATALOG).body)
    )
    assert catalog.plans == listed.plans


def test_an_unfinished_plan_is_saved(post, read_plan):
    answer = post(
        PlanBuilderRoute.PLANS,
        PlanRecord(document=read_plan("unfinished.json")).to_json(),
    )
    assert answer.status == HTTPStatus.OK


def test_an_unknown_plan_is_not_found(get):
    assert get(f"{PlanBuilderRoute.PLAN}missing").status == HTTPStatus.NOT_FOUND


def test_a_body_that_is_no_json_is_refused(answerer):
    answer = send(answerer, HttpMethod.POST, PlanBuilderRoute.PLANS, b"{not json")
    assert answer.status == HTTPStatus.BAD_REQUEST
    assert ErrorKey.ERROR in json.loads(answer.body)


def test_a_body_beyond_the_document_limit_is_refused(answerer):
    body = b" " * (PlanBuilderLimits().maximum_document_bytes + 1)
    answer = send(answerer, HttpMethod.POST, PlanBuilderRoute.PLANS, body)
    assert answer.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


# %% constraints


def test_a_constraint_is_compiled_for_its_step(post):
    answer = post(
        PlanBuilderRoute.CONSTRAINT,
        ConstraintText(
            text="Keep the milk upright",
            step_action="transport",
            objects=["milk"],
        ).to_json(),
    )
    compiled = StepConstraint.from_json(json.loads(answer.body))
    assert compiled.monitor is not None and compiled.monitor_type is not None


def test_a_constraint_turns_on_a_switch_of_the_steps_action(post):
    answer = post(
        PlanBuilderRoute.CONSTRAINT,
        ConstraintText(
            text="Look where it operates", step_action="transport"
        ).to_json(),
    )
    compiled = StepConstraint.from_json(json.loads(answer.body))
    assert compiled.switch == ConstraintCompiler.of_file().rule_for("look at").switch


def test_a_constraint_on_an_action_the_workspace_lacks_turns_on_nothing(post):
    answer = post(
        PlanBuilderRoute.CONSTRAINT,
        ConstraintText(text="Look where it operates", step_action="search").to_json(),
    )
    assert StepConstraint.from_json(json.loads(answer.body)).switch is None


def test_a_blank_constraint_is_refused(post):
    answer = post(PlanBuilderRoute.CONSTRAINT, ConstraintText(text=" ").to_json())
    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY


# %% demos


def test_the_demo_carries_the_plan_and_its_environment(post, plan, scenes):
    answer = post(PlanBuilderRoute.DEMO, plan.to_json())
    source = DemoSource.from_json(json.loads(answer.body))
    lab = scenes.directory_of(plan.environment).path / plan.environment
    assert answer.status == HTTPStatus.OK
    assert source.file_name == f"{plan.name}{DEMO_SUFFIX}"
    assert demo_document(source.source) == DemoDocument(
        plan=plan, environment_directory=str(lab.resolve())
    )


def test_an_unfinished_plan_has_no_demo(post, read_plan):
    answer = post(PlanBuilderRoute.DEMO, read_plan("unfinished.json").to_json())
    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_plan_in_a_bundle_holding_a_robot_has_no_demo(post, plan):
    plan.environment = "cell"
    answer = post(PlanBuilderRoute.DEMO, plan.to_json())
    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_written_demo_lies_in_the_output_directory(post, plan, output):
    saved = SavedDemo.from_json(
        json.loads(post(PlanBuilderRoute.DEMOS, plan.to_json()).body)
    )
    assert saved.path == str((output / f"{plan.name}{DEMO_SUFFIX}").resolve())
    assert saved.path in saved.command


# %% runs


def test_the_demo_process_is_idle_before_a_run(get):
    status = RunStatus.from_json(json.loads(get(PlanBuilderRoute.RUN).body))
    assert status.state is RunState.IDLE


def test_a_run_is_started_and_stopped(serve, output, workspace_loader, plan):
    plugin = PlanBuilderPlugin(
        environment={PlanBuilderVariable.OUTPUT: str(output)},
        workspace=workspace_loader,
        template=DemoTemplate(DATASET / "templates" / "waiting_demo.py"),
    )
    answerer = serve(plugin)

    def post(route, body):
        return send(answerer, HttpMethod.POST, route, json.dumps(body).encode())

    def get(route):
        return send(answerer, HttpMethod.GET, route)

    started = RunStatus.from_json(
        json.loads(
            post(
                PlanBuilderRoute.RUN,
                RunRequest(plan=plan, mode=RunMode.STAGE).to_json(),
            ).body
        )
    )
    stopped = RunStatus.from_json(json.loads(post(PlanBuilderRoute.RUN_STOP, {}).body))
    assert started.state is RunState.RUNNING and started.mode is RunMode.STAGE
    assert stopped.state is RunState.STOPPED
    assert RunStatus.from_json(json.loads(get(PlanBuilderRoute.RUN).body)) == stopped


def test_an_unfinished_plan_is_not_run(post, read_plan):
    answer = post(
        PlanBuilderRoute.RUN,
        RunRequest(plan=read_plan("unfinished.json")).to_json(),
    )
    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_demo_document_names_no_environment_for_an_empty_floor(post, plan):
    plan.environment = None
    answer = post(PlanBuilderRoute.DEMO, plan.to_json())
    source = DemoSource.from_json(json.loads(answer.body))
    assert demo_document(source.source) == DemoDocument(plan=plan)


def test_the_plugin_has_an_entry_in_the_viewers_navigation():
    navigation = PlanBuilderPlugin(environment={}).info().navigation
    assert navigation.title == NAVIGATION_TITLE
    assert navigation.group == NavigationGroup.BUILDER
    assert (
        navigation.page
        == f"{ViewerRoute.PLUGIN_FILES}{PlanBuilderPlugin(environment={}).name()}/{PlanBuilderFile.PAGE}"
    )


# %% robot profile


def test_the_catalog_carries_the_robot_profile_the_environment_names(
    serve, output, workspace_loader, tmp_path
):
    profile = RobotProfile(robot="tracy", label="Tracy")
    path = tmp_path / "tracy.json"
    path.write_text(json.dumps(profile.to_json()), encoding="utf-8")
    plugin = PlanBuilderPlugin(
        environment={
            PlanBuilderVariable.OUTPUT: str(output),
            RobotProfileVariable.PROFILE: str(path),
        },
        workspace=workspace_loader,
    )
    answer = send(serve(plugin), HttpMethod.GET, PlanBuilderRoute.CATALOG)
    assert PlanBuilderCatalog.from_json(json.loads(answer.body)).profile == profile


def test_without_a_configured_profile_the_catalog_carries_none(get):
    answer = get(PlanBuilderRoute.CATALOG)
    assert PlanBuilderCatalog.from_json(json.loads(answer.body)).profile is None


def test_a_run_mode_the_profile_does_not_offer_is_refused(
    serve, output, workspace_loader, tmp_path, plan
):
    profile = RobotProfile(
        robot="tracy",
        label="Tracy",
        executors={RunMode.PERFORM: ExecutorKind.KINEMATIC},
    )
    path = tmp_path / "tracy.json"
    path.write_text(json.dumps(profile.to_json()), encoding="utf-8")
    plugin = PlanBuilderPlugin(
        environment={
            PlanBuilderVariable.OUTPUT: str(output),
            RobotProfileVariable.PROFILE: str(path),
        },
        workspace=workspace_loader,
        template=DemoTemplate(DATASET / "templates" / "waiting_demo.py"),
    )
    answer = send(
        serve(plugin),
        HttpMethod.POST,
        PlanBuilderRoute.RUN,
        json.dumps(RunRequest(plan=plan, mode=RunMode.SIMULATE).to_json()).encode(),
    )
    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_catalog_carries_the_profiles_templates(serve, output, workspace_loader):
    profile_file = DATASET / "profile" / "tracy.json"
    plugin = PlanBuilderPlugin(
        environment={
            PlanBuilderVariable.OUTPUT: str(output),
            RobotProfileVariable.PROFILE: str(profile_file),
        },
        workspace=workspace_loader,
    )
    answer = send(serve(plugin), HttpMethod.GET, PlanBuilderRoute.CATALOG)
    catalog = PlanBuilderCatalog.from_json(json.loads(answer.body))
    assert [template.identifier for template in catalog.templates] == ["park"]


# %% scanning the scene


def test_a_scan_sees_the_simulated_scene_the_profile_names(
    serve, output, workspace_loader, plan
):
    profile_file = DATASET / "profile" / "tracy.json"
    plugin = PlanBuilderPlugin(
        environment={
            PlanBuilderVariable.OUTPUT: str(output),
            RobotProfileVariable.PROFILE: str(profile_file),
        },
        workspace=workspace_loader,
        scans=SceneScans(
            interpreter=str(DATASET / "scanning_interpreter.py"),
            modules=CramModules(
                finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
            ),
        ),
    )
    answer = send(
        serve(plugin),
        HttpMethod.POST,
        PlanBuilderRoute.SCAN,
        json.dumps(ScanRequest(plan=plan).to_json()).encode(),
    )
    assert answer.status == HTTPStatus.OK, answer.body
    scan = SceneScan.from_json(json.loads(answer.body))
    assert scan.objects == ConfiguredProfile.of_file(profile_file).simulated_objects


def scanning_plugin(output, workspace_loader) -> PlanBuilderPlugin:
    """
    :return: A plan builder of Tracy's profile whose scanner is the stand-in that sees
        the profile's simulated objects.
    """
    return PlanBuilderPlugin(
        environment={
            PlanBuilderVariable.OUTPUT: str(output),
            RobotProfileVariable.PROFILE: str(DATASET / "profile" / "tracy.json"),
        },
        workspace=workspace_loader,
        scans=SceneScans(
            interpreter=str(DATASET / "scanning_interpreter.py"),
            modules=CramModules(
                finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
            ),
        ),
    )


def test_a_scan_does_not_need_the_steps_to_be_complete(
    serve, output, workspace_loader, plan
):
    del plan.robots[0].steps[3].values["object_designator"]
    plan.robots[0].steps[5].values["target_location"] = Destination(
        placement=PlacementMode.ZONE
    ).to_json()
    answer = send(
        serve(scanning_plugin(output, workspace_loader)),
        HttpMethod.POST,
        PlanBuilderRoute.SCAN,
        json.dumps(ScanRequest(plan=plan).to_json()).encode(),
    )
    assert answer.status == HTTPStatus.OK, answer.body


def test_a_scan_refuses_an_object_the_catalog_does_not_offer(
    serve, output, workspace_loader, plan
):
    plan.objects[0].model = "anvil.stl"
    answer = send(
        serve(scanning_plugin(output, workspace_loader)),
        HttpMethod.POST,
        PlanBuilderRoute.SCAN,
        json.dumps(ScanRequest(plan=plan).to_json()).encode(),
    )
    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "anvil.stl" in json.loads(answer.body)[ErrorKey.ERROR]


# %% validating


def validating_plugin(
    output,
    workspace_loader,
    profile_file,
    connection: RobotConnection = RobotConnection.SIMULATED,
) -> PlanBuilderPlugin:
    """
    :return: A plan builder of the profile whose validator is the stand-in that fails
        the step a plan's task names as unreachable, attached to the robot as
        `connection` says.
    """
    return PlanBuilderPlugin(
        environment={
            PlanBuilderVariable.OUTPUT: str(output),
            RobotProfileVariable.PROFILE: str(profile_file),
            RobotProfileVariable.CONNECTION: connection,
        },
        workspace=workspace_loader,
        template=DemoTemplate(DATASET / "templates" / "waiting_demo.py"),
        validations=PlanValidations(
            interpreter=str(DATASET / "validating_interpreter.py"),
            modules=CramModules(
                finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
            ),
        ),
    )


def validate(answer, plan) -> ValidationReport:
    """
    :return: The report of the plan's validation.
    """
    validation = send(
        answer,
        HttpMethod.POST,
        PlanBuilderRoute.VALIDATE,
        json.dumps(ValidationRequest(plan=plan).to_json()).encode(),
    )
    assert validation.status == HTTPStatus.OK, validation.body
    return ValidationReport.from_json(json.loads(validation.body))


def run_on_the_robot(answer, plan, override: bool = False) -> HTTPStatus:
    """
    :return: The status the plan builder answers a run of the plan on the robot with.
    """
    request = RunRequest(plan=plan, mode=RunMode.ROBOT, override_validation=override)
    return send(
        answer,
        HttpMethod.POST,
        PlanBuilderRoute.RUN,
        json.dumps(request.to_json()).encode(),
    ).status


@pytest.fixture
def validating(serve, output, workspace_loader):
    """
    :return: Answers requests to a plan builder of Tracy's test profile, which names no
        executor for the robot's run, with the stand-in validator.
    """
    return serve(
        validating_plugin(output, workspace_loader, DATASET / "profile" / "tracy.json")
    )


def test_a_validation_answers_the_validators_report(validating, plan):
    report = validate(validating, plan)

    assert report.passed
    assert [step.step for step in report.steps] == [
        step.identifier for step in plan.performing_robot().steps
    ]
    assert report.seconds > 0


def test_a_robot_run_of_a_plan_never_validated_is_refused(validating, plan):
    assert (
        run_on_the_robot(validating, plan) == PlanNotValidated(RunMode.ROBOT).status()
    )


def test_a_robot_run_of_the_validated_plan_passes_the_gate(validating, plan):
    validate(validating, plan)

    # Tracy's test profile names no executor for the robot's run.
    assert (
        run_on_the_robot(validating, plan)
        == RunModeNotOffered(RunMode.ROBOT, "Tracy").status()
    )


def test_a_plan_changed_after_its_validation_is_not_run_on_the_robot(validating, plan):
    validate(validating, plan)
    [robot, *others] = plan.robots
    changed = replace(plan, robots=[replace(robot, steps=robot.steps[:-1]), *others])

    assert (
        run_on_the_robot(validating, changed)
        == PlanNotValidated(RunMode.ROBOT).status()
    )


def test_a_failed_validation_closes_the_gate(validating, plan):
    validate(validating, plan)
    failing = replace(plan, task=plan.performing_robot().steps[0].identifier)

    report = validate(validating, failing)

    assert not report.passed
    assert report.steps[0].reason is StepFailureReason.UNREACHABLE
    assert (
        run_on_the_robot(validating, failing)
        == PlanNotValidated(RunMode.ROBOT).status()
    )


def run_status(answer) -> RunStatus:
    """
    :return: The status the plan builder answers for its demo started last.
    """
    return RunStatus.from_json(
        json.loads(send(answer, HttpMethod.GET, PlanBuilderRoute.RUN).body)
    )


def test_the_run_status_says_whether_a_validation_is_held(validating, plan):
    assert run_status(validating).validation_held is False

    validate(validating, plan)

    assert run_status(validating).validation_held is True


def test_a_restarted_plan_builder_holds_no_validation(
    serve, output, workspace_loader, plan
):
    profile = DATASET / "profile" / "tracy.json"
    validate(serve(validating_plugin(output, workspace_loader, profile)), plan)

    restarted = serve(validating_plugin(output, workspace_loader, profile))

    assert run_status(restarted).validation_held is False


def test_developer_mode_overrides_the_gate(validating, plan):
    assert (
        run_on_the_robot(validating, plan, override=True)
        == RunModeNotOffered(RunMode.ROBOT, "Tracy").status()
    )


def test_a_step_of_a_skill_the_profile_lacks_fails_without_trying(validating, plan):
    [robot, *others] = plan.robots
    [first, *rest] = robot.steps
    unoffered = replace(
        plan,
        robots=[replace(robot, steps=[replace(first, skill="juggle"), *rest]), *others],
    )

    report = validate(validating, unoffered)

    assert report.steps[0].reason is StepFailureReason.SKILL_NOT_OFFERED
    assert {step.verdict for step in report.steps[1:]} == {StepVerdict.UNCHECKED}


def real_robot_profile(tmp_path) -> Path:
    """
    :return: Tracy's test profile, written into the test's directory, that runs the
        robot run mode on the real robot.
    """
    profile_file = DATASET / "profile" / "tracy.json"
    document = json.loads(profile_file.read_text("utf-8"))
    document[RobotProfileKey.EXECUTORS] = {RunMode.ROBOT: ExecutorKind.REAL}
    real = tmp_path / "profile" / "tracy.json"
    real.parent.mkdir()
    real.write_text(json.dumps(document), "utf-8")
    for referenced in ("templates", "scenes"):
        (real.parent / referenced).symlink_to(profile_file.parent / referenced)
    return real


@pytest.fixture
def attached_to_the_real_robot(serve, output, workspace_loader, tmp_path):
    """
    :return: Answers requests to a plan builder attached to the real robot, whose
        profile runs the robot run mode on it, with the stand-in validator.
    """
    return serve(
        validating_plugin(
            output, workspace_loader, real_robot_profile(tmp_path), RobotConnection.REAL
        )
    )


@dataclass
class StoppedClock:
    """
    A clock that stands still until a test moves it on.
    """

    now: float = 100.0
    """
    The time it tells, in seconds.
    """

    def __call__(self) -> float:
        return self.now


def mirroring_plugin(
    output,
    workspace_loader,
    connection,
    interpreter: str = "mirroring_interpreter.py",
    clock: Callable[[], float] = time.monotonic,
) -> PlanBuilderPlugin:
    """
    :return: A plan builder attached to the robot as `connection` says, whose posture
        mirror is the stand-in `interpreter`, by default the one reporting a turned
        left shoulder whose right arm's driver never reported.
    """
    return replace(
        validating_plugin(
            output, workspace_loader, DATASET / "profile" / "tracy.json", connection
        ),
        mirror=PostureMirror(
            interpreter=str(DATASET / interpreter),
            modules=CramModules(
                finder=lambda name: ModuleSpec(name, loader=None, origin=f"{name}.py")
            ),
            clock=clock,
        ),
    )


def answered_posture(answerer) -> RobotPosture:
    """
    :return: The posture the plan builder answers now.
    """
    answer = send(answerer, HttpMethod.GET, PlanBuilderRoute.POSTURE)
    assert answer.status == HTTPStatus.OK, answer.body
    return RobotPosture.from_json(json.loads(answer.body))


def mirrored_posture(answerer) -> RobotPosture:
    """
    :return: The posture the plan builder answers, once the mirror reported one or
        after a few seconds.
    """
    deadline = time.monotonic() + 10.0
    posture = RobotPosture()
    while not posture.joint_positions and time.monotonic() < deadline:
        time.sleep(0.05)
        posture = answered_posture(answerer)
    return posture


def test_attached_to_the_real_robot_the_builder_mirrors_its_posture_without_a_run(
    serve, output, workspace_loader
):
    plugin = mirroring_plugin(output, workspace_loader, RobotConnection.REAL)
    answerer = serve(plugin)

    posture = mirrored_posture(answerer)
    plugin.close()

    assert posture.joint_positions == {"left_shoulder_pan_joint": 0.3}
    assert posture.mirrored
    assert plugin.mirror.process.poll() is not None


def test_an_arm_reads_as_live_only_while_its_driver_reported_within_a_second(
    serve, output, workspace_loader
):
    clock = StoppedClock()
    plugin = mirroring_plugin(
        output, workspace_loader, RobotConnection.REAL, clock=clock
    )
    answerer = serve(plugin)

    fresh = mirrored_posture(answerer)
    clock.now += STALE_AFTER_SECONDS + 0.5
    stale = answered_posture(answerer)
    plugin.close()

    assert fresh.arms == [
        ArmReport(arm="left_arm", age_seconds=0.0, live=True),
        ArmReport(arm="right_arm"),
    ]
    assert stale.arms[0] == ArmReport(
        arm="left_arm", age_seconds=pytest.approx(STALE_AFTER_SECONDS + 0.5)
    )


def test_no_arm_reads_as_live_once_the_mirror_ended(serve, output, workspace_loader):
    plugin = mirroring_plugin(
        output, workspace_loader, RobotConnection.REAL, clock=StoppedClock()
    )
    answerer = serve(plugin)
    mirrored_posture(answerer)

    plugin.close()
    posture = answered_posture(answerer)

    assert posture.mirrored
    assert posture.joint_positions == {"left_shoulder_pan_joint": 0.3}
    assert not any(arm.live for arm in posture.arms)


def test_a_mirror_that_never_reported_names_no_arm_as_live(
    serve, output, workspace_loader
):
    plugin = mirroring_plugin(
        output,
        workspace_loader,
        RobotConnection.REAL,
        interpreter="silent_mirroring_interpreter.py",
    )

    posture = answered_posture(serve(plugin))
    plugin.close()

    assert posture == RobotPosture(mirrored=True)


def test_a_simulated_robot_has_no_mirrored_posture(serve, output, workspace_loader):
    plugin = mirroring_plugin(output, workspace_loader, RobotConnection.SIMULATED)

    posture = answered_posture(serve(plugin))

    assert posture == RobotPosture()
    assert plugin.mirror.process is None


def test_a_run_on_the_real_robot_needs_a_new_validation_for_the_next_run(
    attached_to_the_real_robot, plan
):
    validate(attached_to_the_real_robot, plan)

    assert run_on_the_robot(attached_to_the_real_robot, plan) == HTTPStatus.OK
    assert (
        run_on_the_robot(attached_to_the_real_robot, plan)
        == PlanNotValidated(RunMode.ROBOT).status()
    )


def test_a_run_on_the_real_robot_answers_that_it_took_the_validation(
    attached_to_the_real_robot, plan
):
    validate(attached_to_the_real_robot, plan)
    request = RunRequest(plan=plan, mode=RunMode.ROBOT)

    started = send(
        attached_to_the_real_robot,
        HttpMethod.POST,
        PlanBuilderRoute.RUN,
        json.dumps(request.to_json()).encode(),
    )

    assert RunStatus.from_json(json.loads(started.body)).validation_held is False


def test_no_request_overrides_the_gate_of_a_run_on_the_real_robot(
    attached_to_the_real_robot, plan
):
    assert (
        run_on_the_robot(attached_to_the_real_robot, plan, override=True)
        == PlanNotValidated(RunMode.ROBOT).status()
    )


def test_a_run_on_the_real_robot_is_refused_in_plain_words_until_validated(
    attached_to_the_real_robot, plan
):
    request = RunRequest(plan=plan, mode=RunMode.ROBOT)

    refused = send(
        attached_to_the_real_robot,
        HttpMethod.POST,
        PlanBuilderRoute.RUN,
        json.dumps(request.to_json()).encode(),
    )

    assert json.loads(refused.body)[ErrorKey.ERROR] == (
        "A run on the real robot needs the plan to pass its validation first: "
        "press Validate, then run it."
    )


def test_a_real_run_after_the_real_run_of_its_validation_cannot_be_forced(
    attached_to_the_real_robot, plan
):
    validate(attached_to_the_real_robot, plan)
    run_on_the_robot(attached_to_the_real_robot, plan)

    assert (
        run_on_the_robot(attached_to_the_real_robot, plan, override=True)
        == PlanNotValidated(RunMode.ROBOT).status()
    )


@dataclass(frozen=True)
class RunStartingValidations(PlanValidations):
    """
    The stand-in validator, after which each of :attr:`starts` is called before the
    validation answers: a run started while a plan is validated.
    """

    starts: List[Callable[[], object]] = field(default_factory=list)
    """
    Called once a validation's report is in.
    """

    def validate(self, order):
        report = super().validate(order)
        for start in self.starts:
            start()
        return report


@pytest.fixture
def real_run_plugin(output, workspace_loader, tmp_path) -> PlanBuilderPlugin:
    """
    :return: A plan builder attached to the real robot whose demos run until they are
        stopped, with the stand-in validator.
    """
    plugin = validating_plugin(
        output, workspace_loader, real_robot_profile(tmp_path), RobotConnection.REAL
    )
    return replace(
        plugin,
        template=DemoTemplate(DATASET / "templates" / "running_demo.py"),
        validations=RunStartingValidations(
            interpreter=plugin.validations.interpreter,
            modules=plugin.validations.modules,
        ),
    )


def test_a_real_run_refused_before_anything_moved_keeps_the_plans_validation(
    serve, output, workspace_loader, tmp_path, plan
):
    plugin = validating_plugin(
        output, workspace_loader, real_robot_profile(tmp_path), RobotConnection.REAL
    )
    answer = serve(
        replace(
            plugin, template=DemoTemplate(DATASET / "templates" / "refused_demo.py")
        )
    )
    validate(answer, plan)

    assert run_on_the_robot(answer, plan) == HTTPStatus.OK
    deadline = time.monotonic() + 10.0
    while plugin_run_state(answer) is not RunState.REFUSED:
        assert time.monotonic() < deadline
        time.sleep(0.05)

    assert run_on_the_robot(answer, plan) == HTTPStatus.OK


def plugin_run_state(answer) -> RunState:
    """
    :return: The state of the plan builder's demo started last.
    """
    status = send(answer, HttpMethod.GET, PlanBuilderRoute.RUN)
    return RunStatus.from_json(json.loads(status.body)).state


def refused_validation(answer, plan):
    """
    :return: The answer to a validation of the plan the plan builder refused.
    """
    validation = send(
        answer,
        HttpMethod.POST,
        PlanBuilderRoute.VALIDATE,
        json.dumps(ValidationRequest(plan=plan).to_json()).encode(),
    )
    assert validation.status != HTTPStatus.OK
    return validation


def test_no_validation_while_a_plan_runs_on_the_real_robot(
    serve, real_run_plugin, plan
):
    answer = serve(real_run_plugin)
    validate(answer, plan)
    assert run_on_the_robot(answer, plan) == HTTPStatus.OK

    refused = refused_validation(answer, plan)

    assert refused.status == HTTPStatus.CONFLICT
    assert (
        json.loads(refused.body)[ErrorKey.ERROR]
        == PlanRunning(RunMode.ROBOT.value).message()
    )
    assert run_on_the_robot(answer, plan) == PlanNotValidated(RunMode.ROBOT).status()
    send(answer, HttpMethod.POST, PlanBuilderRoute.RUN_STOP)

    assert validate(answer, plan).passed
    assert run_on_the_robot(answer, plan) == HTTPStatus.OK
    send(answer, HttpMethod.POST, PlanBuilderRoute.RUN_STOP)


def test_a_validation_during_which_a_real_run_started_is_not_recorded(
    serve, real_run_plugin, plan
):
    answer = serve(real_run_plugin)
    validate(answer, plan)
    real_run_plugin.validations.starts.append(lambda: run_on_the_robot(answer, plan))

    refused = refused_validation(answer, plan)

    real_run_plugin.validations.starts.clear()
    assert refused.status == HTTPStatus.CONFLICT
    assert run_on_the_robot(answer, plan) == PlanNotValidated(RunMode.ROBOT).status()
    send(answer, HttpMethod.POST, PlanBuilderRoute.RUN_STOP)


# %% placing


@pytest.fixture
def profiled(serve, output, workspace_loader):
    """
    :return: Answers requests to a plan builder of Tracy's test profile, which names a
        left tray, measuring reach with the stand-in.
    """
    return serve(
        PlanBuilderPlugin(
            environment={
                PlanBuilderVariable.OUTPUT: str(output),
                RobotProfileVariable.PROFILE: str(DATASET / "profile" / "tracy.json"),
            },
            workspace=workspace_loader,
            reaches=RobotReaches(
                interpreter=str(DATASET / "reaching_interpreter.py"),
                modules=CramModules(
                    finder=lambda name: ModuleSpec(
                        name, loader=None, origin=f"{name}.py"
                    )
                ),
            ),
        )
    )


def placing_at(plan, destination: Destination):
    """
    :return: The plan whose place step puts the milk down at the destination.
    """
    [robot, *others] = plan.robots
    steps = [
        (
            replace(
                step, values={**step.values, "target_location": destination.to_json()}
            )
            if step.action == "place"
            else step
        )
        for step in robot.steps
    ]
    return replace(plan, robots=[replace(robot, steps=steps), *others])


def test_a_demo_carries_the_profiles_zones_a_place_step_names(profiled, plan):
    placed = placing_at(
        plan,
        Destination(placement=PlacementMode.ZONE, zone="left_tray"),
    )

    answer = send(
        profiled,
        HttpMethod.POST,
        PlanBuilderRoute.DEMO,
        json.dumps(placed.to_json()).encode(),
    )

    assert answer.status == HTTPStatus.OK, answer.body
    document = demo_document(DemoSource.from_json(json.loads(answer.body)).source)
    assert [zone.identifier for zone in document.zones] == ["left_tray"]


def test_a_demo_carries_the_profiles_safety_limits(profiled, plan):
    answer = send(
        profiled,
        HttpMethod.POST,
        PlanBuilderRoute.DEMO,
        json.dumps(plan.to_json()).encode(),
    )

    assert answer.status == HTTPStatus.OK, answer.body
    document = demo_document(DemoSource.from_json(json.loads(answer.body)).source)
    profile = RobotProfile.from_file(DATASET / "profile" / "tracy.json")
    assert document.safety == profile.safety


def test_a_demo_carries_the_profiles_perception_settings(profiled, plan):
    answer = send(
        profiled,
        HttpMethod.POST,
        PlanBuilderRoute.DEMO,
        json.dumps(plan.to_json()).encode(),
    )

    assert answer.status == HTTPStatus.OK, answer.body
    document = demo_document(DemoSource.from_json(json.loads(answer.body)).source)
    profile = RobotProfile.from_file(DATASET / "profile" / "tracy.json")
    assert document.perception == profile.perception


@pytest.mark.parametrize(
    "destination",
    [
        Destination(placement=PlacementMode.ZONE, zone="kitchen"),
        Destination(placement=PlacementMode.ZONE),
        Destination(placement=PlacementMode.ON_TOP_OF, object="teapot"),
        Destination(placement=PlacementMode.MARKER),
    ],
    ids=["unknown zone", "no zone", "unknown object", "marker without pose"],
)
def test_a_place_step_naming_what_the_plan_lacks_has_no_demo(
    profiled, plan, destination
):
    placed = placing_at(plan, destination)

    answer = send(
        profiled,
        HttpMethod.POST,
        PlanBuilderRoute.DEMO,
        json.dumps(placed.to_json()).encode(),
    )

    assert answer.status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_place_step_on_top_of_an_object_or_at_a_marker_has_a_demo(profiled, plan):
    for destination in (
        Destination(placement=PlacementMode.ON_TOP_OF, object="milk"),
        Destination(
            placement=PlacementMode.MARKER, pose=Pose(0.7, 0.3, 0.8, 0, 0, 0, 1)
        ),
    ):
        answer = send(
            profiled,
            HttpMethod.POST,
            PlanBuilderRoute.DEMO,
            json.dumps(placing_at(plan, destination).to_json()).encode(),
        )
        assert answer.status == HTTPStatus.OK, answer.body


def test_what_a_robot_model_reaches_is_answered_and_an_unknown_model_refused(profiled):
    known = send(
        profiled,
        HttpMethod.POST,
        PlanBuilderRoute.REACH,
        json.dumps(ReachRequest(robot="tracy").to_json()).encode(),
    )
    unknown = send(
        profiled,
        HttpMethod.POST,
        PlanBuilderRoute.REACH,
        json.dumps(ReachRequest(robot="r2d2").to_json()).encode(),
    )

    assert known.status == HTTPStatus.OK, known.body
    assert RobotReach.from_json(json.loads(known.body)).robot == "tracy"
    assert unknown.status == HTTPStatus.UNPROCESSABLE_ENTITY
