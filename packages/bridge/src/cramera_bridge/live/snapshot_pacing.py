"""
When the world's state is worth a snapshot: only once it is settled, and not more often
than the viewer can use.

Planning code sets the world's state to check a candidate and restores it right after,
within one tick of the executor performing the plan, and the world reports both changes.
While a plan performs, a snapshot is therefore taken only after a tick, never from a
state change itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from typing_extensions import Any, Callable, Optional, Set

FRAME_INTERVAL = 1 / 30
"""
The shortest time between two snapshots, in seconds.
"""

# %% pacing


@dataclass
class SnapshotPacing:
    """
    Decides when the publisher takes a state snapshot; the publisher's world lock guards
    it.
    """

    clock: Callable[[], float] = time.monotonic
    """
    The monotonic clock snapshots are spaced with.
    """

    interval: float = FRAME_INTERVAL
    """
    The shortest time between two snapshots, in seconds.
    """

    pending: bool = field(default=False, init=False)
    """
    Whether the state changed since the last snapshot.
    """

    last_snapshot: Optional[float] = field(default=None, init=False)
    """
    When the last snapshot was taken, or None before the first.
    """

    performing: Set[int] = field(default_factory=set, init=False)
    """
    The identities of the executors performing a plan in the world.
    """

    def state_changed(self) -> bool:
        """
        Note that the world's state changed.

        :return: Whether to take a snapshot now: never while a plan performs, whose
            ticks take them, else once the interval passed.
        """
        self.pending = True
        return not self.performing and self.is_due()

    def is_due(self) -> bool:
        """
        :return: Whether the state changed since the last snapshot and the interval passed.
        """
        return self.pending and (
            self.last_snapshot is None
            or self.clock() - self.last_snapshot >= self.interval
        )

    def plan_started(self, executor: Any) -> None:
        """
        :param executor: The executor that started performing a plan.
        """
        self.performing.add(id(executor))

    def plan_ended(self, executor: Any) -> bool:
        """
        :param executor: The executor whose plan stopped performing.
        :return: Whether the state changed since the last snapshot, so the plan's last
            state is taken.
        """
        self.performing.discard(id(executor))
        return self.pending

    def is_performing(self) -> bool:
        """
        :return: Whether a plan performs in the world.
        """
        return bool(self.performing)

    def taken(self) -> None:
        """
        Note that a snapshot was just taken.
        """
        self.pending = False
        self.last_snapshot = self.clock()
