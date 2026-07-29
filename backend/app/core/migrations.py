"""Schema migration entrypoint.

This project configured Alembic but never used it — ``Base.metadata.create_all`` plus
ad-hoc ``ALTER TABLE ADD COLUMN`` calls kept databases current. That works until two
changes need ordering, or a column needs backfilling, at which point it silently stops
being safe. This module adopts Alembic properly without requiring anyone to hand-migrate
an existing installation:

* **Fresh database** — no tables at all: run every migration from scratch.
* **Pre-Alembic database** — tables but no ``alembic_version``: stamp it at the baseline
  revision (which describes exactly the schema those installations already have), then
  upgrade forward. Nothing is dropped or recreated.
* **Managed database** — already stamped: upgrade to head.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

#: The revision describing the schema as it was before Alembic was adopted.
BASELINE_REVISION = "0001_baseline"

#: A table that only exists once the app has run at least once, used to tell a fresh
#: database apart from a pre-Alembic one.
SENTINEL_TABLE = "users"

#: Columns that a pre-Alembic database is expected to already have, because the old
#: ad-hoc ``_ensure_columns`` calls added them on every startup. If any are missing the
#: database predates those and stamping the baseline would skip them, so we refuse to
#: guess and tell the operator instead.
BASELINE_EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("company_name", "title", "phone"),
    "deals": ("opportunity_id", "supplier_lead_id", "buyer_lead_id"),
    "supplier_leads": ("negotiation_stage", "intel", "disclosed", "contact_name"),
}


class BaselineMismatch(RuntimeError):
    """Raised when an existing database does not match the baseline revision."""


def _alembic_config(connection: Connection) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    # env.py uses this instead of opening its own connection, so the migration runs
    # inside the transaction we already hold.
    cfg.attributes["connection"] = connection
    return cfg


def _verify_baseline(connection: Connection) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table, columns in BASELINE_EXPECTED_COLUMNS.items():
        if table not in tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        missing.extend(f"{table}.{c}" for c in columns if c not in present)
    if missing:
        raise BaselineMismatch(
            "This database predates the Alembic baseline and is missing: "
            + ", ".join(missing)
            + ". Start the previous release once so the legacy column check can add "
            "them, then restart — or add them manually before upgrading."
        )


def _upgrade(connection: Connection) -> None:
    cfg = _alembic_config(connection)
    context = MigrationContext.configure(connection)
    current = context.get_current_revision()

    if current is None:
        inspector = inspect(connection)
        if SENTINEL_TABLE in inspector.get_table_names():
            # Pre-Alembic installation: its schema already matches the baseline, so
            # record that fact rather than replaying migrations that would fail
            # against existing tables.
            _verify_baseline(connection)
            logger.info(
                "Existing database detected with no migration history; "
                "stamping %s before upgrading.",
                BASELINE_REVISION,
            )
            command.stamp(cfg, BASELINE_REVISION)
        else:
            logger.info("Fresh database detected; building schema from migrations.")

    command.upgrade(cfg, "head")


async def run_migrations(engine: AsyncEngine) -> None:
    """Bring the database schema up to head."""
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade)
    logger.info("Database schema is up to date.")
