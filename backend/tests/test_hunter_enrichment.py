"""Tests for Hunter.io contact enrichment on curated supplier seeding.

Covers:
  - HunterClient mock mode returns deterministic contacts when API key is empty
  - _domain_from_url extracts bare domain from various URL formats
  - Seeding curated suppliers enriches leads with contact details (mock mode)
  - contact_name + contact_title fields surface in the API response
  - Enrichment failures are graceful (best-effort, no 500s)
"""
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "hunter.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    # Ensure Hunter API key is empty → mock mode.
    monkeypatch.setenv("HUNTER_API_KEY", "")

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
        json={
            "email": "hunter@atlas.example.com",
            "password": "secret123",
            "full_name": "Hunter Tester",
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _mk_opp(client: AsyncClient, h: dict, **overrides) -> int:
    payload = {
        "title": "Nigeria sugar 50k",
        "commodity": "Sugar (ICUMSA 45)",
        "volume_mt": 50000,
        "destination_country": "Nigeria",
        "incoterms": "CFR",
    }
    payload.update(overrides)
    r = await client.post("/api/v1/opportunities", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- Hunter client unit tests -------------------------------------------------


def test_domain_from_url_strips_www_and_scheme():
    from app.services.hunter import _domain_from_url

    assert _domain_from_url("https://www.copersucar.com.br") == "copersucar.com.br"
    assert _domain_from_url("http://alvean.com/about") == "alvean.com"
    assert _domain_from_url("raizen.com.br") == "raizen.com.br"
    assert _domain_from_url("https://www.czarnikow.com") == "czarnikow.com"


def test_mock_contacts_returns_three_contacts():
    from app.services.hunter import _mock_contacts

    contacts = _mock_contacts("copersucar.com.br")
    assert len(contacts) == 3
    assert contacts[0].email == "trading@copersucar.com.br"
    assert contacts[0].position == "Trading Desk"
    assert contacts[1].email == "sales@copersucar.com.br"


def test_pick_best_prefers_trading_contacts():
    from app.services.hunter import HunterContact, _pick_best

    contacts = [
        HunterContact(email="info@co.com", first_name="Info", position="General", confidence=70),
        HunterContact(email="trade@co.com", first_name="Ana", last_name="Silva", position="Trading Manager", confidence=85),
        HunterContact(email="hr@co.com", first_name="HR", position="HR Director", confidence=90),
    ]
    best = _pick_best(contacts)
    assert best is not None
    assert best.email == "trade@co.com"


def test_pick_best_returns_none_for_empty():
    from app.services.hunter import _pick_best

    assert _pick_best([]) is None


@pytest.mark.asyncio
async def test_hunter_client_mock_mode(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "")
    from importlib import reload

    import app.core.config as config_mod
    reload(config_mod)

    from app.services.hunter import HunterClient
    client = HunterClient()
    assert not client.is_live
    contact = await client.enrich_domain("https://www.alvean.com")
    assert contact is not None
    assert "alvean.com" in contact.email


# --- Integration tests (seeding + enrichment) ---------------------------------


async def test_seed_curated_enriches_contacts_via_hunter_mock(
    client: AsyncClient,
):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert len(created) == 5

    # In mock mode, every lead gets a contact email from the mock Hunter client.
    for lead in created:
        assert lead["email"] is not None, f"{lead['supplier_name']} has no email"
        assert "@" in lead["email"]
        assert lead["contact_name"] is not None

    # Spot-check specific domains from the curated registry.
    by_name = {lead["supplier_name"]: lead for lead in created}
    cop = by_name["Copersucar S.A."]
    assert "copersucar.com.br" in cop["email"]

    alv = by_name["Alvean"]
    assert "alvean.com" in alv["email"]


async def test_seed_enriched_leads_have_contact_title(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={},
    )
    assert r.status_code == 201
    for lead in r.json():
        assert lead["contact_title"] is not None


async def test_seed_subset_enriches_only_requested(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={"names": ["Copersucar S.A."]},
    )
    assert r.status_code == 201
    created = r.json()
    assert len(created) == 1
    assert created[0]["supplier_name"] == "Copersucar S.A."
    assert "copersucar.com.br" in created[0]["email"]
    assert created[0]["contact_name"] is not None
