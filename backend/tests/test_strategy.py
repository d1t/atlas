"""Tests for the strategy engine — pillars, cadence generation, and the board."""
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "strategy.db"
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
        json={"email": "strategist@atlas.example.com", "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _mk_strategy(client: AsyncClient, h: dict, **overrides) -> dict:
    payload = {
        "title": "Control Brazil->Nigeria sugar chain",
        "north_star": "Own the value chain: 50k MT/quarter at >$18/MT margin.",
        "commodity": "sugar",
        "origin_region": "Brazil",
        "destination_region": "Nigeria",
        "target_volume_mt": 50000,
        "target_margin_per_mt": 18,
    }
    payload.update(overrides)
    r = await client.post("/api/v1/strategy", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_strategy_drafts_four_pillars(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    assert set(s["pillars"].keys()) == {
        "origination",
        "demand",
        "supply",
        "execution",
    }
    for pillar in s["pillars"].values():
        assert pillar["objective"]
        assert "target" in pillar


async def test_generate_plan_and_board(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)

    # An opportunity with only 1 supplier => cadence should flag coverage gaps.
    opp = (
        await client.post(
            "/api/v1/opportunities",
            headers=h,
            json={"title": "Sugar 50k", "commodity": "sugar", "volume_mt": 50000},
        )
    ).json()
    await client.post(
        f"/api/v1/opportunities/{opp['id']}/supplier-leads",
        headers=h,
        json={"supplier_name": "Atlas Mill", "email": "m@x.example.com"},
    )

    tasks = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
        )
    ).json()
    assert len(tasks) >= 1
    pillars_present = {t["pillar"] for t in tasks}
    # Sourcing more suppliers / buyers should show up in supply/demand pillars.
    assert pillars_present & {"supply", "demand", "origination"}

    board = (
        await client.get(f"/api/v1/strategy/{s['id']}/board", headers=h)
    ).json()
    assert len(board["pillars"]) == 4
    assert board["week_tasks"]
    assert "headline" in board
    # Supply actual should reflect the single supplier lead we created.
    supply = next(p for p in board["pillars"] if p["pillar"] == "supply")
    assert supply["actual"] == 1.0


async def test_generate_plan_is_idempotent(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)

    first = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
        )
    ).json()
    second = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
        )
    ).json()
    # Regenerating replaces auto tasks rather than duplicating them.
    assert len(first) == len(second)


async def test_task_toggle_sets_completed_at(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    task = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/tasks",
            headers=h,
            json={"pillar": "supply", "title": "Call 3 mills", "priority": "high"},
        )
    ).json()
    assert task["source"] == "manual"
    assert task["completed_at"] is None

    done = (
        await client.patch(
            f"/api/v1/strategy/{s['id']}/tasks/{task['id']}",
            headers=h,
            json={"status": "done"},
        )
    ).json()
    assert done["status"] == "done"
    assert done["completed_at"] is not None


async def test_buyer_cadence_and_close_tasks(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    opp = (
        await client.post(
            "/api/v1/opportunities",
            headers=h,
            json={"title": "Sugar 50k", "commodity": "sugar", "volume_mt": 50000},
        )
    ).json()

    # A brand-new buyer lead should earn a buy-side outreach task linked to it.
    buyer = (
        await client.post(
            f"/api/v1/opportunities/{opp['id']}/buyer-leads",
            headers=h,
            json={"buyer_name": "Lagos Foods", "email": "b@lagos.example.com"},
        )
    ).json()

    # A supplier deep in negotiation should earn a deal/close task.
    supplier = (
        await client.post(
            f"/api/v1/opportunities/{opp['id']}/supplier-leads",
            headers=h,
            json={"supplier_name": "Atlas Mill", "email": "m@x.example.com"},
        )
    ).json()
    await client.patch(
        f"/api/v1/opportunities/{opp['id']}/supplier-leads/{supplier['id']}",
        headers=h,
        json={"negotiation_stage": 4, "status": "quoted"},
    )

    tasks = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
        )
    ).json()

    demand_for_buyer = [
        t
        for t in tasks
        if t["pillar"] == "demand" and t["buyer_lead_id"] == buyer["id"]
    ]
    assert demand_for_buyer, "expected a buy-side outreach task linked to the buyer"

    close_for_supplier = [
        t
        for t in tasks
        if t["pillar"] == "execution"
        and t["supplier_lead_id"] == supplier["id"]
        and "SPA" in t["title"]
    ]
    assert close_for_supplier, "expected a deal/close task for the stage-4 supplier"


