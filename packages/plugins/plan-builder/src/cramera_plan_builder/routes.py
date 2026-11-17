"""
The plan builder's routes: the catalog, saved plans, constraint compilation, generated
demos, validation, the demo process and the object and robot models' files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from typing_extensions import Any, List, Optional

from cramera.http.messages import HttpMethod, Request, Response
from cramera.plugins import PluginPrefixRoute, PluginRoute, RouteDeclaration
from cramera.scenes import SceneCatalog
from cramera_plan_builder.constraints import ConstraintCompiler
from cramera_plan_builder.demos import DemoDirectory, DemoTemplate
from cramera_plan_builder.environments import (
    EnvironmentAnnotationCache,
    EnvironmentBundles,
)
from cramera_plan_builder.exceptions import (
    ContentTooLarge,
    MalformedRequestBody,
    NotAUrdfFile,
    PlanNotValidated,
    UnsupportedByCatalog,
)
from cramera_plan_builder.mirroring import PostureMirror
from cramera_plan_builder.plans import CatalogCheck, PlanCheck, PlanStore
from cramera_plan_builder.profiles import ConfiguredProfile
from cramera_plan_builder.reaches import RobotReaches
from cramera_plan_builder.runs import DemoRunner
from cramera_plan_builder.scans import SceneScans
from cramera_plan_builder.urdf_folders import URDF_SOURCE_SUFFIXES, UrdfFolders
from cramera_plan_builder.validations import PlanValidations, ValidationGate
from cramera_plan_builder.workspace import RobotModelFiles, WorkspaceLoader
from cramera_protocol.detection import ScanRequest, SceneScanOrder
from cramera_protocol.plan_builder import (
    ActionSchema,
    ConstraintText,
    DemoDocument,
    PlanBuilderCatalog,
    PlanBuilderLimits,
    PlanBuilderRoute,
    PlanDocument,
    PlanList,
    PlanRecord,
    RunRequest,
    RunStatus,
    SavedNames,
    UrdfEnvironment,
    UrdfFolderParameter,
    EnvironmentAnnotationsParameter,
)
from cramera_protocol.reach import ReachRequest, ReachRequestKey
from cramera_protocol.robot_profile import ExecutorKind, PerceptionSettings
from cramera_protocol.validation import (
    StepFailureReason,
    StepValidation,
    StepVerdict,
    ValidationReport,
    ValidationRequest,
)

# %% request bodies


@dataclass(frozen=True)
class JsonBody:
    """
    The JSON body of a request, read within a size limit.
    """

    request: Request
    """
    The request.
    """

    limit: int
    """
    How large the body may be, in bytes.
    """

    def read(self) -> Any:
        """
        The HTTP boundary is where a malformed body becomes a refusal, so this is the
        one place where the decoder's errors become the builder's.

        :return: The decoded body.
        :raises ContentTooLarge: When the body is larger than :attr:`limit`.
        :raises MalformedRequestBody: When the body is no UTF-8 JSON text.
        """
        if len(self.request.body) > self.limit:
            raise ContentTooLarge(len(self.request.body), self.limit)
        try:
            return json.loads(self.request.body.decode("utf-8"))
        except ValueError as error:
            raise MalformedRequestBody(str(error)) from error


# %% routes


@dataclass(frozen=True)
class PlanBuilderRoutes:
    """
    Answers the plan builder's requests.
    """

    scenes: SceneCatalog
    """
    The scenes the viewer shows, whose bundles without a robot are environments.
    """

    local_scenes: Path
    """
    The scenes directory environments loaded from a URDF are written to.
    """

    workspace: WorkspaceLoader
    """
    Reads what the CRAM workspace offers.
    """

    annotations: EnvironmentAnnotationCache
    """
    The environments' semantic annotations read so far.
    """

    robot_files: RobotModelFiles
    """
    The robots' model descriptions and their meshes.
    """

    plans: PlanStore
    """
    The saved plans.
    """

    compiler: ConstraintCompiler
    """
    Compiles constraints written in plain language.
    """

    demos: DemoDirectory
    """
    Where generated demos are written.
    """

    runner: DemoRunner
    """
    Runs the demo of a plan.
    """

    limits: PlanBuilderLimits
    """
    The bounds plans and demos stay within.
    """

    live_url: str
    """
    Where started demos publish their world.
    """

    template: DemoTemplate = field(default_factory=DemoTemplate)
    """
    Renders a plan into its demo.
    """

    urdf_folders: UrdfFolders = field(default_factory=UrdfFolders)
    """
    Lists the folders the URDF picker browses.
    """

    profile: ConfiguredProfile = field(default_factory=ConfiguredProfile)
    """
    The robot profile new plans start from and its templates; none by default.
    """

    scans: SceneScans = field(default_factory=SceneScans)
    """
    Scans the scene for a plan's objects.
    """

    validations: PlanValidations = field(default_factory=PlanValidations)
    """
    Validates plans.
    """

    gate: ValidationGate = field(default_factory=ValidationGate)
    """
    The plan whose validation passed last, which alone may run where a run needs one.
    """

    reaches: RobotReaches = field(default_factory=RobotReaches)
    """
    Measures what robot models reach.
    """

    mirror: PostureMirror = field(default_factory=PostureMirror)
    """
    Mirrors where the real robot stands, while the builder is attached to it.
    """

    def declarations(self) -> List[RouteDeclaration]:
        """
        :return: The routes, below the plugin's prefix.
        """
        return [
            PluginRoute(HttpMethod.GET, PlanBuilderRoute.CATALOG, self.answer_catalog),
            PluginRoute(HttpMethod.GET, PlanBuilderRoute.POSTURE, self.answer_posture),
            PluginRoute(
                HttpMethod.POST, PlanBuilderRoute.ENVIRONMENTS, self.answer_environment
            ),
            # the listing names any folder of this machine, so other devices may not ask
            PluginRoute(
                HttpMethod.GET,
                PlanBuilderRoute.URDF_FOLDER,
                self.answer_urdf_folder,
                local_only=True,
            ),
            PluginRoute(
                HttpMethod.GET,
                PlanBuilderRoute.ENVIRONMENT_ANNOTATIONS,
                self.answer_environment_annotations,
            ),
            PluginRoute(HttpMethod.GET, PlanBuilderRoute.PLANS, self.answer_plans),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.PLANS, self.answer_save),
            PluginPrefixRoute(HttpMethod.GET, PlanBuilderRoute.PLAN, self.answer_plan),
            PluginRoute(
                HttpMethod.POST, PlanBuilderRoute.CONSTRAINT, self.answer_constraint
            ),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.DEMO, self.answer_demo),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.DEMOS, self.answer_write),
            PluginRoute(HttpMethod.GET, PlanBuilderRoute.RUN, self.answer_run_status),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.RUN, self.answer_run),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.RUN_STOP, self.answer_stop),
            PluginRoute(
                HttpMethod.POST,
                PlanBuilderRoute.RUN_ACKNOWLEDGE,
                self.answer_acknowledge,
            ),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.SCAN, self.answer_scan),
            PluginRoute(
                HttpMethod.POST, PlanBuilderRoute.VALIDATE, self.answer_validate
            ),
            PluginRoute(HttpMethod.POST, PlanBuilderRoute.REACH, self.answer_reach),
            PluginPrefixRoute(
                HttpMethod.GET, PlanBuilderRoute.OBJECT_FILES, self.answer_object_file
            ),
            PluginPrefixRoute(
                HttpMethod.GET, PlanBuilderRoute.ROBOT_FILES, self.answer_robot_file
            ),
        ]

    # %% catalog

    def catalog(self) -> PlanBuilderCatalog:
        """
        :return: What the builder offers: the workspace's robots, objects and actions,
            the viewer's bundles without a robot, the rules and the saved plans.
        """
        workspace = self.workspace.workspace()
        actions = workspace.action_catalog()
        return PlanBuilderCatalog(
            live_url=self.live_url,
            robots=workspace.robots(),
            environments=EnvironmentBundles(self.scenes).entries(),
            objects=workspace.objects(),
            surface_types=workspace.surface_types(),
            saved_surface_types=workspace.saved_surface_types(),
            actions=actions.actions,
            failed_action_modules=actions.failed_modules,
            constraint_rules=self.compiler.offered_rules(),
            plans=self.plans.summaries(),
            limits=self.limits,
            missing=workspace.missing(),
            profile=self.profile.profile,
            templates=self.profile.templates,
        )

    def answer_catalog(self, request: Request) -> Response:
        """
        :param request: A request for the catalog.
        :return: The catalog.
        """
        return Response.of_json(self.catalog())

    def answer_environment(self, request: Request) -> Response:
        """
        Write a URDF or xacro file of this machine, with its meshes, as an environment
        bundle into the local scenes, where the catalog then lists it.

        :param request: A request whose body is a :class:`UrdfEnvironment`.
        :return: The written bundle's :class:`LoadedEnvironment`.
        :raises NotAUrdfFile: When the path names no URDF or xacro file.
        """
        given = UrdfEnvironment.from_json(self.body(request)).path
        source = Path(given.strip()).expanduser()
        if source.suffix not in URDF_SOURCE_SUFFIXES or not source.is_file():
            raise NotAUrdfFile(given)
        taken = {entry.name for entry in self.scenes.index().scenes}
        return Response.of_json(
            self.workspace.workspace().write_environment(
                source.resolve(), self.local_scenes, taken
            )
        )

    def answer_environment_annotations(self, request: Request) -> Response:
        """
        :param request: A request naming an environment with
            :attr:`EnvironmentAnnotationsParameter.ENVIRONMENT`.
        :return: The environment's :class:`EnvironmentAnnotations`.
        :raises UnknownScene: When no scenes directory holds the environment.
        """
        name = request.parameters().get(
            EnvironmentAnnotationsParameter.ENVIRONMENT, [""]
        )[0]
        directory = self.scenes.directory_of(name).path / name
        return Response.of_json(self.annotations.of(directory.resolve()))

    def answer_urdf_folder(self, request: Request) -> Response:
        """
        :param request: A request naming a folder with :attr:`UrdfFolderParameter.PATH`,
            or none for the home folder.
        :return: The folder's :class:`UrdfFolder`.
        """
        given = request.parameters().get(UrdfFolderParameter.PATH, [None])[0]
        return Response.of_json(self.urdf_folders.listing(given))

    def answer_object_file(self, request: Request, path: str) -> Response:
        """
        :param request: A request for an object model's file.
        :param path: The file, relative to the workspace's object directory.
        :return: The file.
        """
        return Response.of_file(self.workspace.workspace().object_file(path))

    def answer_robot_file(self, request: Request, path: str) -> Response:
        """
        :param request: A request for a robot model's file.
        :param path: The file: ``<robot>/<path>``.
        :return: The file.
        """
        return Response.of_file(self.robot_files.file(path))

    # %% plans

    def answer_plans(self, request: Request) -> Response:
        """
        :param request: A request for the saved plans.
        :return: The saved plans.
        """
        return Response.of_json(PlanList(plans=self.plans.summaries()))

    def answer_plan(self, request: Request, identifier: str) -> Response:
        """
        :param request: A request for one saved plan.
        :param identifier: The plan's identity.
        :return: The plan with its identity.
        """
        record = self.plans.load(identifier)
        return Response.of_json(
            replace(record, document=self.translated(record.document))
        )

    def answer_save(self, request: Request) -> Response:
        """
        Check a plan against the limits, then save it; a saved plan may still lack
        parameters a demo needs.

        :param request: A request whose body is a :class:`PlanRecord`.
        :return: The saved plan with its identity.
        """
        record = PlanRecord.from_json(self.body(request))
        record = replace(record, document=self.translated(record.document))
        PlanCheck(self.limits).check(record.document)
        return Response.of_json(self.plans.save(record))

    # %% constraints

    def answer_constraint(self, request: Request) -> Response:
        """
        :param request: A request whose body is a :class:`ConstraintText`.
        :return: The compiled constraint.
        """
        text = ConstraintText.from_json(self.body(request))
        PlanCheck(self.limits).text(PlanBuilderRoute.CONSTRAINT, text.text)
        return Response.of_json(
            self.compiler.compile(text, self.action_schema(text.step_action))
        )

    def action_schema(self, name: Optional[str]) -> Optional[ActionSchema]:
        """
        :param name: An action's class name, or None.
        :return: The schema of the workspace's action of that name, or None.
        """
        for schema in self.workspace.workspace().action_catalog().actions:
            if schema.name == name:
                return schema
        return None

    # %% demos

    def translated(self, plan: PlanDocument) -> PlanDocument:
        """
        :param plan: A plan as it was saved or sent.
        :return: The plan naming actions and robots by the catalog's identifiers; a plan
            of the planner's class names is translated through the names the catalog
            lists them as having been saved under, and a step of a profile's skill names
            the action the profile maps the skill to now.
        """
        return self.profile.resolved(
            SavedNames.of_catalog(self.catalog()).translated(plan)
        )

    def runnable(self, plan: PlanDocument) -> DemoDocument:
        """
        :param plan: A plan as a browser sent it.
        :return: What the plan's demo runs, once the plan is complete and uses only
            what the catalog offers; the catalog lists only environments the viewer
            holds, so the environment's directory is found.
        """
        plan = self.translated(plan)
        check = PlanCheck(self.limits)
        check.check(plan)
        check.complete(plan)
        CatalogCheck(self.catalog(), self.limits).check(plan)
        return self.demo_document(plan)

    def demo_document(self, plan: PlanDocument) -> DemoDocument:
        """
        :param plan: A checked plan.
        :return: The plan with its environment's directory, found among the viewer's
            scenes, and the robot profile's zones and safety limits.
        """
        profile = self.profile.profile
        zones = [] if profile is None else list(profile.zones)
        safety = None if profile is None else profile.safety
        perception = PerceptionSettings() if profile is None else profile.perception
        if plan.environment is None:
            return DemoDocument(
                plan=plan, zones=zones, safety=safety, perception=perception
            )
        directory = self.scenes.directory_of(plan.environment).path / plan.environment
        return DemoDocument(
            plan=plan,
            environment_directory=str(directory.resolve()),
            zones=zones,
            safety=safety,
            perception=perception,
        )

    def answer_demo(self, request: Request) -> Response:
        """
        :param request: A request whose body is a :class:`PlanDocument`.
        :return: The demo generated from the plan, not written.
        """
        demo = self.runnable(PlanDocument.from_json(self.body(request)))
        return Response.of_json(self.template.render(demo))

    def answer_write(self, request: Request) -> Response:
        """
        :param request: A request whose body is a :class:`PlanDocument`.
        :return: Where the plan's demo was written and how to run it.
        """
        demo = self.runnable(PlanDocument.from_json(self.body(request)))
        return Response.of_json(self.demos.write(self.template.render(demo)))

    # %% runs

    def answer_run(self, request: Request) -> Response:
        """
        :param request: A request whose body is a :class:`RunRequest`.
        :return: The state of the started demo.
        :raises PlanNotValidated: When the mode needs a validated plan, the plan is not
            the one whose validation passed last and the request does not override it.
            No request overrides the gate of a run on the real robot. A run on the
            real robot moves it, so the next run needs a new validation, unless the
            robot's check refused the run before anything moved.
        """
        run = RunRequest.from_json(self.body(request))
        demo = self.runnable(run.plan)
        real = self.profile.moves_real_robot(run.mode)
        if (
            run.mode.needs_validation
            and not self.gate.admits(demo.plan)
            and not (run.override_validation and not real)
        ):
            raise PlanNotValidated(run.mode.value, real=real)
        executor = self.profile.executor_for(run.mode)
        status = self.runner.start(demo, run.mode, executor)
        if executor is ExecutorKind.REAL:
            self.gate.close(kept_if=self.runner.current.refused_before_motion)
        return Response.of_json(self.with_validation_held(status))

    def answer_run_status(self, request: Request) -> Response:
        """
        :param request: A request for the demo's state.
        :return: The state of the demo started last.
        """
        return Response.of_json(self.with_validation_held(self.runner.status()))

    def with_validation_held(self, status: RunStatus) -> RunStatus:
        """
        :param status: The state of the demo started last.
        :return: The state, saying whether the gate holds a passed validation, which
            the page compares with its own, as a restart of the plan builder empties
            the gate.
        """
        return replace(status, validation_held=self.gate.holds_validation())

    def answer_posture(self, request: Request) -> Response:
        """
        :param request: A request for the real robot's posture.
        :return: The joint positions its drivers reported last and how long ago each
            arm's did; none while the builder is not attached to the real robot.
        """
        return Response.of_json(self.mirror.posture())

    def answer_stop(self, request: Request) -> Response:
        """
        :param request: A request to stop the demo.
        :return: The state of the stopped demo.
        """
        return Response.of_json(self.with_validation_held(self.runner.stop()))

    def answer_acknowledge(self, request: Request) -> Response:
        """
        :param request: A request acknowledging the safety stop of the demo.
        :return: The state of the demo started last, its stop acknowledged.
        """
        return Response.of_json(self.with_validation_held(self.runner.acknowledge()))

    # %% scans

    def answer_scan(self, request: Request) -> Response:
        """
        Scan the scene the plan's robot sees: the simulated scene the profile names, or
        what the robot's own perception sees. The plan's steps are not checked, since a
        scan finds the objects they may still lack.

        :param request: A request whose body is a :class:`ScanRequest`.
        :return: The :class:`SceneScan`, the plan's objects after the scan.
        """
        scan = ScanRequest.from_json(self.body(request))
        plan = self.translated(scan.plan)
        PlanCheck(self.limits).check(plan)
        CatalogCheck(self.catalog(), self.limits).check_scene(plan)
        return Response.of_json(
            self.scans.scan(
                SceneScanOrder(
                    demo=self.demo_document(plan),
                    region=scan.region,
                    simulated_objects=self.profile.simulated_objects,
                )
            )
        )

    # %% validation

    def answer_validate(self, request: Request) -> Response:
        """
        Validate a plan the plan builder checked: a step naming a skill the robot
        profile does not offer fails it, else every step is tried in a copy of the twin.
        The plan may run where a run needs a validated plan once it passed.

        :param request: A request whose body is a :class:`ValidationRequest`.
        :return: The :class:`ValidationReport`.
        :raises SafetyStopNotAcknowledged: While nobody acknowledged the safety stop of
            the demo started last.
        :raises RunRecordSaving: While the demo started last saves its run's record.
        :raises PlanRunning: While a demo performs a plan, or when one started while
            the plan was validated; the gate stays as it was.
        """
        before = self.runner.refuse_to_validate()
        demo = self.runnable(ValidationRequest.from_json(self.body(request)).plan)
        report = self.report_unoffered_skills(demo.plan) or self.validations.validate(
            self.profile.validation_order(demo)
        )
        self.runner.refuse_validated(before)
        self.gate.record(demo.plan, report)
        return Response.of_json(report)

    def report_unoffered_skills(self, plan: PlanDocument) -> Optional[ValidationReport]:
        """
        :param plan: A checked plan.
        :return: The report failing every step of the performing robot that names a
            skill the robot profile does not offer, the others unchecked; None when
            every step's skill is offered.
        """
        unoffered = {
            step.identifier for step in self.profile.find_unoffered_steps(plan)
        }
        if not unoffered:
            return None
        return ValidationReport(
            steps=[
                (
                    StepValidation(
                        step=step.identifier,
                        verdict=StepVerdict.FAILED,
                        reason=StepFailureReason.SKILL_NOT_OFFERED,
                    )
                    if step.identifier in unoffered
                    else StepValidation(
                        step=step.identifier, verdict=StepVerdict.UNCHECKED
                    )
                )
                for step in plan.performing_robot().steps
            ]
        )

    # %% reach

    def answer_reach(self, request: Request) -> Response:
        """
        :param request: A request whose body is a :class:`ReachRequest`.
        :return: The :class:`RobotReach` of the robot model.
        :raises UnsupportedByCatalog: When the catalog offers no such robot model.
        """
        reach = ReachRequest.from_json(self.body(request))
        offered = [robot.name for robot in self.workspace.workspace().robots()]
        if reach.robot not in offered:
            raise UnsupportedByCatalog(ReachRequestKey.ROBOT, reach.robot, offered)
        return Response.of_json(self.reaches.reach(reach))

    def body(self, request: Request) -> Any:
        """
        :param request: A request with a JSON body.
        :return: The decoded body, read within the document limit.
        """
        return JsonBody(request, self.limits.maximum_document_bytes).read()
