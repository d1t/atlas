import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai import get_llm
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.models import (  # noqa: F401  -- ensure mappers registered
    Activity,
    Deal,
    Document,
    Supplier,
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


async def _ensure_user_columns(conn) -> None:
    """Lightweight dev-only schema drift handler.

    ``Base.metadata.create_all`` only creates missing tables, not missing
    columns on existing tables. When new optional profile fields are added to
    ``User``, an existing SQLite dev DB would silently be out-of-date and
    every insert would fail. For production we have Alembic; for local dev
    we do best-effort ``ALTER TABLE ADD COLUMN`` so users don't have to wipe
    their DB on each pull.
    """
    from sqlalchemy import text

    dialect = conn.dialect.name
    if dialect not in ("sqlite", "postgresql"):
        return
    if dialect == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(users)"))
        existing = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users'"
            )
        )
        existing = {row[0] for row in result.fetchall()}

    for col, ddl in _USER_NEW_COLUMNS.items():
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
            logger.info("Added missing column users.%s", col)


@app.on_event("startup")
async def on_startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_user_columns(conn)
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
