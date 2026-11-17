"""
Storing the plans of a run with CRAM's database mapping.
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, event, inspect

from cramera_bridge.database.native_plans import GeneratedMapping, NativePlans
from krrood.ormatic.utils import create_engine


def schema_of(*names: str) -> MetaData:
    """
    :param names: The names of the schema's tables.
    :return: A schema of tables holding only an identifier each.
    """
    schema = MetaData()
    for name in names:
        Table(name, schema, Column("identifier", Integer, primary_key=True))
    return schema


def test_the_tables_of_every_schema_are_created_in_one_transaction(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'plans.sqlite'}")
    statements = []
    event.listen(
        engine,
        "connect",
        lambda connection, record: connection.set_trace_callback(
            lambda statement: statements.append(statement.split()[0].upper())
        ),
    )

    NativePlans.create_tables(engine, [schema_of("run"), schema_of("plan", "step")])

    assert sorted(inspect(engine).get_table_names()) == ["plan", "run", "step"]
    assert [
        statement
        for statement in statements
        if statement in ("BEGIN", "CREATE", "COMMIT")
    ] == ["BEGIN", "CREATE", "CREATE", "CREATE", "COMMIT"]


def test_tables_the_database_holds_already_are_kept(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'plans.sqlite'}")
    NativePlans.create_tables(engine, [schema_of("run")])

    NativePlans.create_tables(engine, [schema_of("run"), schema_of("plan")])

    assert sorted(inspect(engine).get_table_names()) == ["plan", "run"]


# %% preparing


@pytest.mark.skipif(
    not NativePlans.mapping_is_built(), reason="CRAM's mapping of plans is not built"
)
def test_preparing_imports_and_configures_the_mapping_of_plans():
    NativePlans.prepare()

    mapping = sys.modules[GeneratedMapping.PLANS]
    assert all(mapper.configured for mapper in mapping.Base.registry.mappers)
