"""
Rendering a plan into its demo and writing the demo into the output directory.
"""

from __future__ import annotations

import shlex

import pytest

from cramera_plan_builder.demos import (
    DEMO_SUFFIX,
    TEMPLATE,
    DemoDirectory,
    DemoTemplate,
    TemplateField,
)
from cramera_plan_builder.exceptions import UnsafeStoragePath
from cramera_protocol.plan_builder import DemoDocument, DemoSource

from .dataset.demo_source import demo_document


@pytest.mark.parametrize("file_name", ["milk_to_table.json", "tricky_texts.json"])
def test_the_demo_carries_the_plan_unchanged(read_plan, file_name):
    demo = DemoDocument(plan=read_plan(file_name), environment_directory="/scenes/lab")
    rendered = DemoTemplate().render(demo)
    compile(rendered.source, rendered.file_name, "exec")
    assert demo_document(rendered.source) == demo


def test_the_demo_is_named_after_the_plan(plan):
    rendered = DemoTemplate().render(DemoDocument(plan=plan))
    assert rendered.file_name == f"{plan.name}{DEMO_SUFFIX}"
    assert rendered.file_name in rendered.source


def test_a_demo_of_a_plan_named_with_spaces_has_them_as_underscores(plan):
    plan.name = "milk to  table"
    rendered = DemoTemplate().render(DemoDocument(plan=plan))
    assert rendered.file_name == f"milk_to__table{DEMO_SUFFIX}"
    compile(rendered.source, rendered.file_name, "exec")
    assert demo_document(rendered.source).plan.name == "milk to  table"


def test_the_template_is_python_of_its_own(tmp_path):
    source = TEMPLATE.read_text(encoding="utf-8")
    compile(source, TEMPLATE.name, "exec")
    for placeholder in TemplateField:
        assert f"${{{placeholder}}}" in source


def test_a_written_demo_is_found_where_its_command_runs_it(tmp_path, plan):
    rendered = DemoTemplate().render(DemoDocument(plan=plan))
    saved = DemoDirectory(tmp_path / "demos", interpreter="python3").write(rendered)
    written = tmp_path / "demos" / rendered.file_name
    assert written.read_text(encoding="utf-8") == rendered.source
    assert saved.path == str(written.resolve())
    assert shlex.split(saved.command) == ["python3", str(written.resolve())]


def test_writing_a_demo_again_replaces_it(tmp_path):
    directory = DemoDirectory(tmp_path)
    directory.write(DemoSource(file_name="plan.py", source="old = 1\n"))
    directory.write(DemoSource(file_name="plan.py", source="new = 2\n"))
    assert [path.name for path in tmp_path.iterdir()] == ["plan.py"]
    assert (tmp_path / "plan.py").read_text(encoding="utf-8") == "new = 2\n"


def test_a_demo_file_that_is_a_link_is_not_written(tmp_path):
    (tmp_path / "elsewhere.py").write_text("kept = 1\n", encoding="utf-8")
    (tmp_path / "plan.py").symlink_to(tmp_path / "elsewhere.py")
    with pytest.raises(UnsafeStoragePath):
        DemoDirectory(tmp_path).write(DemoSource(file_name="plan.py", source="x = 1\n"))
    assert (tmp_path / "elsewhere.py").read_text(encoding="utf-8") == "kept = 1\n"
