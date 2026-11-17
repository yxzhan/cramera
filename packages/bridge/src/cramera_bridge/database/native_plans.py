"""
The plans a run performed, stored in the bundle's plan database by CRAM's own database
mapping, with every parameter of every step as CRAM holds it.

CRAM generates its mapping into ``ormatic_interface`` modules, which a workspace builds
with ``scripts/regenerate_all_orm.py``; where they have not been built, the run keeps
what :mod:`cramera_bridge.database.runs` stores of it.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import Session, configure_mappers
from typing_extensions import List

from krrood.ormatic.data_access_objects.helper import to_dao
from krrood.ormatic.utils import create_engine

from cramera_bridge.database.runs import RecordedPlan, RunTable, StoredRun
from cramera_bridge.live.plan import PerformedPlan

SQLITE_BEGIN = "BEGIN"
"""
The statement beginning a transaction in SQLite explicitly.
"""

# %% CRAM's mapping


class GeneratedMapping(StrEnum):
    """
    The modules CRAM generates its database mapping into.
    """

    PLANS = "coraplex.orm.ormatic_interface"
    """
    The mapping of coraplex's plans, which includes the statecharts they run as and
    everything they refer to.
    """


# %% storing


@dataclass(frozen=True)
class NativePlans:
    """
    The plans of one run, to be stored in a bundle's plan database.
    """

    run: StoredRun
    """
    The run, kept in the bundle's plan database.
    """

    @staticmethod
    def mapping_is_built() -> bool:
        """
        :return: Whether CRAM's mapping of plans has been generated in this workspace.
        """
        return importlib.util.find_spec(GeneratedMapping.PLANS) is not None

    @classmethod
    def prepare(cls) -> None:
        """
        Import CRAM's mapping of plans and configure it, so storing a run's plans later
        only writes them; without a generated mapping nothing is prepared.
        """
        if not cls.mapping_is_built():
            return
        importlib.import_module(GeneratedMapping.PLANS)
        configure_mappers()

    @staticmethod
    def create_tables(engine: Engine, schemas: List[MetaData]) -> None:
        """
        Create the tables of every schema the database lacks, in one transaction.

        SQLite's Python driver commits each ``CREATE`` on its own unless a transaction
        was begun explicitly; CRAM's mapping has some two thousand tables, which then
        take about half a minute to create instead of a fraction of a second.

        :param engine: The engine of the plan database.
        :param schemas: The schemas whose tables are created.
        """
        with engine.connect() as connection:
            connection.exec_driver_sql(SQLITE_BEGIN)
            for schema in schemas:
                schema.create_all(connection)
            connection.commit()

    def store(self, plans: List[PerformedPlan]) -> None:
        """
        Store each plan with CRAM's mapping and link it to the run; without a generated
        mapping nothing is stored and a warning names how to build it.

        The database is opened through krrood's engine, whose JSON serializer writes the
        values CRAM's mapping keeps as JSON.

        :param plans: The plans the run performed, in the order each began.
        """
        if not self.mapping_is_built():
            logging.getLogger(__name__).warning(
                "CRAM's database mapping %s is not built, so the run's plans are kept "
                "only as recorded steps; build it with scripts/regenerate_all_orm.py "
                "in the CRAM workspace.",
                GeneratedMapping.PLANS.value,
            )
            return
        mapping = importlib.import_module(GeneratedMapping.PLANS)
        engine = create_engine(f"sqlite:///{self.run.database.path}")
        self.create_tables(engine, [RunTable.metadata, mapping.Base.metadata])
        with Session(engine) as session, session.begin():
            for performed in plans:
                stored = to_dao(performed.statechart)
                session.add(stored)
                session.flush()
                session.add(
                    RecordedPlan(
                        run_identifier=self.run.identifier,
                        number=performed.number,
                        robot=performed.robot,
                        stored_plan=stored.database_id,
                    )
                )


@dataclass(frozen=True)
class PendingPlans:
    """
    The plans a saved run performed, not yet stored in the bundle's plan database:
    storing them takes long, so a run on a robot stores them once it released the robot.
    """

    run: StoredRun
    """
    The saved run the plans belong to.
    """

    plans: List[PerformedPlan]
    """
    The plans the run performed, in the order each began.
    """

    def store(self) -> None:
        """
        Store the plans with the run, as :meth:`NativePlans.store` does.
        """
        NativePlans(self.run).store(self.plans)
