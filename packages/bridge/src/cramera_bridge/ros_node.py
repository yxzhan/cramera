"""
A ROS 2 node spun on a thread of its own, so that a run's plans, its perception and its
checks can wait for answers while the node keeps receiving them.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from threading import Thread

import rclpy
from rclpy.executors import Executor, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.task import Future
from typing_extensions import Iterator

# %% spinning node


@dataclass(frozen=True)
class SpinningNode:
    """
    A node that is spun while a block runs, ROS initialized for it unless it already
    was.
    """

    name: str
    """
    The node's name.
    """

    executor_type: type[Executor] = SingleThreadedExecutor
    """
    Spins the node; a multi-threaded one lets a callback wait for another.
    """

    @contextmanager
    def running(self) -> Iterator[Node]:
        """
        Create and spin the node while the block runs, then destroy it, and shut ROS
        down again if it was initialized for the node.

        :return: The node.
        """
        with ExitStack() as spinning:
            if not rclpy.ok():
                rclpy.init()
                spinning.callback(rclpy.try_shutdown)
            node = rclpy.create_node(self.name)
            spinning.callback(node.destroy_node)
            executor = self.executor_type()
            executor.add_node(node)
            spinning.callback(executor.shutdown)
            # The spinning thread ends before ROS shuts down, or exiting aborts. It is
            # ended through a future, which wakes it: shutting the executor down while it
            # spins would destroy its wake-up before it waits, so it waited forever.
            ended = Future()
            spinner = Thread(
                target=executor.spin_until_future_complete,
                args=(ended,),
                daemon=True,
                name=self.name,
            )
            spinner.start()
            spinning.callback(spinner.join)
            spinning.callback(ended.set_result, None)
            yield node
