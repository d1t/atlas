"""Tests for the Gmail email layer (offline mode — no credentials).

In offline mode sends are recorded but not transmitted, which lets us exercise
the full send/log/lead-advancement flow deterministically.
"""
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "email.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    # Ensure Gmail is unconfigured => offline mode.
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    from importlib import reload

    import app.core.config as config_mod
    import app.core.db as db_mod
    reload(config_mod)
    reload(db_mod)

    import app.main as main_mod
    reload(main_mod)

    async with db_mod.engine.begin() as conn:
        from app.models import Base
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=main_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await db_mod.engine.dispose()
    if os.path.exists(db_path):
        os.remove(db_path)


async def _auth(client: AsyncClient) -> dict:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "trader@atlas.example.com", "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _mk_opp_with_supplier(client: AsyncClient, h: dict) -> tuple[int, int]:
    r = await client.post(
        "/api/v1/opportunities",
        headers=h,
        json={"title": "Sugar 50k", "commodity": "sugar", "volume_mt": 50000},
    )
    opp_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/supplier-leads",
        headers=h,
        json={
            "supplier_name": "Atlas Mill",
            "country": "Brazil",
            "email": "mill@supplier.example.com",
        },
    )
    return opp_id, r.json()["id"]


async def test_gmail_status_offline(client: AsyncClient):
    h = await _auth(client)
    r = await client.get("/api/v1/email/status", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["configured"] is False
    assert data["mode"] == "offline"


async def test_send_email_records_and_advances_lead(client: AsyncClient):
    h = await _auth(client)
    opp_id, sid = await _mk_opp_with_supplier(client, h)

    r = await client.post(
        "/api/v1/email/send",
        headers=h,
        json={
            "to_email": "mill@supplier.example.com",
            "subject": "Sugar enquiry",
            "body": "Do you have ICUMSA 45 available?",
            "opportunity_id": opp_id,
            "supplier_lead_id": sid,
        },
    )
    assert r.status_code == 201, r.text
    msg = r.json()
    assert msg["direction"] == "outbound"
    assert msg["status"] == "offline"
    assert msg["to_email"] == "mill@supplier.example.com"

    # Lead should now be 'contacted' with a contact timestamp.
    lead = (
        await client.get(
            f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
        )
    ).json()[0]
    assert lead["status"] == "contacted"
    assert lead["last_contacted_at"] is not None

    # And the message is listed against the opportunity.
    lst = (
        await client.get(
            f"/api/v1/email?opportunity_id={opp_id}", headers=h
        )
    ).json()
    assert len(lst) == 1
    assert lst[0]["subject"] == "Sugar enquiry"


async def test_send_document_parses_subject(client: AsyncClient):
    h = await _auth(client)
    opp_id, sid = await _mk_opp_with_supplier(client, h)

    doc = (
        await client.post(
            "/api/v1/documents/generate",
            headers=h,
            json={
                "type": "outreach_email",
                "opportunity_id": opp_id,
                "supplier_lead_id": sid,
            },
        )
    ).json()

    r = await client.post(
        "/api/v1/email/send-document",
        headers=h,
        json={
            "document_id": doc["id"],
            "opportunity_id": opp_id,
            "supplier_lead_id": sid,
        },
    )
    assert r.status_code == 201, r.text
    msg = r.json()
    # Recipient resolved from the lead's inline email.
    assert msg["to_email"] == "mill@supplier.example.com"
    assert msg["document_id"] == doc["id"]
    assert msg["subject"]


async def test_send_document_without_recipient_400(client: AsyncClient):
    h = await _auth(client)
    r = await client.post(
        "/api/v1/opportunities",
        headers=h,
        json={"title": "Corn", "commodity": "corn"},
    )
    opp_id = r.json()["id"]
    # Supplier lead with NO email.
    sid = (
        await client.post(
            f"/api/v1/opportunities/{opp_id}/supplier-leads",
            headers=h,
            json={"supplier_name": "No Email Co"},
        )
    ).json()["id"]
    doc = (
        await client.post(
            "/api/v1/documents/generate",
            headers=h,
            json={
                "type": "outreach_email",
                "opportunity_id": opp_id,
                "supplier_lead_id": sid,
            },
        )
    ).json()
    r = await client.post(
        "/api/v1/email/send-document",
        headers=h,
        json={"document_id": doc["id"], "supplier_lead_id": sid},
    )
    assert r.status_code == 400


async def test_sync_replies_offline_noop(client: AsyncClient):
    h = await _auth(client)
    r = await client.post("/api/v1/email/sync", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "offline"
    assert data["fetched"] == 0
    assert data["new_messages"] == []


async def test_sync_replies_matches_lead(client: AsyncClient, monkeypatch):
    """A fake fetched reply is matched to the lead and folded into its intel."""
    h = await _auth(client)
    opp_id, sid = await _mk_opp_with_supplier(client, h)

    from datetime import UTC, datetime

    import app.core.db as db_mod
    from app.integrations.gmail import FetchedEmail, GmailClient
    from app.services import email_service

    async def fake_fetch(self, *, since=None, limit=50):
        return [
            FetchedEmail(
                message_id="<reply-1@supplier>",
                from_email="mill@supplier.example.com",
                from_name="Atlas Mill",
                subject="RE: Sugar enquiry",
                body="Yes, we offer ICUMSA 45 at $450/MT FOB Santos.",
                received_at=datetime.now(UTC),
            )
        ]

    # Stub the network fetch and force the client into live mode.
    monkeypatch.setattr(GmailClient, "fetch_replies", fake_fetch)
    monkeypatch.setattr(GmailClient, "configured", property(lambda self: True))

    async with db_mod.AsyncSessionLocal() as session:
        new_messages, fetched, mode = await email_service.sync_replies(session)

    assert mode == "live"
    assert fetched == 1
    assert len(new_messages) == 1
    assert new_messages[0].matched_side == "supplier"
    assert new_messages[0].supplier_lead_id == sid

    # The reply advanced the lead to 'quoted' and stored its text in intel.
    lead = (
        await client.get(
            f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
        )
    ).json()[0]
    assert lead["status"] == "quoted"
    assert "ICUMSA 45" in lead["intel"]["last_supplier_response"]

    # Re-running is idempotent (same Message-ID isn't ingested twice).
    async with db_mod.AsyncSessionLocal() as session:
        again, _, _ = await email_service.sync_replies(session)
    assert again == []
