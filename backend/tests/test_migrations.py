"""Tests for Alembic adoption.

The risk being tested is not "does the migration run" — it is "does the migration run
against a database that already has data in it". A migration that only ever gets tried
on an empty dev database will pass here and fail in production, so every test below
starts from a populated schema wherever that is the realistic case.
"""
import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.migrations import (
    BACKEND_ROOT,
    BASELINE_REVISION,
    BaselineMismatch,
    run_migrations,
)
from app.models import Base

EXECUTION_TABLES = {
    "agent_runs",
    "agent_actions",
    "approvals",
    "evidence",
    "kpi_snapshots",
    "audit_logs",
}


def _engine(tmp_path, name="m.db"):
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")


async def _tables(engine) -> set[str]:
    async with engine.begin() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


async def _revision(engine) -> str | None:
    async with engine.begin() as conn:
        rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = rows.first()
        return row[0] if row else None


async def test_fresh_database_is_built_entirely_from_migrations(tmp_path):
    engine = _engine(tmp_path, "fresh.db")
    try:
        await run_migrations(engine)

        tables = await _tables(engine)
        assert "users" in tables
        assert EXECUTION_TABLES <= tables
        assert await _revision(engine) == "0002_execution_spine"
    finally:
        await engine.dispose()


async def _build_pre_alembic_db(engine) -> None:
    """Recreate what a running installation looks like today: the baseline schema,
    real rows, and no ``alembic_version`` table."""

    def _stamp_baseline_only(sync_conn):
        cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        cfg.attributes["connection"] = sync_conn
        command.upgrade(cfg, BASELINE_REVISION)

    async with engine.begin() as conn:
        await conn.run_sync(_stamp_baseline_only)
        # Drop the marker so the database looks like one that never used Alembic.
        await conn.execute(text("DROP TABLE alembic_version"))
        await conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, role, is_active, "
                "created_at, updated_at) VALUES "
                "(1, 'dapo@atlas.example.com', 'x', 'trader', 1, "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO strategies (id, title, horizon, pillars, status, "
                "created_at, updated_at) VALUES "
                "(1, 'Brazil->Nigeria sugar', 'quarter', '{}', 'active', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO strategy_tasks (id, strategy_id, pillar, title, cadence, "
                "priority, status, source, created_at, updated_at) VALUES "
                "(1, 1, 'demand', 'Follow up with Dangote', 'weekly', 'high', 'todo', "
                "'auto', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )


async def test_existing_database_upgrades_without_losing_data(tmp_path):
    """The production path: tables and rows already exist, no migration history."""
    engine = _engine(tmp_path, "existing.db")
    try:
        await _build_pre_alembic_db(engine)
        assert "alembic_version" not in await _tables(engine)

        await run_migrations(engine)

        assert await _revision(engine) == "0002_execution_spine"
        assert EXECUTION_TABLES <= await _tables(engine)

        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT title, status FROM strategy_tasks WHERE id = 1")
                )
            ).all()
        assert rows == [("Follow up with Dangote", "todo")]
    finally:
        await engine.dispose()


async def test_existing_rows_get_behaviour_preserving_defaults(tmp_path):
    """A task written before the execution spine must read back as it behaved before:
    ungated, human-owned, root-level. This is what makes the upgrade invisible."""
    engine = _engine(tmp_path, "defaults.db")
    try:
        await _build_pre_alembic_db(engine)
        await run_migrations(engine)

        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT kind, position, depends_on_ids, requires_evidence, "
                        "assignee, parent_id FROM strategy_tasks WHERE id = 1"
                    )
                )
            ).one()
        kind, position, depends, requires_evidence, assignee, parent_id = row
        assert kind == "task"
        assert position == 0
        assert depends == "[]"
        assert not requires_evidence
        assert assignee == "human"
        assert parent_id is None
    finally:
        await engine.dispose()


async def test_upgrade_is_idempotent(tmp_path):
    """Startup runs migrations every boot, so a second run must be a no-op."""
    engine = _engine(tmp_path, "twice.db")
    try:
        await run_migrations(engine)
        first = await _tables(engine)
        await run_migrations(engine)
        assert await _tables(engine) == first
        assert await _revision(engine) == "0002_execution_spine"
    finally:
        await engine.dispose()


async def test_stale_database_is_refused_rather_than_silently_stamped(tmp_path):
    """If a database predates the baseline it must not be stamped as if it matched.

    Stamping would mark columns as present that are not, and the failure would surface
    later as a confusing query error rather than here as a clear one.
    """
    engine = _engine(tmp_path, "stale.db")
    try:
        await _build_pre_alembic_db(engine)
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users DROP COLUMN company_name"))

        with pytest.raises(BaselineMismatch) as exc:
            await run_migrations(engine)
        assert "users.company_name" in str(exc.value)
    finally:
        await engine.dispose()


async def test_migrations_match_the_models(tmp_path):
    """Guards against the classic drift: a model changed, no migration written.

    Autogenerate against a fully migrated database should detect nothing to do.
    """
    engine = _engine(tmp_path, "drift.db")
    try:
        await run_migrations(engine)

        def _diff(sync_conn):
            context = MigrationContext.configure(sync_conn)
            return compare_metadata(context, Base.metadata)

        async with engine.begin() as conn:
            diff = await conn.run_sync(_diff)

        assert diff == [], f"Models and migrations have drifted: {diff}"
    finally:
        await engine.dispose()
