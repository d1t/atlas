import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


@app.on_event("startup")
async def on_startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Atlas backend ready (env=%s, llm=%s)", settings.app_env, settings.llm_provider)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "llm_provider": settings.llm_provider}


app.include_router(api_router)
