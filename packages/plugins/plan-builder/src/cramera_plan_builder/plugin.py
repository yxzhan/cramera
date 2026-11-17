"""
The plan builder as a viewer plugin: where it keeps its files, where generated demos go
and run, and the routes and browser files it adds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path

from typing_extensions import List, Mapping, Optional

from cramera.locations import DataLocations
from cramera.plugins import (
    NavigationEntry,
    PageEntry,
    PluginContext,
    RouteDeclaration,
    ViewerPlugin,
)
from cramera_plan_builder.constraints import ConstraintCompiler
from cramera_plan_builder.demos import DemoDirectory, DemoTemplate
from cramera_plan_builder.plans import PlanStore
from cramera_plan_builder.profiles import ConfiguredProfile
from cramera_plan_builder.environments import EnvironmentAnnotationCache
from cramera_plan_builder.mirroring import PostureMirror
from cramera_plan_builder.routes import PlanBuilderRoutes
from cramera_plan_builder.runs import DemoRunner
from cramera_plan_builder.reaches import RobotReaches
from cramera_plan_builder.scans import SceneScans
from cramera_plan_builder.validations import PlanValidations
from cramera_plan_builder.workspace import RobotModelFiles, WorkspaceLoader
from cramera_protocol.live import LIVE_HOST, LIVE_PORT
from cramera_protocol.plan_builder import (
    PlanBuilderFile,
    PlanBuilderLimits,
    PlanBuilderName,
    PlanBuilderVariable,
)
from cramera_protocol.robot_profile import RobotConnection
from cramera_protocol.viewer import NavigationGroup

WEB_DIRECTORY = Path(__file__).parent / "web"
"""
The plan builder's browser files.
"""

LIVE_SCHEME = "http"
"""
The scheme of the live publisher's URL.
"""

NAVIGATION_TITLE = "Plan Builder"
"""
The text of the plugin's entry in the viewer's navigation.
"""

# %% storage


class StorageDirectory(StrEnum):
    """
    The directories the plan builder writes, below cramera's data directory.
    """

    BUILDER = "plan-builder"
    """
    The plan builder's own directory.
    """

    PLANS = "plans"
    """
    The saved plans, below :attr:`BUILDER`.
    """

    DEMOS = "demos"
    """
    The generated demos, below :attr:`BUILDER`, unless the environment names another
    directory.
    """

    ROBOTS = "robots"
    """
    The robots' model descriptions the 3D view loads, below :attr:`BUILDER`.
    """


class Distribution(StrEnum):
    """
    The package the plan builder is installed from.
    """

    NAME = "cramera-plan-builder"
    """
    The distribution's name, under which its version is recorded.
    """


@dataclass(frozen=True)
class PlanBuilderStorage:
    """
    Where the plan builder keeps plans and writes demos, apart from every CRAM checkout.
    """

    directory: Path
    """
    The plan builder's directory.
    """

    demos: Path
    """
    The directory generated demos are written to.
    """

    @classmethod
    def of(
        cls, locations: DataLocations, environment: Mapping[str, str]
    ) -> PlanBuilderStorage:
        """
        :param locations: Where cramera keeps its data.
        :param environment: The process environment, which may name the demo directory.
        :return: The storage below cramera's data directory.
        """
        directory = locations.data / StorageDirectory.BUILDER
        configured = environment.get(PlanBuilderVariable.OUTPUT)
        return cls(
            directory=directory,
            demos=(
                Path(configured).expanduser()
                if configured
                else directory / StorageDirectory.DEMOS
            ),
        )

    @property
    def plans(self) -> Path:
        """
        The directory of the saved plans.
        """
        return self.directory / StorageDirectory.PLANS

    @property
    def robots(self) -> Path:
        """
        The directory of the robots' model descriptions.
        """
        return self.directory / StorageDirectory.ROBOTS


# %% plugin


@dataclass
class PlanBuilderPlugin(ViewerPlugin):
    """
    Composes robot plans and runs the demos generated from them.
    """

    environment: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    """
    The process environment, which may name the demo directory and port.
    """

    limits: PlanBuilderLimits = field(default_factory=PlanBuilderLimits)
    """
    The bounds plans and demos stay within.
    """

    workspace: WorkspaceLoader = field(default_factory=WorkspaceLoader)
    """
    Reads what the CRAM workspace offers.
    """

    template: DemoTemplate = field(default_factory=DemoTemplate)
    """
    Renders a plan into its demo.
    """

    scans: SceneScans = field(default_factory=SceneScans)
    """
    Scans the scene for a plan's objects.
    """

    validations: PlanValidations = field(default_factory=PlanValidations)
    """
    Validates plans.
    """

    reaches: RobotReaches = field(default_factory=RobotReaches)
    """
    Measures what robot models reach.
    """

    mirror: PostureMirror = field(default_factory=PostureMirror)
    """
    Mirrors where the real robot stands, while the builder is attached to it.
    """

    def name(self) -> str:
        return PlanBuilderName.PLUGIN

    def version(self) -> str:
        return version(Distribution.NAME)

    def port(self) -> int:
        """
        :return: The port started demos publish on: the configured one, else the live
            publisher's.
        """
        return int(self.environment.get(PlanBuilderVariable.PORT, LIVE_PORT))

    def routes(self, context: PluginContext) -> List[RouteDeclaration]:
        """
        :param context: What the viewer offers the plugin.
        :return: The plan builder's routes.
        """
        storage = PlanBuilderStorage.of(context.locations, self.environment)
        profile = ConfiguredProfile.of_environment(self.environment)
        demos = DemoDirectory(storage.demos)
        port = self.port()
        if profile.connection is RobotConnection.REAL:
            self.mirror.start()
        runner = DemoRunner(
            directory=demos,
            port=port,
            log_limit=self.limits.maximum_log_bytes,
            template=self.template,
            prepared_program=DemoRunner.prepared_program_command(demos.interpreter),
        )
        return PlanBuilderRoutes(
            scenes=context.catalog,
            local_scenes=context.locations.local_scenes,
            workspace=self.workspace,
            annotations=EnvironmentAnnotationCache(self.workspace),
            robot_files=RobotModelFiles(self.workspace, storage.robots),
            plans=PlanStore(storage.plans),
            compiler=ConstraintCompiler.of_file(camera_mount=profile.camera_mount()),
            demos=demos,
            runner=runner,
            limits=self.limits,
            live_url=f"{LIVE_SCHEME}://{LIVE_HOST}:{port}",
            template=self.template,
            profile=profile,
            scans=self.scans,
            validations=self.validations,
            reaches=self.reaches,
            mirror=self.mirror,
        ).declarations()

    def start(self) -> None:
        """
        Read the CRAM workspace in the background, so the first catalog request does not
        wait for CRAM's import.
        """
        self.workspace.preload()

    def close(self) -> None:
        """
        End the posture mirror.
        """
        self.mirror.close()

    def web_directory(self) -> Optional[Path]:
        return WEB_DIRECTORY

    def module(self) -> Optional[str]:
        return PlanBuilderFile.MODULE

    def navigation(self) -> Optional[NavigationEntry]:
        return PageEntry(
            NAVIGATION_TITLE, NavigationGroup.BUILDER, PlanBuilderFile.PAGE.value
        )
