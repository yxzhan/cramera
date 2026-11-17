"""
The gate every thread of the viewer passes to import CRAM: Python cannot import one
package graph with cycles from two threads at once without one of them seeing a module
only partly initialised.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from types import ModuleType

from typing_extensions import ClassVar


@dataclass(frozen=True)
class CramImports:
    """
    Lets one thread of the process import CRAM, or code importing it, at a time; a
    thread holding the gate may import more through it.
    """

    lock: ClassVar[threading.RLock] = threading.RLock()
    """
    Held by the thread importing CRAM; shared by every instance, since the import
    system it guards is the process's one.
    """

    def module(self, name: str) -> ModuleType:
        """
        :param name: A module's full name.
        :return: The module, imported once no other thread imports CRAM.
        """
        with self.lock:
            return importlib.import_module(name)
