"""
The demo generated from a plan: a Python script, rendered from a template file, that
runs the plan as a robot task demo; and the output directory it is written to.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from string import Template

from cramera_plan_builder.exceptions import UnsafeStoragePath
from cramera_protocol.plan_builder import DemoDocument, DemoSource, SavedDemo

TEMPLATE = Path(__file__).parent / "templates" / "plan_demo.py"
"""
The template every generated demo is rendered from.
"""

PLAN_INDENTATION = 2
"""
The indentation of the JSON inside the demo, so it stays readable.
"""

DEMO_SUFFIX = ".py"
"""
The suffix of a generated demo's file.
"""

TEMPORARY_PREFIX = ".demo-"
"""
The prefix of a demo being written, which is hidden until it is complete.
"""

# %% template


class TemplateField(StrEnum):
    """
    The placeholders of the demo template.
    """

    NAME = "name"
    """
    The plan's name.
    """

    FILE_NAME = "file_name"
    """
    The demo's file name.
    """

    DEMO = "demo"
    """
    The JSON of the :class:`DemoDocument`, inside a raw string literal; JSON escapes
    every quote inside its strings, so the literal cannot end early.
    """


@dataclass(frozen=True)
class DemoTemplate:
    """
    Renders a plan into its demo.
    """

    path: Path = TEMPLATE
    """
    The template file.
    """

    def render(self, demo: DemoDocument) -> DemoSource:
        """
        :param demo: A checked plan, whose name gives a file name, and its environment.
        :return: The demo's file name and source.
        """
        plan = demo.plan
        file_name = f"{plan.file_name()}{DEMO_SUFFIX}"
        source = Template(self.path.read_text(encoding="utf-8")).substitute(
            {
                TemplateField.NAME: plan.name,
                TemplateField.FILE_NAME: file_name,
                TemplateField.DEMO: json.dumps(
                    demo.to_json(), indent=PLAN_INDENTATION, allow_nan=False
                ),
            }
        )
        return DemoSource(file_name=file_name, source=source)


# %% output directory


@dataclass(frozen=True)
class DemoDirectory:
    """
    The directory generated demos are written to, never a CRAM checkout's.
    """

    directory: Path
    """
    The output directory.
    """

    interpreter: str = field(default=sys.executable)
    """
    The Python interpreter that runs a demo.
    """

    def write(self, demo: DemoSource) -> SavedDemo:
        """
        Write a demo whole or not at all, replacing an earlier demo of its name.

        :param demo: A generated demo.
        :return: Where it was written and how to run it.
        :raises UnsafeStoragePath: When the directory or the demo file is a symbolic
            link.
        """
        target = self.directory / demo.file_name
        for path in (self.directory, target):
            if path.is_symlink():
                raise UnsafeStoragePath(path)
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.directory,
            prefix=TEMPORARY_PREFIX,
            suffix=DEMO_SUFFIX,
            delete=False,
        ) as temporary:
            temporary.write(demo.source)
        os.replace(temporary.name, target)
        return SavedDemo(
            path=str(target.resolve()),
            command=shlex.join([self.interpreter, str(target.resolve())]),
        )
