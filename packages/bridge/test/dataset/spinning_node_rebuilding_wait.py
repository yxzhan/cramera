"""
A process whose block ends while the node's spinning thread, woken just before, is
still gathering what it waits on next, as when a waitable such as an action client was
destroyed right before the block ended; the process ends once the block ended.

rclpy gives the thread no hook between gathering and waiting, so the gathering is held
up where rclpy counts the gathered entities.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import rclpy.executors
from rclpy.waitable import NumberOfEntities

from cramera_bridge.ros_node import SpinningNode

# %% Holding up the spinning thread


@dataclass
class CountingHeldUpOnce:
    """
    Counts the entities rclpy's executor waits on, as :class:`NumberOfEntities` does;
    once armed, the next count is held up for :attr:`hold_seconds`.
    """

    hold_seconds: float = 0.5
    """
    How long, in seconds, the armed count is held up.
    """

    armed: threading.Event = field(default_factory=threading.Event)
    """
    Set while the next count is to be held up.
    """

    held_up: threading.Event = field(default_factory=threading.Event)
    """
    Set once a count is being held up.
    """

    def __call__(self, *counts: int) -> NumberOfEntities:
        if self.armed.is_set():
            self.armed.clear()
            self.held_up.set()
            time.sleep(self.hold_seconds)
        return NumberOfEntities(*counts)


# %% Process

if __name__ == "__main__":
    counting = CountingHeldUpOnce()
    rclpy.executors.NumberOfEntities = counting
    with SpinningNode("cramera_spinning_node_rebuilding_wait").running() as node:
        counting.armed.set()
        node.executor.wake()
        counting.held_up.wait()
