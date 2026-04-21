"""Tests for the V2 opportunity orchestration layer.

Covers:
  - Opportunity CRUD
  - SupplierLead / BuyerLead CRUD
  - Matching engine ranking order + margin math
  - Deal Health Score composition + recommendations
  - Next Action Engine rules
  - Promote-match-to-deal end to end
"""
import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "opps.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

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
            "email": "orc@atlas.example.com",
            "password": "secret123",
            "full_name": "Orchestrator",
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _mk_opp(client: AsyncClient, h: dict, **overrides) -> int:
    payload = {
        "title": "Nigeria sugar 50k",
        "commodity": "sugar",
        "volume_mt": 50000,
        "destination_country": "Nigeria",
        "incoterms": "CFR",
        "target_price_min": 470,
        "target_price_max": 510,
    }
    payload.update(overrides)
    r = await client.post("/api/v1/opportunities", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _mk_supplier(
    client: AsyncClient, h: dict, opp_id: int, **overrides
) -> int:
    payload = {
        "supplier_name": "Atlas Mill",
        "country": "Brazil",
        "price_mt": 440.0,
        "quoted_incoterms": "FOB",
        "credibility_score": 70,
        "responsiveness_score": 80,
    }
    payload.update(overrides)
    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h, json=payload
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _mk_buyer(
    client: AsyncClient, h: dict, opp_id: int, **overrides
) -> int:
    payload = {
        "buyer_name": "Lagos Refinery",
        "country": "Nigeria",
        "target_price_mt": 500.0,
        "volume_mt": 50000,
        "appetite": "high",
        "urgency": "medium",
    }
    payload.update(overrides)
    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/buyer-leads", headers=h, json=payload
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- basic CRUD ---------------------------------------------------------------


async def test_opportunity_crud(client: AsyncClient):
    h = await _auth(client)

    opp_id = await _mk_opp(client, h)

    r = await client.get("/api/v1/opportunities", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "draft"

    r = await client.patch(
        f"/api/v1/opportunities/{opp_id}",
        headers=h,
        json={"status": "sourcing", "notes": "kick off"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "sourcing"
    assert r.json()["notes"] == "kick off"

    r = await client.delete(f"/api/v1/opportunities/{opp_id}", headers=h)
    assert r.status_code == 204
    r = await client.get(f"/api/v1/opportunities/{opp_id}", headers=h)
    assert r.status_code == 404


async def test_supplier_and_buyer_leads_crud(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    sid = await _mk_supplier(client, h, opp_id)
    bid = await _mk_buyer(client, h, opp_id)

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
    )
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = await client.patch(
        f"/api/v1/opportunities/{opp_id}/supplier-leads/{sid}",
        headers=h,
        json={"status": "quoted", "price_mt": 445.0},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "quoted"
    assert r.json()["price_mt"] == 445.0

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/buyer-leads", headers=h
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == bid


# --- matching engine ----------------------------------------------------------


async def test_matching_ranks_higher_margin_first(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    # Two suppliers with different prices.
    cheap = await _mk_supplier(
        client, h, opp_id, supplier_name="Cheap Mill", price_mt=440
    )
    expensive = await _mk_supplier(
        client, h, opp_id, supplier_name="Premium Mill", price_mt=480
    )
    await _mk_buyer(client, h, opp_id, buyer_name="Buyer A", target_price_mt=500)

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/matches", headers=h
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total_pairs"] == 2
    assert data["viable_pairs"] == 2
    # Highest-margin pair (cheap supplier) should sort first.
    top = data["pairs"][0]
    assert top["supplier_lead_id"] == cheap
    assert top["margin_per_mt"] == 60
    # Second pair is the expensive supplier.
    assert data["pairs"][1]["supplier_lead_id"] == expensive
    assert data["pairs"][1]["margin_per_mt"] == 20
    # Top score must strictly beat the second.
    assert top["score"] > data["pairs"][1]["score"]


async def test_matching_handles_missing_quote(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)
    await _mk_supplier(client, h, opp_id, price_mt=None)
    await _mk_buyer(client, h, opp_id, target_price_mt=500)

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/matches", headers=h
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total_pairs"] == 1
    assert data["viable_pairs"] == 0
    # Pair is included with a neutral score, not dropped.
    assert data["pairs"][0]["score"] == 0.0
    assert "Waiting on supplier quote" in data["pairs"][0]["reasoning"][0]


# --- health score -------------------------------------------------------------


async def test_health_empty_opportunity_is_weak(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.get(f"/api/v1/opportunities/{opp_id}/health", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["score"] < 30
    assert data["status"] == "Weak"
    assert len(data["factors"]) == 5


async def test_health_improves_with_coverage_and_margin(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    # 3 suppliers + 2 buyers + healthy margin + all recently contacted.
    for i in range(3):
        await _mk_supplier(
            client,
            h,
            opp_id,
            supplier_name=f"Mill {i}",
            price_mt=440 + i * 2,
            credibility_score=80,
            responsiveness_score=80,
        )
    for i in range(2):
        await _mk_buyer(
            client,
            h,
            opp_id,
            buyer_name=f"Buyer {i}",
            target_price_mt=500,
            appetite="high",
            urgency="high",
        )

    # Touch freshness via the update endpoint.
    leads = (
        await client.get(
            f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
        )
    ).json()
    recent = datetime.now(UTC).isoformat()
    for lead in leads:
        await client.patch(
            f"/api/v1/opportunities/{opp_id}/supplier-leads/{lead['id']}",
            headers=h,
            json={"last_contacted_at": recent},
        )

    r = await client.get(f"/api/v1/opportunities/{opp_id}/health", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["score"] >= 70
    assert data["status"] == "Viable"


# --- next action engine -------------------------------------------------------


async def test_next_action_empty_opp_recommends_sourcing(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/next-actions", headers=h
    )
    assert r.status_code == 200
    actions = r.json()["actions"]
    labels = [a["action"] for a in actions]
    assert any("Source" in x for x in labels)
    assert any("buyer" in x.lower() for x in labels)


async def test_next_action_flags_stale_supplier_followup(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    sid = await _mk_supplier(
        client, h, opp_id, price_mt=None, supplier_name="Stale Mill"
    )
    # Contacted 3 days ago but no quote received.
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    await client.patch(
        f"/api/v1/opportunities/{opp_id}/supplier-leads/{sid}",
        headers=h,
        json={"status": "contacted", "last_contacted_at": old},
    )

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/next-actions", headers=h
    )
    actions = r.json()["actions"]
    assert any("Follow up Stale Mill" in a["action"] for a in actions)


# --- promote match to deal ----------------------------------------------------


async def test_promote_match_creates_deal_and_updates_statuses(
    client: AsyncClient,
):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)
    sid = await _mk_supplier(client, h, opp_id, price_mt=440)
    bid = await _mk_buyer(client, h, opp_id, target_price_mt=500, volume_mt=50000)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/deals",
        headers=h,
        json={"supplier_lead_id": sid, "buyer_lead_id": bid},
    )
    assert r.status_code == 201, r.text
    deal_summary = r.json()
    assert deal_summary["buy_price"] == 440
    assert deal_summary["sell_price"] == 500
    assert deal_summary["margin_per_mt"] == 60
    assert deal_summary["total_margin"] == 60 * 50000
    assert deal_summary["opportunity_id"] == opp_id

    # Opportunity + leads should have been moved to 'matched' / 'shortlisted' / 'committed'.
    opp = (
        await client.get(f"/api/v1/opportunities/{opp_id}", headers=h)
    ).json()
    assert opp["status"] == "matched"
    sup = (
        await client.get(
            f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
        )
    ).json()[0]
    buy = (
        await client.get(
            f"/api/v1/opportunities/{opp_id}/buyer-leads", headers=h
        )
    ).json()[0]
    assert sup["status"] == "shortlisted"
    assert buy["status"] == "committed"

    # The created deal is retrievable via the existing /deals endpoint.
    r = await client.get(
        f"/api/v1/deals/{deal_summary['deal_id']}", headers=h
    )
    assert r.status_code == 200
    deal = r.json()
    assert deal["commodity"] == "sugar"
    assert deal["stage"] == "pricing"


async def test_dashboard_composes_everything(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)
    await _mk_supplier(client, h, opp_id, price_mt=440)
    await _mk_buyer(client, h, opp_id, target_price_mt=500)

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/dashboard", headers=h
    )
    assert r.status_code == 200
    data = r.json()
    assert data["opportunity"]["id"] == opp_id
    assert len(data["supplier_leads"]) == 1
    assert len(data["buyer_leads"]) == 1
    assert data["matches"]["total_pairs"] == 1
    assert data["health"]["score"] > 0
    assert len(data["next_actions"]["actions"]) >= 1
