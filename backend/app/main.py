import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai import get_llm
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.models import (  # noqa: F401  -- ensure mappers registered
    Activity,
    BuyerLead,
    Deal,
    Document,
    Opportunity,
    Supplier,
    SupplierLead,
    Task,
    User,
)
from app.models.base import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("atlas")

settings = get_settings()

app = FastAPI(
    title="Atlas Trade OS",
    description="AI-Native Commodity Trading Operating System",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_USER_NEW_COLUMNS: dict[str, str] = {
    "company_name": "VARCHAR(255)",
    "title": "VARCHAR(128)",
    "phone": "VARCHAR(64)",
}

# V2 opportunity layer: new FKs on the ``deals`` table that link a Deal back to
# its originating Opportunity + chosen SupplierLead + BuyerLead. Nullable so
# pre-V2 deals stay valid.
_DEAL_NEW_COLUMNS: dict[str, str] = {
    "opportunity_id": "INTEGER",
    "supplier_lead_id": "INTEGER",
    "buyer_lead_id": "INTEGER",
}

# Negotiation-strategy columns added to supplier_leads / buyer_leads. Kept as
# nullable-with-default so pre-existing rows stay valid.
_LEAD_NEGOTIATION_COLUMNS: dict[str, str] = {
    "negotiation_stage": "INTEGER DEFAULT 1",
    "intel": "JSON DEFAULT '{}'",
    "disclosed": "JSON DEFAULT '{}'",
}


async def _ensure_columns(
    conn, table: str, new_columns: dict[str, str]
) -> None:
    """Best-effort ``ALTER TABLE ADD COLUMN`` for missing optional fields.

    ``Base.metadata.create_all`` only creates missing tables, not missing
    columns on existing tables. For production we have Alembic; for local dev
    we keep existing SQLite / Postgres DBs usable across pulls.
    """
    from sqlalchemy import text

    dialect = conn.dialect.name
    if dialect not in ("sqlite", "postgresql"):
        return
    if dialect == "sqlite":
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t"
            ),
            {"t": table},
        )
        existing = {row[0] for row in result.fetchall()}

    for col, ddl in new_columns.items():
        if col not in existing:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            )
            logger.info("Added missing column %s.%s", table, col)


async def _ensure_user_columns(conn) -> None:
    await _ensure_columns(conn, "users", _USER_NEW_COLUMNS)


async def _ensure_deal_columns(conn) -> None:
    await _ensure_columns(conn, "deals", _DEAL_NEW_COLUMNS)


async def _ensure_lead_negotiation_columns(conn) -> None:
    await _ensure_columns(conn, "supplier_leads", _LEAD_NEGOTIATION_COLUMNS)
    await _ensure_columns(conn, "buyer_leads", _LEAD_NEGOTIATION_COLUMNS)


@app.on_event("startup")
async def on_startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_user_columns(conn)
        await _ensure_deal_columns(conn)
        await _ensure_lead_negotiation_columns(conn)
    llm = get_llm()
    logger.info(
        "Atlas backend ready (env=%s, llm=%s, configured=%s)",
        settings.app_env,
        llm.provider_name,
        settings.llm_provider,
    )


@app.get("/health")
async def health() -> dict:
    llm = get_llm()
    return {
        "status": "ok",
        "llm_provider": llm.provider_name,
        "llm_configured": settings.llm_provider,
    }


app.include_router(api_router)
