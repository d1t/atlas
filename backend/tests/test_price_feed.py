"""Tests for the commodity futures price feed (CNBC primary, Yahoo fallback)."""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient, Request, Response

from app.integrations import price_feed


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


class _CaptureClient:
    """Fake client that records the query params of each GET."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.params: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.params.append(dict(params or {}))
        req = Request("GET", url, params=params)
        return Response(
            status_code=200,
            content=json.dumps(self._payload).encode(),
            headers={"content-type": "application/json"},
            request=req,
        )


def _cnbc_payload(last: str, previous_close: str = "", currency: str = "USD") -> dict:
    return {
        "FormattedQuoteResult": {
            "FormattedQuote": [
                {
                    "symbol": "@SB.1",
                    "last": last,
                    "previous_day_closing": previous_close,
                    "currencyCode": currency,
                }
            ]
        }
    }


def _disable_cnbc(monkeypatch):
    """Force the Yahoo fallback path so Yahoo-specific behaviour is exercised."""
    monkeypatch.setattr(price_feed, "_fetch_cnbc", AsyncMock(return_value=None))


def _install_client(monkeypatch, payload, status=200):
    """Install a fake transport serving a Yahoo chart payload, with CNBC (the
    primary source) disabled so the Yahoo fallback is what gets tested.
    """
    _disable_cnbc(monkeypatch)
    fake = _FakeClient(payload, status)
    monkeypatch.setattr(
        price_feed.httpx,
        "AsyncClient",
        lambda *a, **kw: fake,
    )
    return fake


@pytest.fixture(autouse=True)
def _clear_cache():
    price_feed.clear_cache()
    # Pre-seed the crumb so tests don't attempt the Yahoo handshake over the
    # network; the handshake itself is covered by a dedicated test.
    price_feed._crumb_cache = (time.time(), "test-crumb", {})
    yield
    price_feed.clear_cache()


def test_resolve_commodity_matches_aliases():
    assert price_feed.resolve_commodity("Sugar").slug == "sugar"
    assert price_feed.resolve_commodity("raw sugar").slug == "sugar"
    assert price_feed.resolve_commodity("Brazilian ICUMSA 45").slug == "sugar"
    assert price_feed.resolve_commodity("Wheat futures").slug == "wheat"
    assert price_feed.resolve_commodity("maize").slug == "corn"
    assert price_feed.resolve_commodity("unknown thing") is None
    assert price_feed.resolve_commodity(None) is None


async def test_sugar_conversion_cents_per_lb_to_mt(monkeypatch):
    # 22.50 cents/lb → 22.50 * 22.0462 = $496.04/MT
    payload = _chart_payload("SB=F", price=22.50, previous_close=22.00)
    _install_client(monkeypatch, payload)

    q = await price_feed.get_price("sugar")
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

    q = await price_feed.get_price("wheat")
    assert q is not None
    assert q.ticker == "ZW=F"
    assert q.price_mt is not None
    assert abs(q.price_mt - 202.09) < 0.1


async def test_cocoa_no_conversion_native_usd_per_mt(monkeypatch):
    payload = _chart_payload("CC=F", price=4200.0)
    _install_client(monkeypatch, payload)

    q = await price_feed.get_price("cocoa")
    assert q is not None
    assert q.quoted_unit == "USD/MT"
    assert q.price_mt == 4200.0  # direct passthrough


async def test_crude_oil_no_mt_conversion(monkeypatch):
    payload = _chart_payload("CL=F", price=75.20)
    _install_client(monkeypatch, payload)

    q = await price_feed.get_price("crude_oil")
    assert q is not None
    assert q.price_mt is None  # $/bbl stays $/bbl
    assert q.raw_price == 75.20


async def test_cache_hits_on_second_call(monkeypatch):
    payload = _chart_payload("SB=F", price=22.00)
    fake = _install_client(monkeypatch, payload)

    q1 = await price_feed.get_price("sugar")
    q2 = await price_feed.get_price("sugar")
    assert q1 is q2
    assert len(fake.calls) == 1  # second call came from cache


async def test_unknown_commodity_returns_none(monkeypatch):
    # Should not even hit the network for unknown commodities.
    bad = AsyncMock()
    monkeypatch.setattr(price_feed.httpx, "AsyncClient", bad)

    q = await price_feed.get_price("palladium-zephyr")
    assert q is None
    bad.assert_not_called()


async def test_malformed_payload_returns_none(monkeypatch):
    _install_client(monkeypatch, {"chart": {"result": []}})
    q = await price_feed.get_price("sugar")
    assert q is None


async def test_chart_request_includes_crumb(monkeypatch):
    payload = _chart_payload("SB=F", price=22.0)
    cap = _CaptureClient(payload)
    _disable_cnbc(monkeypatch)
    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: cap)
    monkeypatch.setattr(
        price_feed,
        "_get_crumb",
        AsyncMock(return_value=("CRUMB123", {"A": "1"})),
    )

    q = await price_feed.get_price("sugar")
    assert q is not None
    assert cap.params, "expected at least one chart request"
    assert cap.params[0].get("crumb") == "CRUMB123"


async def test_serves_stale_quote_on_fetch_failure(monkeypatch):
    # First call succeeds and populates the last-good cache.
    _install_client(monkeypatch, _chart_payload("SB=F", price=22.0))
    q1 = await price_feed.get_price("sugar")
    assert q1 is not None and not q1.stale

    # Expire the fresh cache but keep last-good, then make upstream fail.
    price_feed._cache.clear()

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            import httpx

            raise httpx.ConnectError("boom")

    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Boom())

    q2 = await price_feed.get_price("sugar")
    assert q2 is not None
    assert q2.stale is True
    assert q2.raw_price == q1.raw_price


async def test_retries_then_succeeds_on_transient_429(monkeypatch):
    payload = _chart_payload("SB=F", price=22.0)
    calls = {"n": 0}

    class _Flaky:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            req = Request("GET", url, params=params)
            # First attempt rate-limited, second succeeds.
            if calls["n"] == 1:
                return Response(status_code=429, content=b"", request=req)
            return Response(
                status_code=200,
                content=json.dumps(payload).encode(),
                headers={"content-type": "application/json"},
                request=req,
            )

    _disable_cnbc(monkeypatch)
    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Flaky())
    monkeypatch.setattr(price_feed, "_backoff_delay", lambda attempt: 0.0)

    q = await price_feed.get_price("sugar")
    assert q is not None
    assert q.raw_price == 22.0
    assert calls["n"] == 2  # retried once after the 429


async def test_http_failure_returns_none(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            import httpx

            raise httpx.ConnectError("boom")

    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Boom())
    q = await price_feed.get_price("sugar")
    assert q is None


# ---------------- CNBC (primary source) ----------------


async def test_cnbc_is_primary_and_yahoo_is_not_called(monkeypatch):
    fake = _FakeClient(_cnbc_payload("14.58", "14.50"))
    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: fake)

    q = await price_feed.get_price("sugar")
    assert q is not None
    assert q.source == "cnbc"
    assert q.raw_price == 14.58
    assert q.price_mt is not None
    assert abs(q.price_mt - 14.58 * 22.0462) < 0.05
    assert q.change_pct is not None
    assert abs(q.change_pct - 0.55) < 0.05  # (14.58-14.50)/14.50
    # Only CNBC was contacted — Yahoo is never reached when the primary works.
    assert len(fake.calls) == 1
    assert "cnbc.com" in fake.calls[0]


async def test_cnbc_parses_thousands_separator(monkeypatch):
    # CNBC formats larger quotes with commas, e.g. soybeans "1,209.25".
    fake = _FakeClient(_cnbc_payload("1,209.25"))
    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: fake)

    q = await price_feed.get_price("soybeans")
    assert q is not None
    assert q.raw_price == 1209.25


async def test_cnbc_unchanged_previous_close_yields_no_change_pct(monkeypatch):
    # CNBC sends the literal "UNCH" rather than a number when unchanged.
    fake = _FakeClient(_cnbc_payload("14.58", "UNCH"))
    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: fake)

    q = await price_feed.get_price("sugar")
    assert q is not None
    assert q.raw_price == 14.58
    assert q.previous_close is None
    assert q.change_pct is None


async def test_falls_back_to_yahoo_when_cnbc_unusable(monkeypatch):
    """An unparseable CNBC response must not block the Yahoo fallback."""
    calls: list[str] = []

    class _Router:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append(url)
            req = Request("GET", url, params=params)
            body = (
                {"unexpected": "shape"}
                if "cnbc.com" in url
                else _chart_payload("SB=F", price=22.0)
            )
            return Response(
                status_code=200,
                content=json.dumps(body).encode(),
                headers={"content-type": "application/json"},
                request=req,
            )

    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Router())

    q = await price_feed.get_price("sugar")
    assert q is not None
    assert q.source == "yahoo_finance"
    assert q.raw_price == 22.0
    assert any("cnbc.com" in c for c in calls)
    assert any("finance.yahoo.com" in c for c in calls)


def test_every_commodity_has_a_cnbc_symbol():
    missing = [s.slug for s in price_feed.COMMODITIES.values() if not s.cnbc_symbol]
    assert missing == []


# ---------------- Blocked-host handling ----------------


async def test_crumb_failure_is_cached_not_retried_per_commodity(monkeypatch):
    """A blocked handshake must be remembered — otherwise every commodity in a
    dashboard render repeats it and deepens the rate limit.
    """
    price_feed.clear_cache()
    attempts = {"n": 0}

    class _Blocked:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            attempts["n"] += 1
            req = Request("GET", url, params=params)
            return Response(status_code=429, content=b"", request=req)

    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Blocked())

    crumb1, _ = await price_feed._get_crumb()
    after_first = attempts["n"]
    crumb2, _ = await price_feed._get_crumb()

    assert crumb1 is None and crumb2 is None
    assert after_first > 0
    assert attempts["n"] == after_first, "second handshake should be short-circuited"


async def test_yahoo_breaker_trips_and_skips_further_calls(monkeypatch):
    """Once Yahoo is shown to be blocking, stop calling it for the cooldown."""
    price_feed.clear_cache()
    _disable_cnbc(monkeypatch)
    price_feed._crumb_cache = (time.time(), "test-crumb", {})
    monkeypatch.setattr(price_feed, "_backoff_delay", lambda attempt: 0.0)
    calls = {"n": 0}

    class _Blocked:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            req = Request("GET", url, params=params)
            return Response(status_code=429, content=b"", request=req)

    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Blocked())

    assert await price_feed.get_price("sugar") is None
    exhausted = calls["n"]
    assert exhausted == price_feed._MAX_ATTEMPTS

    # A different commodity must not re-attempt the blocked host.
    assert await price_feed.get_price("wheat") is None
    assert calls["n"] == exhausted


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
    price_feed.clear_cache()
    price_feed._crumb_cache = (time.time(), "test-crumb", {})
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
    price_feed.clear_cache()
    price_feed._crumb_cache = (time.time(), "test-crumb", {})

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            import httpx

            raise httpx.ConnectError("upstream down")

    monkeypatch.setattr(price_feed.httpx, "AsyncClient", lambda *a, **kw: _Boom())

    token = await _token(api_client)
    r = await api_client.get(
        "/api/v1/prices/sugar", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 502
