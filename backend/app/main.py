import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai import get_llm
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.migrations import run_migrations
from app.models import (  # noqa: F401  -- ensure mappers registered
    Activity,
    BuyerLead,
    Deal,
    Document,
    EmailMessage,
    Opportunity,
    Strategy,
    StrategyTask,
    Supplier,
    SupplierLead,
    Task,
    User,
)

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


@app.on_event("startup")
async def on_startup() -> None:
    await run_migrations(engine)
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
