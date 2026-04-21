"""Tests for the Yahoo Finance price integration."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient, Request, Response

from app.integrations import yahoo_finance


def _chart_payload(
    ticker: str,
    price: float,
    previous_close: float = 0.0,
    currency: str = "USD",
) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": ticker,
                        "regularMarketPrice": price,
                        "chartPreviousClose": previous_close,
                        "regularMarketTime": 1_700_000_000,
                        "currency": currency,
                    }
                }
            ],
            "error": None,
        }
    }


class _FakeClient:
    """Stand-in for httpx.AsyncClient that returns a fixed payload."""

    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self._status = status
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append(url)
        req = Request("GET", url, params=params)
        return Response(
            status_code=self._status,
            content=json.dumps(self._payload).encode(),
            headers={"content-type": "application/json"},
            request=req,
        )


def _install_client(monkeypatch, payload, status=200):
    fake = _FakeClient(payload, status)
    monkeypatch.setattr(
        yahoo_finance.httpx,
        "AsyncClient",
        lambda *a, **kw: fake,
    )
    return fake


@pytest.fixture(autouse=True)
def _clear_cache():
    yahoo_finance.clear_cache()
    yield
    yahoo_finance.clear_cache()


def test_resolve_commodity_matches_aliases():
    assert yahoo_finance.resolve_commodity("Sugar").slug == "sugar"
    assert yahoo_finance.resolve_commodity("raw sugar").slug == "sugar"
    assert yahoo_finance.resolve_commodity("Brazilian ICUMSA 45").slug == "sugar"
    assert yahoo_finance.resolve_commodity("Wheat futures").slug == "wheat"
    assert yahoo_finance.resolve_commodity("maize").slug == "corn"
    assert yahoo_finance.resolve_commodity("unknown thing") is None
    assert yahoo_finance.resolve_commodity(None) is None


async def test_sugar_conversion_cents_per_lb_to_mt(monkeypatch):
    # 22.50 cents/lb → 22.50 * 22.0462 = $496.04/MT
    payload = _chart_payload("SB=F", price=22.50, previous_close=22.00)
    _install_client(monkeypatch, payload)

    q = await yahoo_finance.get_price("sugar")
    assert q is not None
    assert q.ticker == "SB=F"
    assert q.quoted_unit == "cents/lb"
    assert q.raw_price == 22.50
    assert q.price_mt is not None
    assert abs(q.price_mt - 496.04) < 0.05
    assert q.change_pct is not None
    assert abs(q.change_pct - 2.27) < 0.05  # (22.50-22.00)/22.00


async def test_wheat_conversion_cents_per_bushel_to_mt(monkeypatch):
    # 550 cents/bu × 0.367437 = $202.09/MT
    payload = _chart_payload("ZW=F", price=550.0, previous_close=540.0)
    _install_client(monkeypatch, payload)

    q = await yahoo_finance.get_price("wheat")
    assert q is not None
    assert q.ticker == "ZW=F"
    assert q.price_mt is not None
    assert abs(q.price_mt - 202.09) < 0.1


async def test_cocoa_no_conversion_native_usd_per_mt(monkeypatch):
    payload = _chart_payload("CC=F", price=4200.0)
    _install_client(monkeypatch, payload)

    q = await yahoo_finance.get_price("cocoa")
    assert q is not None
    assert q.quoted_unit == "USD/MT"
    assert q.price_mt == 4200.0  # direct passthrough


async def test_crude_oil_no_mt_conversion(monkeypatch):
    payload = _chart_payload("CL=F", price=75.20)
    _install_client(monkeypatch, payload)

    q = await yahoo_finance.get_price("crude_oil")
    assert q is not None
    assert q.price_mt is None  # $/bbl stays $/bbl
    assert q.raw_price == 75.20


async def test_cache_hits_on_second_call(monkeypatch):
    payload = _chart_payload("SB=F", price=22.00)
    fake = _install_client(monkeypatch, payload)

    q1 = await yahoo_finance.get_price("sugar")
    q2 = await yahoo_finance.get_price("sugar")
    assert q1 is q2
    assert len(fake.calls) == 1  # second call came from cache


async def test_unknown_commodity_returns_none(monkeypatch):
    # Should not even hit the network for unknown commodities.
    bad = AsyncMock()
    monkeypatch.setattr(yahoo_finance.httpx, "AsyncClient", bad)

    q = await yahoo_finance.get_price("palladium-zephyr")
    assert q is None
    bad.assert_not_called()


async def test_malformed_payload_returns_none(monkeypatch):
    _install_client(monkeypatch, {"chart": {"result": []}})
    q = await yahoo_finance.get_price("sugar")
    assert q is None


async def test_http_failure_returns_none(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            import httpx

            raise httpx.ConnectError("boom")

    monkeypatch.setattr(yahoo_finance.httpx, "AsyncClient", lambda *a, **kw: _Boom())
    q = await yahoo_finance.get_price("sugar")
    assert q is None


# ---------------- API-level tests ----------------


@pytest.fixture
async def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "prices.db"
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


async def _token(c: AsyncClient) -> str:
    r = await c.post(
        "/api/v1/auth/register",
        json={"email": "p@a.com", "password": "secret123", "full_name": "P"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


async def test_list_commodities_endpoint(api_client: AsyncClient):
    token = await _token(api_client)
    r = await api_client.get(
        "/api/v1/prices", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert any(c["slug"] == "sugar" and c["ticker"] == "SB=F" for c in body["commodities"])


async def test_price_endpoint_sugar(monkeypatch, api_client: AsyncClient):
    yahoo_finance.clear_cache()
    payload = _chart_payload("SB=F", price=22.0, previous_close=21.5)
    _install_client(monkeypatch, payload)

    token = await _token(api_client)
    r = await api_client.get(
        "/api/v1/prices/sugar", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "SB=F"
    assert body["commodity"] == "sugar"
    assert body["price_mt"] is not None
    assert abs(body["price_mt"] - 22.0 * 22.0462) < 0.1


async def test_price_endpoint_unknown_commodity(api_client: AsyncClient):
    token = await _token(api_client)
    r = await api_client.get(
        "/api/v1/prices/palladium-zephyr",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


async def test_price_endpoint_upstream_failure(monkeypatch, api_client: AsyncClient):
    yahoo_finance.clear_cache()

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            import httpx

            raise httpx.ConnectError("upstream down")

    monkeypatch.setattr(yahoo_finance.httpx, "AsyncClient", lambda *a, **kw: _Boom())

    token = await _token(api_client)
    r = await api_client.get(
        "/api/v1/prices/sugar", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 502
