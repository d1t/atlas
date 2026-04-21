"""Tests for the negotiation-aware outreach / counter-offer email generation.

The core claim being asserted: the cold outreach email NEVER leaks our buy price
(that would anchor against us), while the counter-offer email DOES price the
deal and, when a market reference is provided, anchors against it.
"""
from __future__ import annotations

import pytest

from app.services.document_generation import DocumentGenerationService


@pytest.fixture
def svc() -> DocumentGenerationService:
    return DocumentGenerationService()


_BASE_INPUTS = {
    "sender": {
        "full_name": "Dapo Thomas",
        "company_name": "Atlas Commodities Ltd",
        "title": "Director",
        "email": "dapo@atlas.example.com",
        "phone": "+44 20 7000 0000",
    },
    "supplier": {
        "name": "Brasil Sugar International",
        "country": "Brazil",
        "commodity": "sugar",
        "email": "sales@brasil-sugar.example.com",
        "website": "https://brasil-sugar.example.com",
        "type": "mill",
    },
    "deal": {
        "title": "Brazil ICUMSA-45, 5k MT/mo",
        "commodity": "sugar",
        "volume_mt": 5000,
        "buy_price": 450.0,
        "sell_price": 510.0,
        "freight_estimate": 40.0,
        "incoterms": "CFR",
        "currency": "USD",
        "structure": "back-to-back",
    },
}


async def test_outreach_email_never_leaks_buy_price(svc: DocumentGenerationService):
    """Rule #1: do not anchor against ourselves. Buy price must NOT appear."""
    _, content = await svc.generate("outreach_email", _BASE_INPUTS)

    assert "450" not in content, "buy_price 450 must not appear in outreach email"
    assert "510" not in content, "sell_price 510 must not appear in outreach email"
    # Standard anchors for 'we offered a price' phrasing:
    for banned in ["our offer", "we offer", "our target price", "our bid"]:
        assert banned.lower() not in content.lower(), (
            f"outreach should not contain offer-price phrasing: {banned!r}"
        )


async def test_outreach_email_asks_supplier_to_quote_first(
    svc: DocumentGenerationService,
):
    """The email should explicitly request FOB/CFR indicative pricing from THEM."""
    _, content = await svc.generate("outreach_email", _BASE_INPUTS)
    low = content.lower()

    # Must ask for indicative pricing
    assert "fob" in low and "cfr" in low, "should ask for FOB/CFR levels"
    assert "indicative" in low or "quote" in low or "level" in low

    # Must anchor on specs and signal NCNDA readiness
    assert "ncnda" in low
    assert str(_BASE_INPUTS["deal"]["volume_mt"]) in content or "5,000" in content


async def test_outreach_email_uses_real_sender_and_supplier_names(
    svc: DocumentGenerationService,
):
    _, content = await svc.generate("outreach_email", _BASE_INPUTS)
    assert "Dapo Thomas" in content
    assert "Atlas Commodities Ltd" in content
    assert "Brasil Sugar International" in content
    assert "[Your Name]" not in content
    assert "Atlas Trade" not in content  # old hardcoded placeholder
    assert "specified commodity" not in content.lower()


async def test_counter_offer_anchors_to_market_reference(
    svc: DocumentGenerationService,
):
    """With a market reference, the counter should cite the exchange + ticker
    and propose a number below the supplier's quote."""
    inputs = {
        **_BASE_INPUTS,
        "market_reference": {
            "exchange": "ICE",
            "ticker": "SB=F",
            "price_mt": 295.00,
            "source": "yahoo_finance",
        },
        "supplier_quote": {"price_mt": 560.00, "incoterms": "CFR"},
    }
    _, content = await svc.generate("counter_offer_email", inputs)
    low = content.lower()

    # Anchors market reference explicitly
    assert "sb=f" in low or "ice" in low, "should reference the exchange/ticker"
    assert "295" in content, "should cite the market reference price"

    # Proposes a counter BELOW the supplier quote (supplier quoted 560; counter < 560)
    import re

    prices = [float(m) for m in re.findall(r"\$([0-9]{3,4}\.[0-9]{2})", content)]
    counters = [p for p in prices if 300 <= p < 560]
    assert counters, f"no counter between market and supplier quote found in: {prices}"


async def test_counter_offer_gracefully_handles_missing_market_reference(
    svc: DocumentGenerationService,
):
    """Without a market reference, the email should still make sense — asking for
    the supplier's basis rationale rather than inventing a price."""
    inputs = {**_BASE_INPUTS, "supplier_quote": {"price_mt": 560.00}}
    _, content = await svc.generate("counter_offer_email", inputs)
    assert "Brasil Sugar International" in content
    assert "Dapo Thomas" in content


async def test_document_generation_api_auto_injects_market_reference(
    monkeypatch, tmp_path,
):
    """Counter-offer email via the REST endpoint should auto-populate
    market_reference from the Yahoo Finance integration."""
    import os
    from importlib import reload

    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'co.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

    import app.core.config as config_mod
    import app.core.db as db_mod

    reload(config_mod)
    reload(db_mod)

    import app.main as main_mod

    reload(main_mod)

    async with db_mod.engine.begin() as conn:
        from app.models import Base

        await conn.run_sync(Base.metadata.create_all)

    # Stub the market data feed so the test doesn't hit the network.
    import app.api.v1.documents as docs_mod
    from app.integrations.yahoo_finance import PriceQuote

    async def fake_get_price(name: str) -> PriceQuote:
        return PriceQuote(
            commodity="sugar",
            display="Sugar #11 (ICE)",
            ticker="SB=F",
            exchange="ICE",
            quoted_unit="cents/lb",
            raw_price=13.40,
            price_mt=295.42,
            currency="USD",
            timestamp=1700000000,
            previous_close=13.35,
            change_pct=0.37,
            source="yahoo_finance",
        )

    monkeypatch.setattr(docs_mod, "get_price", fake_get_price)

    transport = ASGITransport(app=main_mod.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post(
                "/api/v1/auth/register",
                json={"email": "t@test.com", "password": "hunter22x", "full_name": "Tester"},
            )
            r = await c.post(
                "/api/v1/auth/login",
                json={"email": "t@test.com", "password": "hunter22x"},
            )
            token = r.json()["access_token"]
            h = {"Authorization": f"Bearer {token}"}

            r = await c.post(
                "/api/v1/documents/generate",
                headers=h,
                json={
                    "type": "counter_offer_email",
                    "inputs": {
                        "supplier": {"name": "Test Mill", "commodity": "sugar"},
                        "supplier_quote": {"price_mt": 560.0},
                    },
                },
            )
            assert r.status_code == 201, r.text
            doc = r.json()

            # market_reference must have been injected server-side
            mr = doc["inputs"].get("market_reference")
            assert mr and mr["ticker"] == "SB=F"
            assert mr["price_mt"] == 295.42

            # And the rendered email must cite that anchor
            assert "295" in doc["content"]
            assert "SB=F" in doc["content"] or "ICE" in doc["content"]
    finally:
        await db_mod.engine.dispose()
        db_path = tmp_path / "co.db"
        if os.path.exists(db_path):
            os.remove(db_path)
