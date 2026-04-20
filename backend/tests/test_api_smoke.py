"""End-to-end smoke tests against the FastAPI app with SQLite + mock LLM."""
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

    # Re-import the app with the patched env.
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


async def _auth_client(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "trader@atlas.example.com", "password": "secret123", "full_name": "Test Trader"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_auth_flow(client: AsyncClient):
    token = await _auth_client(client)
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "trader@atlas.example.com"


async def test_supplier_crud_and_discovery(client: AsyncClient):
    token = await _auth_client(client)
    h = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/api/v1/suppliers",
        headers=h,
        json={"name": "Atlas Mill", "type": "mill", "country": "Brazil", "commodity": "sugar"},
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await client.post(
        "/api/v1/suppliers/discover",
        headers=h,
        json={"commodity": "sugar", "country": "Brazil", "limit": 5},
    )
    assert r.status_code == 200

    r = await client.get(f"/api/v1/suppliers/{sid}", headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Atlas Mill"


async def test_deal_flow_with_pricing_and_stage(client: AsyncClient):
    token = await _auth_client(client)
    h = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/api/v1/deals",
        headers=h,
        json={
            "title": "Brazil Sugar 10k",
            "commodity": "sugar",
            "volume_mt": 1000,
            "buy_price": 400,
            "sell_price": 500,
            "freight_estimate": 40,
            "incoterms": "FOB",
        },
    )
    assert r.status_code == 201, r.text
    deal = r.json()
    assert deal["margin_per_mt"] == 60
    assert deal["total_margin"] == 60_000
    assert deal["structure"] == "principal"
    did = deal["id"]

    r = await client.post(
        f"/api/v1/deals/{did}/stage", headers=h, json={"stage": "contacted"}
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "contacted"

    r = await client.post(
        f"/api/v1/deals/{did}/stage", headers=h, json={"stage": "closed"}
    )
    assert r.status_code == 400

    r = await client.post(
        "/api/v1/deals/structure",
        headers=h,
        json={"buy_price": 100, "sell_price": 120, "freight_estimate": 5, "volume_mt": 500},
    )
    assert r.status_code == 200
    assert r.json()["margin_per_mt"] == 15


async def test_document_generation(client: AsyncClient):
    token = await _auth_client(client)
    h = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/api/v1/documents/generate",
        headers=h,
        json={"type": "ncnda", "inputs": {"buyer": "A", "seller": "B", "commodity": "sugar"}},
    )
    assert r.status_code == 201
    doc = r.json()
    assert "NCNDA" in doc["content"].upper() or "NON-CIRCUMVENTION" in doc["content"].upper()

    r = await client.get(f"/api/v1/documents/{doc['id']}/export.md", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")

    r = await client.get(f"/api/v1/documents/{doc['id']}/export.docx", headers=h)
    assert r.status_code == 200
