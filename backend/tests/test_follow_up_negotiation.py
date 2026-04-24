"""Tests for the stage-aware follow_up_email document type.

Asserts the five-stage disclosure matrix is actually enforced in the
generated text: stage 2 never contains a price, stage 3 cites market
reference and counters below the supplier quote, stage 4 mentions
destination port + deposit, stage 5 mentions SPA + LC pre-advice.
"""
from __future__ import annotations

import re

import pytest

from app.ai.negotiation_strategy import (
    NegotiationContext,
    NegotiationStage,
    build_disclosure_guidance,
)
from app.services.document_generation import DocumentGenerationService


@pytest.fixture
def svc() -> DocumentGenerationService:
    return DocumentGenerationService()


_SENDER = {
    "full_name": "Dapo Thomas",
    "company_name": "Atlas Commodities Ltd",
    "title": "Director",
    "email": "dapo@atlas.example.com",
    "phone": "+44 20 7000 0000",
}
_SUPPLIER = {
    "name": "Brasil Sugar International",
    "country": "Brazil",
    "commodity": "sugar",
    "email": "sales@brasil-sugar.example.com",
}


def _inputs_for(stage: NegotiationStage, **extra) -> dict:
    ctx = NegotiationContext(stage=stage, side="supplier")
    return {
        "sender": _SENDER,
        "supplier": _SUPPLIER,
        "deal": {"commodity": "sugar", "volume_mt": 5000, "incoterms": "CFR"},
        "negotiation": build_disclosure_guidance(ctx),
        **extra,
    }


async def test_stage_2_follow_up_has_no_price(svc: DocumentGenerationService):
    inputs = _inputs_for(NegotiationStage.FIRST_RESPONSE)
    _, content = await svc.generate("follow_up_email", inputs)
    low = content.lower()

    # No USD/MT number should appear in a stage-2 follow-up.
    assert not re.search(r"\$\s?[0-9]{2,4}", content), (
        f"stage-2 must not contain a price: {content}"
    )

    # Must escalate commitment (SCO / NCNDA / payment-term range / inspection).
    assert "sco" in low, "stage-2 must ask for full SCO"
    assert "ncnda" in low or "inspection" in low or "payment" in low

    # Must contain at least three question marks (explicit asks).
    assert content.count("?") >= 3, f"stage-2 needs ≥3 questions: {content}"


async def test_stage_3_follow_up_anchors_to_market_reference(
    svc: DocumentGenerationService,
):
    inputs = _inputs_for(
        NegotiationStage.COUNTER_OFFER,
        market_reference={
            "exchange": "ICE",
            "ticker": "SB=F",
            "price_mt": 295.00,
            "source": "yahoo_finance",
        },
        supplier_quote={"price_mt": 560.00, "incoterms": "CFR"},
    )
    _, content = await svc.generate("follow_up_email", inputs)
    low = content.lower()

    assert "sb=f" in low or "ice" in low
    assert "295" in content

    # Counter between market + basis and the supplier quote.
    prices = [float(m) for m in re.findall(r"\$([0-9]{3,4}\.[0-9]{2})", content)]
    counters = [p for p in prices if 300 <= p < 560]
    assert counters, f"no counter in ({300}, {560}): {prices}"


async def test_stage_4_follow_up_reveals_port_and_deposit(
    svc: DocumentGenerationService,
):
    inputs = _inputs_for(NegotiationStage.TERMS_NEGOTIATION)
    _, content = await svc.generate("follow_up_email", inputs)
    low = content.lower()

    assert "port" in low, "stage-4 must reference the destination port"
    assert "deposit" in low or "lc" in low, "stage-4 must mention deposit or LC format"
    assert content.count("?") >= 3


async def test_stage_5_follow_up_focuses_on_spa_and_lc(
    svc: DocumentGenerationService,
):
    inputs = _inputs_for(NegotiationStage.CLOSE)
    _, content = await svc.generate("follow_up_email", inputs)
    low = content.lower()

    assert "spa" in low
    assert "lc" in low or "pre-advice" in low
    assert "vessel" in low or "loading" in low


async def test_follow_up_signs_with_real_sender(svc: DocumentGenerationService):
    inputs = _inputs_for(NegotiationStage.FIRST_RESPONSE)
    _, content = await svc.generate("follow_up_email", inputs)

    assert "Dapo Thomas" in content
    assert "Atlas Commodities Ltd" in content
    assert "[Your Name]" not in content
    assert "Brasil Sugar International" in content


