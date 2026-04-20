from fastapi import APIRouter

from app.api.v1 import auth, deals, documents, pipeline, prices, suppliers

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(suppliers.router, prefix="/suppliers", tags=["suppliers"])
api_router.include_router(deals.router, prefix="/deals", tags=["deals"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
api_router.include_router(prices.router, prefix="/prices", tags=["prices"])
