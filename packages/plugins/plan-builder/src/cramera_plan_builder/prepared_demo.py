"""
A demo's interpreter started ahead of its run: it runs what prepares every demo, such
as importing the CRAM workspace, then waits for the one demo it is handed on its input
and runs it as ``python <demo> <arguments>`` would; its input stays the demo's lifeline.

``python -m cramera_plan_builder.prepared_demo [--prepare module:function]...``
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import runpy
import sys
from argparse import ArgumentParser
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from typing_extensions import Callable, List, Optional, Sequence

READ_SIZE = 1
"""
How many bytes of the input are read at once while the order is read, so nothing after
its line is taken from the demo's lifeline.
"""

ORDER_END = b"\n"
"""
What ends the order on the input.
"""

ORDER_ENCODING = "utf-8"
"""
The encoding of the order.
"""


class PreparedDemoOption(StrEnum):
    """
    The options of a prepared demo's interpreter.
    """

    PREPARE = "--prepare"
    """
    A function, as ``module:attribute.path``, called before the order is read.
    """


class PreparationSeparator(StrEnum):
    """
    What separates the parts of a preparation's name.
    """

    MODULE = ":"
    """
    Between the module and the function in it.
    """

    ATTRIBUTE = "."
    """
    Between the attributes leading to the function.
    """


# %% order


@dataclass(frozen=True)
class DemoOrder:
    """
    The demo a prepared interpreter is handed, one JSON array on one line: the demo
    file, then its arguments.
    """

    command: List[str]
    """
    The demo file, then its arguments, as ``python`` would be given them.
    """

    def to_line(self) -> bytes:
        """
        :return: The order as written to the interpreter's input.
        """
        return json.dumps(self.command).encode(ORDER_ENCODING) + ORDER_END

    @classmethod
    def from_input(cls, descriptor: int) -> Optional[DemoOrder]:
        """
        :param descriptor: The interpreter's input.
        :return: The order once its line was read; None when the input ended before.
        """
        line = bytearray()
        while not line.endswith(ORDER_END):
            chunk = os.read(descriptor, READ_SIZE)
            if not chunk:
                return None
            line.extend(chunk)
        return cls(command=json.loads(line.decode(ORDER_ENCODING)))

    @property
    def demo(self) -> Path:
        """
        The demo file.
        """
        return Path(self.command[0]).resolve()


# %% interpreter


@dataclass(frozen=True)
class PreparedInterpreter:
    """
    Prepares, waits for its order and runs the ordered demo.
    """

    preparations: List[Callable[[], object]] = field(default_factory=list)
    """
    What runs before the order is read, in order.
    """

    @classmethod
    def from_arguments(cls, arguments: Sequence[str]) -> PreparedInterpreter:
        """
        :param arguments: The interpreter's arguments.
        :return: The interpreter calling the preparations they name.
        """
        parser = ArgumentParser(description=__doc__)
        parser.add_argument(
            PreparedDemoOption.PREPARE,
            action="append",
            default=[],
            help="a function module:attribute.path called before the order is read",
        )
        options = parser.parse_args(list(arguments))
        return cls(preparations=[cls.preparation(name) for name in options.prepare])

    @staticmethod
    def preparation(name: str) -> Callable[[], object]:
        """
        :param name: A function as ``module:attribute.path``.
        :return: The function, its module imported.
        """
        module, _, path = name.partition(PreparationSeparator.MODULE)
        found = importlib.import_module(module)
        for attribute in path.split(PreparationSeparator.ATTRIBUTE):
            found = getattr(found, attribute)
        return found

    def main(self) -> None:
        """
        Prepare, then run the demo ordered on the input; end without running anything
        when the input ends first. A demo that ran to its end leaves the interpreter
        without tearing its modules down, which takes seconds after a run.
        """
        for preparation in self.preparations:
            preparation()
        order = DemoOrder.from_input(sys.stdin.fileno())
        if order is None:
            return
        os.chdir(order.demo.parent)
        sys.argv = list(order.command)
        sys.path[0] = str(order.demo.parent)
        runpy.run_path(str(order.demo), run_name="__main__")
        sys.stdout.flush()
        sys.stderr.flush()
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    PreparedInterpreter.from_arguments(sys.argv[1:]).main()
