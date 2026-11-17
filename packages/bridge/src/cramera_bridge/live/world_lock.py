"""
The lock the live publisher holds while it reads or writes the world it publishes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from typing_extensions import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from semantic_digital_twin.world import World

# %% lock


class TakenWorldLocks(threading.local):
    """
    The world locks one thread took and holds, innermost last; None for an acquisition
    before a world was attached. Every thread sees its own list.
    """

    def __init__(self) -> None:
        self.locks: list[threading.RLock | None] = []


@dataclass
class PublishedWorldLock:
    """
    Held while the publisher reads or writes the world: the attached world's own lock
    first, then the publisher's. The world reports its changes while it holds its own
    lock, so a publisher taking the locks in the other order would wait for a simulation
    that waits for it. Reentrant, like both locks it takes.
    """

    world: World | None = None
    """
    The attached world, whose lock is taken first; None before a world is attached.
    """

    publisher_lock: threading.RLock = field(default_factory=threading.RLock)
    """
    Keeps the publisher's own readers and writers apart.
    """

    taken_world_locks: TakenWorldLocks = field(default_factory=TakenWorldLocks)
    """
    The world locks each thread took, innermost last, so a thread releases the lock it
    took although another world was attached in between.
    """

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """
        :param blocking: Whether to wait for the locks.
        :param timeout: How long to wait for each lock, in seconds; -1 waits as long as
            it takes.
        :return: Whether both locks are held.
        """
        world_lock = None if self.world is None else self.world.state.world_lock
        if world_lock is not None and not world_lock.acquire(blocking, timeout):
            return False
        if not self.publisher_lock.acquire(blocking, timeout):
            if world_lock is not None:
                world_lock.release()
            return False
        self.taken_world_locks.locks.append(world_lock)
        return True

    def release(self) -> None:
        """
        Release both locks the latest acquisition of this thread took.
        """
        world_lock = self.taken_world_locks.locks.pop()
        self.publisher_lock.release()
        if world_lock is not None:
            world_lock.release()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exception: object) -> None:
        self.release()
