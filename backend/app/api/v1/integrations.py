"""Mailbox connection endpoints."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import TokenEncryptionUnavailable
from app.core.db import get_db
from app.core.deps import get_current_user
from app.integrations.google_oauth import OAuthError, scope_explanations
from app.models.user import User
from app.schemas.integration import (
    ConnectionStatusOut,
    ConsentOut,
    ScopeExplanationOut,
)
from app.services import integration_service
from app.services.integration_service import IntegrationUnavailable

logger = logging.getLogger("atlas.integrations")

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/google/permissions", response_model=list[ScopeExplanationOut])
async def explain_permissions(
    _: User = Depends(get_current_user),
) -> list[ScopeExplanationOut]:
    """What Atlas will ask Google for, and why — shown before leaving the app."""
    return [ScopeExplanationOut(**e) for e in scope_explanations()]


@router.get("/google/status", response_model=ConnectionStatusOut)
async def connection_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConnectionStatusOut:
    status = await integration_service.status(db, user.id)
    return ConnectionStatusOut(
        provider=status.provider,
        connected=status.connected,
        mode=status.mode,
        address=status.address,
        detail=status.detail,
        fault=status.fault,
        missing_scopes=list(status.missing_scopes),
        can_send=status.can_send,
        connected_at=status.connected_at,
        last_used_at=status.last_used_at,
    )


@router.post("/google/connect", response_model=ConsentOut)
async def connect(
    redirect_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConsentOut:
    """Start authorisation. Reconnecting uses this same route.

    Google is asked for consent every time, so a reconnect always yields a fresh
    refresh token rather than silently reusing a dead grant.
    """
    try:
        url = await integration_service.begin_authorization(
            db, user.id, redirect_to=redirect_to
        )
    except IntegrationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TokenEncryptionUnavailable as exc:
        logger.error("OAuth connect blocked: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail sign-in is misconfigured on this deployment and cannot store "
                "credentials safely."
            ),
        ) from exc
    await db.commit()
    return ConsentOut(authorization_url=url, scopes=scope_explanations())


@router.get("/google/callback")
async def callback(
    state: str = Query(default=""),
    code: str = Query(default=""),
    error: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Google redirects here.

    Unauthenticated by necessity — the browser arrives from Google, not from the app —
    so the single-use ``state`` token is what binds the callback to the user who
    started it.
    """
    settings = get_settings()
    base = settings.cors_origin_list[0] if settings.cors_origin_list else ""

    def back(outcome: str, message: str = "") -> RedirectResponse:
        url = f"{base}/settings/integrations?gmail={outcome}"
        if message:
            url += f"&message={quote(message, safe='')}"
        return RedirectResponse(url=url, status_code=303)

    if error:
        # The user pressed Cancel, or Google refused. Not an application failure.
        return back("cancelled", "Access was not granted.")
    if not state or not code:
        return back("error", "Google's response was incomplete.")

    try:
        await integration_service.complete_authorization(db, state=state, code=code)
    except (OAuthError, TokenEncryptionUnavailable) as exc:
        await db.rollback()
        logger.warning("OAuth callback failed: %s", exc)
        return back("error", str(exc))

    await db.commit()
    return back("connected")


@router.delete("/google", status_code=204)
async def disconnect(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Revoke at Google and delete the stored tokens."""
    await integration_service.disconnect(db, user.id)
    await db.commit()
