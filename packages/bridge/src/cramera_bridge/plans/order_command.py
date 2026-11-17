"""
The command line of the bridge modules the plan builder asks:
``python -m <module> <order file> <answer file>`` answers one order document with one
answer document, and reads the order only once its standard input closes, so it can be
started, and its imports done, before the order is written.
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from typing_extensions import Any, Sequence

from cramera_protocol.document import JsonDocument

# %% command


@dataclass(frozen=True)
class OrderCommand(ABC):
    """
    Answers the order a file holds and writes the answer into another file.
    """

    def main(self, arguments: Sequence[str]) -> None:
        """
        Wait until the standard input closes, then answer the order.

        :param arguments: The order's file and the file the answer is written to.
        """
        order_file, answer_file = arguments
        sys.stdin.read()
        order = json.loads(Path(order_file).read_text(encoding="utf-8"))
        Path(answer_file).write_text(
            json.dumps(self.answer(order).to_json()), encoding="utf-8"
        )

    @abstractmethod
    def answer(self, order: Any) -> JsonDocument:
        """
        :param order: The JSON of the order.
        :return: The answer to the order.
        """
