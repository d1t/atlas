from fastapi import APIRouter

from app.api.v1 import (
    auth,
    deals,
    documents,
    email,
    execution,
    integrations,
    opportunities,
    pipeline,
    prices,
    strategy,
    suppliers,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(suppliers.router, prefix="/suppliers", tags=["suppliers"])
api_router.include_router(deals.router, prefix="/deals", tags=["deals"])
api_router.include_router(
    opportunities.router, prefix="/opportunities", tags=["opportunities"]
)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
api_router.include_router(prices.router, prefix="/prices", tags=["prices"])
api_router.include_router(email.router, prefix="/email", tags=["email"])
api_router.include_router(strategy.router, prefix="/strategy", tags=["strategy"])
api_router.include_router(
    execution.router, prefix="/execution", tags=["execution"]
)
api_router.include_router(integrations.router)
