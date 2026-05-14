"""Tests for the curated counterparty registry + opportunity endpoints.

Covers:
  - registry lookup is commodity-substring + optional exact-country
  - GET /opportunities/{id}/curated-suppliers returns the matching set with
    ``already_added`` flags reflecting existing supplier leads
  - POST .../seed creates SupplierLead rows for each entry, leaves emails blank
    (so the site-crawler fills them in later), and is idempotent against
    repeated calls / pre-existing leads with the same name
  - empty registry match (e.g. iron ore) returns [] cleanly rather than 500
"""
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "curated.db"
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
            "email": "curated@atlas.example.com",
            "password": "secret123",
            "full_name": "Curated Tester",
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


# --- registry unit tests ------------------------------------------------------


def test_registry_matches_sugar_substring():
    from app.data.curated_counterparties import get_curated_counterparties

    # Commodity is matched as a case-insensitive substring so real-world
    # opportunity titles like "Raw Sugar" or "Sugar (ICUMSA 45)" both hit.
    assert len(get_curated_counterparties("sugar")) == 5
    assert len(get_curated_counterparties("Raw Sugar")) == 5
    assert len(get_curated_counterparties("SUGAR (ICUMSA 45)")) == 5
    assert get_curated_counterparties("sugar")[0].country == "Brazil"


def test_registry_country_filter_is_optional_and_exact():
    from app.data.curated_counterparties import get_curated_counterparties

    # Without a country we get all sugar entries (currently all Brazilian).
    assert {cp.country for cp in get_curated_counterparties("sugar")} == {"Brazil"}
    # Case-insensitive country match.
    assert len(get_curated_counterparties("sugar", "brazil")) == 5
    # Wrong origin → empty (this is intentionally strict — curated entries are
    # origin-specific).
    assert get_curated_counterparties("sugar", "India") == []


def test_registry_unknown_commodity_returns_empty():
    from app.data.curated_counterparties import get_curated_counterparties

    assert get_curated_counterparties("iron ore") == []
    assert get_curated_counterparties("") == []
    assert get_curated_counterparties(None) == []


# --- HTTP endpoint tests ------------------------------------------------------


async def test_list_curated_returns_brazil_sugar_set_for_sugar_opportunity(
    client: AsyncClient,
):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers", headers=h
    )
    assert r.status_code == 200
    entries = r.json()
    names = {e["name"] for e in entries}
    assert names == {
        "Copersucar S.A.",
        "Alvean",
        "Raízen",
        "Sucden Brazil",
        "Czarnikow Brazil",
    }
    # None are added yet on a fresh opportunity.
    assert all(e["already_added"] is False for e in entries)
    # Each entry carries the public website (so the contact-crawler has
    # something to work with) and a meaningful description.
    for e in entries:
        assert e["website"].startswith("http")
        assert len(e["description"]) > 20


async def test_list_curated_marks_existing_leads_as_already_added(
    client: AsyncClient,
):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    # Pre-attach Copersucar via the normal supplier-leads endpoint.
    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/supplier-leads",
        headers=h,
        json={"supplier_name": "copersucar s.a.", "country": "Brazil"},
    )
    assert r.status_code == 201

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers", headers=h
    )
    assert r.status_code == 200
    entries = {e["name"]: e for e in r.json()}
    assert entries["Copersucar S.A."]["already_added"] is True
    assert entries["Alvean"]["already_added"] is False


async def test_list_curated_empty_for_non_curated_commodity(client: AsyncClient):
    h = await _auth(client)
    # Opportunities the registry doesn't cover should get an empty list, not
    # a 404 or 500 — keeps the UI quiet for non-curated lanes.
    opp_id = await _mk_opp(client, h, commodity="Iron Ore", destination_country="India")

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers", headers=h
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_seed_curated_creates_supplier_leads_with_blank_emails(
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

    # Emails are deliberately blank — the site-crawler fills them in later.
    assert all(lead["email"] is None for lead in created)

    # The notes line carries the curated-set provenance so the user can see
    # where the row came from when scanning the supplier panel.
    assert all(lead["notes"].startswith("[Curated]") for lead in created)

    # The leads land on the opportunity.
    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
    )
    assert r.status_code == 200
    names = {lead["supplier_name"] for lead in r.json()}
    assert {"Copersucar S.A.", "Alvean", "Raízen"}.issubset(names)


async def test_seed_curated_is_idempotent(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={},
    )
    assert r.status_code == 201
    assert len(r.json()) == 5

    # Calling again must not duplicate rows — second call returns []. The
    # operation stays a 201 either way (a successful no-op create is still
    # a successful create).
    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={},
    )
    assert r.status_code == 201
    assert r.json() == []

    r = await client.get(
        f"/api/v1/opportunities/{opp_id}/supplier-leads", headers=h
    )
    assert len(r.json()) == 5


async def test_seed_curated_subset_by_name(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={"names": ["Copersucar S.A.", "RAÍZEN"]},
    )
    assert r.status_code == 201
    created = r.json()
    assert {c["supplier_name"] for c in created} == {"Copersucar S.A.", "Raízen"}


async def test_seed_curated_unknown_names_returns_404(client: AsyncClient):
    h = await _auth(client)
    opp_id = await _mk_opp(client, h)

    r = await client.post(
        f"/api/v1/opportunities/{opp_id}/curated-suppliers/seed",
        headers=h,
        json={"names": ["NotARealMill Ltd"]},
    )
    assert r.status_code == 404


async def test_discover_prepends_curated_for_brazil_sugar(client: AsyncClient):
    """When AI Discover runs on sugar+Brazil, curated entries appear first."""
    h = await _auth(client)

    r = await client.post(
        "/api/v1/suppliers/discover",
        headers=h,
        json={"commodity": "sugar", "country": "Brazil", "limit": 10},
    )
    assert r.status_code == 200, r.text
    results = r.json()
    # The first up-to-5 results must be the curated set (in registry order).
    expected_curated = [
        "Copersucar S.A.",
        "Alvean",
        "Raízen",
        "Sucden Brazil",
        "Czarnikow Brazil",
    ]
    leading_names = [r["name"] for r in results[: len(expected_curated)]]
    assert leading_names == expected_curated
    assert all(
        r["source"] == "curated" for r in results[: len(expected_curated)]
    )