async def test_document_endpoint_injects_negotiation_from_lead(
    monkeypatch, tmp_path,
):
    """End-to-end: POST /documents/generate with supplier_lead_id should
    auto-build the negotiation block from the lead's stage."""
    import os
    from importlib import reload

    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'neg.db'}")
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

    transport = ASGITransport(app=main_mod.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post(
                "/api/v1/auth/register",
                json={"email": "n@t.com", "password": "hunter22x", "full_name": "Neg Tester"},
            )
            r = await c.post(
                "/api/v1/auth/login",
                json={"email": "n@t.com", "password": "hunter22x"},
            )
            token = r.json()["access_token"]
            h = {"Authorization": f"Bearer {token}"}

            r = await c.post(
                "/api/v1/opportunities",
                headers=h,
                json={
                    "title": "Brazil sugar",
                    "commodity": "sugar",
                    "volume_mt": 5000,
                    "destination": "Lagos",
                },
            )
            assert r.status_code in (200, 201), r.text
            opp_id = r.json()["id"]

            r = await c.post(
                f"/api/v1/opportunities/{opp_id}/supplier-leads",
                headers=h,
                json={
                    "supplier_name": "Brasil Sugar International",
                    "country": "Brazil",
                    "email": "sales@brasil-sugar.example.com",
                },
            )
            assert r.status_code in (200, 201), r.text
            lead_id = r.json()["id"]
            assert r.json()["negotiation_stage"] == 1

            # Advance the lead to stage 2 via PATCH.
            r = await c.patch(
                f"/api/v1/opportunities/{opp_id}/supplier-leads/{lead_id}",
                headers=h,
                json={
                    "negotiation_stage": 2,
                    "intel": {"quoted_price_usd_mt": 560.0},
                    "disclosed": {"commodity": "sugar", "volume_band": "3-5k MT/mo"},
                    "price_mt": 560.0,
                },
            )
            assert r.status_code == 200, r.text
            assert r.json()["negotiation_stage"] == 2
            assert r.json()["intel"]["quoted_price_usd_mt"] == 560.0

            # Generate a follow-up email; server should inject the stage-2
            # negotiation block for us.
            r = await c.post(
                "/api/v1/documents/generate",
                headers=h,
                json={
                    "type": "follow_up_email",
                    "opportunity_id": opp_id,
                    "supplier_lead_id": lead_id,
                },
            )
            assert r.status_code == 201, r.text
            doc = r.json()
            neg = doc["inputs"].get("negotiation")
            assert neg and neg["stage"] == 2
            assert neg["side"] == "supplier"

            # Stage 2 email must not contain a price.
            assert not re.search(r"\$\s?[0-9]{2,4}", doc["content"])
    finally:
        await db_mod.engine.dispose()
        db_path = tmp_path / "neg.db"
        if os.path.exists(db_path):
            os.remove(db_path)


async def test_document_endpoint_auto_wires_supplier_quote_at_stage_3(
    monkeypatch, tmp_path,
):
    """End-to-end: a stage-3 follow-up must auto-inject the SupplierLead's
    persisted ``price_mt`` into ``negotiation.supplier_quote`` so the mock
    counter can compute an actual $X.XX/MT anchor below the supplier quote.
    Regression test for the PR #9 blocker where ``supplier_quote`` was never
    populated and stage-3 emails shipped without a numeric counter.
    """
    import os
    from importlib import reload

    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'neg3.db'}"
    )
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

    # Stub the live-price feed so the test is deterministic and offline.
    from app.api.v1 import documents as documents_mod
    from app.integrations.yahoo_finance import PriceQuote

    async def _fake_get_price(_commodity: str) -> PriceQuote:
        return PriceQuote(
            commodity="sugar",
            display="Sugar #11",
            ticker="SB=F",
            exchange="ICE",
            quoted_unit="USD/lb",
            raw_price=0.1337,
            price_mt=295.00,
            currency="USD",
            timestamp=0,
            previous_close=None,
            change_pct=None,
            source="test",
        )

    monkeypatch.setattr(documents_mod, "get_price", _fake_get_price)

    transport = ASGITransport(app=main_mod.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post(
                "/api/v1/auth/register",
                json={
                    "email": "n3@t.com",
                    "password": "hunter22x",
                    "full_name": "Neg Tester",
                },
            )
            r = await c.post(
                "/api/v1/auth/login",
                json={"email": "n3@t.com", "password": "hunter22x"},
            )
            token = r.json()["access_token"]
            h = {"Authorization": f"Bearer {token}"}

            r = await c.post(
                "/api/v1/opportunities",
                headers=h,
                json={
                    "title": "Brazil sugar",
                    "commodity": "sugar",
                    "volume_mt": 5000,
                    "destination": "Lagos",
                },
            )
            opp_id = r.json()["id"]

            r = await c.post(
                f"/api/v1/opportunities/{opp_id}/supplier-leads",
                headers=h,
                json={
                    "supplier_name": "Brasil Sugar International",
                    "country": "Brazil",
                    "email": "sales@brasil-sugar.example.com",
                    "price_mt": 560.0,
                    "quoted_incoterms": "CFR",
                },
            )
            lead_id = r.json()["id"]

            # Advance the lead to stage 3 (counter-offer).
            r = await c.patch(
                f"/api/v1/opportunities/{opp_id}/supplier-leads/{lead_id}",
                headers=h,
                json={"negotiation_stage": 3},
            )
            assert r.status_code == 200, r.text

            r = await c.post(
                "/api/v1/documents/generate",
                headers=h,
                json={
                    "type": "follow_up_email",
                    "opportunity_id": opp_id,
                    "supplier_lead_id": lead_id,
                },
            )
            assert r.status_code == 201, r.text
            doc = r.json()

            # supplier_quote must be auto-wired from the lead's price_mt.
            quote = doc["inputs"].get("supplier_quote")
            assert quote and quote.get("price_mt") == 560.0, doc["inputs"]
            assert quote.get("incoterms") == "CFR"

            # Stage-3 email must contain a numeric counter strictly below
            # the supplier's $560 quote (the whole point of this PR).
            prices = [
                float(m)
                for m in re.findall(r"\$([0-9]{3,4}\.[0-9]{2})", doc["content"])
            ]
            counters = [p for p in prices if 300 <= p < 560]
            assert counters, f"stage-3 must emit a counter <$560: {doc['content']}"
    finally:
        await db_mod.engine.dispose()
        db_path = tmp_path / "neg3.db"
        if os.path.exists(db_path):
            os.remove(db_path)
