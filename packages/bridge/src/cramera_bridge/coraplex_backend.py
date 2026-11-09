"""
This package's live visualization as the world visualization of coraplex's executors.

coraplex shows a run through the :class:`~cramph.visualization.WorldVisualization` among
an executor's extensions. :class:`CrameraVisualization` is that extension for the
cramera viewer: it serves the executor's world through a :class:`LiveVisualization` and
publishes the plan the executor runs, so a demonstration handed it as its
``visualization`` is shown in the viewer.

A demo file that builds its executors itself is shown without being changed through a
registration: while it is entered, every plan executor created for the world the demo
visualizes first gets a cramera visualization serving that world, and the registration
stops the visualization once it is left, which writes its recording.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field

from typing_extensions import Callable, ClassVar, List, Optional

from coraplex.plans.executors import PlanExecutor
from cramera_bridge.visualization import LiveVisualization, PublishedPlanProgress
from cramph.executor import Executor, StatechartExecutor, TickingExecutorExtension
from cramph.visualization import WorldVisualization
from semantic_digital_twin.world import World

VisualizationFactory = Callable[..., LiveVisualization]
"""
Creates the visualization of a world, called as ``factory(world=world)``.
"""

# %% the executor extension


@dataclass
class CrameraVisualization(WorldVisualization, TickingExecutorExtension):
    """
    Shows the world of the executor it is added to in the cramera viewer and publishes
    the plan the executor runs, tick by tick.

    Given a started :attr:`live` visualization, it publishes through it and leaves it
    running after the run; otherwise it starts one from :attr:`factory` once the plan is
    compiled and stops it, which writes its recording, once the run stopped.
    """

    factory: VisualizationFactory = field(default=LiveVisualization, kw_only=True)
    """
    Creates the visualization serving the world, when none is given.
    """

    live: Optional[LiveVisualization] = field(default=None, kw_only=True)
    """
    The visualization serving the world, while it runs.
    """

    progress: Optional[PublishedPlanProgress] = field(default=None, init=False)
    """
    Publishes the plan of the executor compiled last.
    """

    @property
    def is_rendering(self) -> bool:
        return self.live is not None and self.live.publisher is not None

    def _start(self, world: World, cleanup: ExitStack) -> None:
        self.live = self.factory(world=world).start()
        cleanup.callback(self._stop_live)

    def _stop_live(self) -> None:
        """
        Stop the visualization this extension started, which writes its recording.
        """
        try:
            self.live.stop()
        finally:
            self.live = None

    def shows(self, executor: Executor) -> bool:
        """
        :param executor: An executor this extension is added to.
        :return: Whether the visualization serves the executor's world, which it does
            not for an executor trying a plan in a copy of it.
        """
        return self.is_rendering and self.live.world is executor.context.world

    def after_compile(self, executor: Executor) -> None:
        """
        Start serving the executor's world unless it is served already, and publish the
        plan it compiled.
        """
        self.start(executor.context.world)
        if not self.shows(executor) or not isinstance(executor, PlanExecutor):
            return
        self.progress = self.live.progress()
        self.progress.after_compile(executor)

    def after_recompile(self, executor: StatechartExecutor) -> None:
        """
        Publishes nothing again: the plan is read from the statechart, which keeps the
        nodes it held.
        """

    def after_tick(self, executor: StatechartExecutor) -> None:
        if self.progress is not None and self.shows(executor):
            self.progress.after_tick(executor)

    def after_run(self, executor: Executor) -> None:
        """
        Publish the state the plan left, then stop serving the world if this extension
        started serving it, which writes the recording with that state.
        """
        if self.progress is not None and self.shows(executor):
            self.progress.after_run(executor)
        super().after_run(executor)


# %% registration


@dataclass
class CoraplexBackendRegistration:
    """
    Shows every plan executor created while entered in the cramera viewer, through one
    visualization of the first world an executor is created for; leaving stops it, which
    writes its recording.
    """

    factory: VisualizationFactory = LiveVisualization
    """
    Creates the visualization of the world.
    """

    entered: ClassVar[List[CoraplexBackendRegistration]] = []
    """
    The registrations entered, innermost last.
    """

    unregistered_set_up: ClassVar[Optional[Callable[[PlanExecutor], None]]] = None
    """
    How a plan executor is set up without a registration, while one is entered.
    """

    live: Optional[LiveVisualization] = field(default=None, init=False)
    """
    The visualization serving the world, once an executor was created.
    """

    @classmethod
    def current(cls) -> Optional[CoraplexBackendRegistration]:
        """
        :return: The innermost registration entered, or None.
        """
        return cls.entered[-1] if cls.entered else None

    @classmethod
    def set_up(cls, executor: PlanExecutor) -> None:
        """
        Set a plan executor up as without a registration, then add the visualization of
        the innermost registration entered, if it shows the executor's world.

        :param executor: The plan executor being created.
        """
        cls.unregistered_set_up(executor)
        registration = cls.current()
        if registration is None:
            return
        extension = registration.extension_for(executor)
        if extension is not None:
            executor.extensions.append(extension)

    def extension_for(self, executor: PlanExecutor) -> Optional[CrameraVisualization]:
        """
        :param executor: A plan executor that was just set up.
        :return: The visualization showing it, starting the world's visualization for
            the first executor; None for an executor of another world, such as one
            trying a candidate in a copy of it.
        """
        world = executor.context.world
        if self.live is None:
            self.live = self.factory(world=world).start()
        if self.live.world is not world:
            return None
        return CrameraVisualization(live=self.live)

    def __enter__(self) -> CoraplexBackendRegistration:
        if not self.entered:
            type(self).unregistered_set_up = PlanExecutor.__post_init__
            PlanExecutor.__post_init__ = set_up_registered
        self.entered.append(self)
        return self

    def __exit__(self, *exception: object) -> None:
        self.entered.remove(self)
        if not self.entered:
            PlanExecutor.__post_init__ = type(self).unregistered_set_up
            type(self).unregistered_set_up = None
        if self.live is not None:
            self.live.stop()
            self.live = None


def set_up_registered(executor: PlanExecutor) -> None:
    """
    How a plan executor is set up while a registration is entered, see
    :meth:`CoraplexBackendRegistration.set_up`.

    :param executor: The plan executor being created.
    """
    CoraplexBackendRegistration.set_up(executor)