async def test_send_weekly_digest_offline(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    await client.post(
        f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
    )

    r = await client.post(
        f"/api/v1/strategy/{s['id']}/digest",
        headers=h,
        json={"to_email": "trader@atlas.example.com"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # No Gmail creds in tests => offline mode, recorded but not transmitted.
    assert body["mode"] == "offline"
    assert body["message"]["status"] == "offline"
    assert body["message"]["direction"] == "outbound"
    assert "Weekly plan" in body["subject"]
    assert body["message"]["to_email"] == "trader@atlas.example.com"


async def test_digest_requires_recipient_when_offline(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    r = await client.post(f"/api/v1/strategy/{s['id']}/digest", headers=h, json={})
    # Offline + no configured Gmail address + no explicit recipient => 400.
    assert r.status_code == 400, r.text


async def test_draft_and_send_task_email_ticks_off(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    opp = (
        await client.post(
            "/api/v1/opportunities",
            headers=h,
            json={"title": "Sugar 50k", "commodity": "sugar", "volume_mt": 50000},
        )
    ).json()
    buyer = (
        await client.post(
            f"/api/v1/opportunities/{opp['id']}/buyer-leads",
            headers=h,
            json={"buyer_name": "Lagos Foods", "email": "buyer@lagos.example.com"},
        )
    ).json()

    tasks = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
        )
    ).json()
    task = next(t for t in tasks if t["buyer_lead_id"] == buyer["id"])

    # Draft resolves the recipient from the linked buyer lead.
    draft = (
        await client.get(
            f"/api/v1/strategy/{s['id']}/tasks/{task['id']}/draft-email", headers=h
        )
    ).json()
    assert draft["to_email"] == "buyer@lagos.example.com"
    assert draft["can_send"] is True
    assert draft["subject"]
    assert draft["body"]
    assert draft["mode"] == "offline"

    # Sending offline-records the email and ticks the task off.
    sent = await client.post(
        f"/api/v1/strategy/{s['id']}/tasks/{task['id']}/send-email",
        headers=h,
        json={
            "to_email": draft["to_email"],
            "subject": draft["subject"],
            "body": draft["body"],
        },
    )
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["mode"] == "offline"
    assert body["message"]["status"] == "offline"
    assert body["message"]["buyer_lead_id"] == buyer["id"]
    assert body["task"]["status"] == "done"
    assert body["task"]["completed_at"] is not None

    # And it shows up in the outbox for that buyer lead.
    emails = (
        await client.get(
            f"/api/v1/email?buyer_lead_id={buyer['id']}", headers=h
        )
    ).json()
    assert any(m["subject"] == draft["subject"] for m in emails)


async def test_draft_task_email_without_recipient(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    # A manual task with no linked lead can't be sent until a recipient is added.
    task = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/tasks",
            headers=h,
            json={"pillar": "origination", "title": "Frame a new trade idea"},
        )
    ).json()
    draft = (
        await client.get(
            f"/api/v1/strategy/{s['id']}/tasks/{task['id']}/draft-email", headers=h
        )
    ).json()
    assert draft["to_email"] is None
    assert draft["can_send"] is False
    assert draft["reason"]
    assert draft["subject"]


async def test_manual_task_survives_regeneration(client: AsyncClient):
    h = await _auth(client)
    s = await _mk_strategy(client, h)
    await client.post(
        f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
    )
    manual = (
        await client.post(
            f"/api/v1/strategy/{s['id']}/tasks",
            headers=h,
            json={"pillar": "execution", "title": "Board sync"},
        )
    ).json()
    await client.post(
        f"/api/v1/strategy/{s['id']}/generate-plan", headers=h, json={}
    )
    board = (
        await client.get(f"/api/v1/strategy/{s['id']}/board", headers=h)
    ).json()
    ids = {t["id"] for t in board["week_tasks"]}
    assert manual["id"] in ids
