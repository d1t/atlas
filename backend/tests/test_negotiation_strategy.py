"""Tests for the game-theory-aware negotiation strategy module.

These are pure-function tests — no DB, no LLM. They pin down the disclosure
matrix so refactors can't silently weaken the buyer's negotiating position.
"""
from __future__ import annotations

import pytest

from app.ai.negotiation_strategy import (
    BUYER_DISCLOSURE_MATRIX,
    SUPPLIER_DISCLOSURE_MATRIX,
    NegotiationContext,
    NegotiationStage,
    build_disclosure_guidance,
    extract_intel_keys,
    next_stage,
    render_guidance_prompt,
)


class TestStageEnum:
    def test_five_stages_in_order(self) -> None:
        assert int(NegotiationStage.COLD_OUTREACH) == 1
        assert int(NegotiationStage.FIRST_RESPONSE) == 2
        assert int(NegotiationStage.COUNTER_OFFER) == 3
        assert int(NegotiationStage.TERMS_NEGOTIATION) == 4
        assert int(NegotiationStage.CLOSE) == 5

    def test_labels_are_human_readable(self) -> None:
        labels = {s.label for s in NegotiationStage}
        assert "Cold outreach" in labels
        assert "Counter-offer" in labels
        assert "Close / SPA" in labels


class TestDisclosureMatrix:
    @pytest.mark.parametrize("matrix", [SUPPLIER_DISCLOSURE_MATRIX, BUYER_DISCLOSURE_MATRIX])
    def test_every_stage_has_all_four_buckets(self, matrix) -> None:
        for stage, cell in matrix.items():
            assert set(cell.keys()) == {"reveal", "ask", "hold", "tactics"}, (
                f"stage {stage} is missing buckets"
            )
            for bucket, items in cell.items():
                assert isinstance(items, list) and items, (
                    f"stage {stage} bucket {bucket} must be non-empty"
                )

    def test_stage_1_holds_price_and_port(self) -> None:
        """Anchoring rule #1: stage-1 must hold price and destination port."""
        hold = SUPPLIER_DISCLOSURE_MATRIX[NegotiationStage.COLD_OUTREACH]["hold"]
        assert any("target buy price" in h or "USD/MT number" in h for h in hold)
        assert any("destination port" in h for h in hold), hold

    def test_stage_1_asks_them_to_anchor(self) -> None:
        ask = SUPPLIER_DISCLOSURE_MATRIX[NegotiationStage.COLD_OUTREACH]["ask"]
        assert any("indicative" in a.lower() and "MT" in a for a in ask), ask

    def test_stage_3_is_only_stage_that_reveals_price(self) -> None:
        """Stage 3 (counter-offer) is the single stage where we lead with a price."""
        for stage in NegotiationStage:
            reveal = SUPPLIER_DISCLOSURE_MATRIX[stage]["reveal"]
            mentions_price = any("USD/MT" in r or "WORKING LEVEL" in r for r in reveal)
            if stage == NegotiationStage.COUNTER_OFFER:
                assert mentions_price, "stage-3 MUST reveal price"
            else:
                assert not mentions_price, (
                    f"stage {stage} must not reveal a USD/MT number: {reveal}"
                )

    def test_stage_3_anchors_to_market(self) -> None:
        tactics = SUPPLIER_DISCLOSURE_MATRIX[NegotiationStage.COUNTER_OFFER]["tactics"]
        assert any("anchor" in t.lower() and "basis" in t.lower() for t in tactics), tactics

    def test_end_buyer_identity_only_disclosed_at_close(self) -> None:
        """Commitment-escalation rule: end-buyer identity is the last thing revealed."""
        for stage in [
            NegotiationStage.COLD_OUTREACH,
            NegotiationStage.FIRST_RESPONSE,
            NegotiationStage.COUNTER_OFFER,
            NegotiationStage.TERMS_NEGOTIATION,
        ]:
            hold = SUPPLIER_DISCLOSURE_MATRIX[stage]["hold"]
            assert any("end-buyer" in h.lower() for h in hold), (
                f"stage {stage} must still hold end-buyer identity"
            )
        close_reveal = SUPPLIER_DISCLOSURE_MATRIX[NegotiationStage.CLOSE]["reveal"]
        assert any("end-buyer" in r.lower() for r in close_reveal)

    def test_buyer_side_never_reveals_our_margin(self) -> None:
        for stage in NegotiationStage:
            hold = BUYER_DISCLOSURE_MATRIX[stage]["hold"]
            assert any("margin" in h.lower() or "buy price" in h.lower() for h in hold), (
                f"buyer-side stage {stage} must hold our margin: {hold}"
            )


class TestBuildGuidance:
    def test_round_trip_supplier_side(self) -> None:
        ctx = NegotiationContext(
            stage=NegotiationStage.COLD_OUTREACH,
            side="supplier",
            intel={"claimed_origin": "Brazil"},
            disclosed={"commodity": "sugar"},
        )
        g = build_disclosure_guidance(ctx)
        assert g["stage"] == 1
        assert g["stage_label"] == "Cold outreach"
        assert g["side"] == "supplier"
        assert g["reveal"] == SUPPLIER_DISCLOSURE_MATRIX[NegotiationStage.COLD_OUTREACH]["reveal"]
        assert g["known_intel"] == {"claimed_origin": "Brazil"}
        assert g["previously_disclosed"] == {"commodity": "sugar"}

    def test_buyer_side_uses_buyer_matrix(self) -> None:
        ctx = NegotiationContext(stage=NegotiationStage.COUNTER_OFFER, side="buyer")
        g = build_disclosure_guidance(ctx)
        assert g["side"] == "buyer"
        assert g["reveal"] == BUYER_DISCLOSURE_MATRIX[NegotiationStage.COUNTER_OFFER]["reveal"]


class TestRenderGuidancePrompt:
    def test_stage_1_prompt_forbids_price(self) -> None:
        ctx = NegotiationContext(stage=NegotiationStage.COLD_OUTREACH)
        text = render_guidance_prompt(ctx)
        assert "HOLD" in text
        assert "target buy price" in text or "USD/MT number" in text
        # Prompt must direct the LLM to ask at least three things.
        assert "ASK" in text
        assert text.count("  - ") >= 9  # 3 bucket lists minimum

    def test_stage_3_prompt_includes_market_anchor(self) -> None:
        ctx = NegotiationContext(
            stage=NegotiationStage.COUNTER_OFFER,
            market_reference={
                "exchange": "ICE",
                "ticker": "SB=F",
                "price_mt": 295.42,
            },
        )
        text = render_guidance_prompt(ctx)
        assert "MARKET REFERENCE available" in text
        assert "SB=F" in text
        assert "295.42" in text

    def test_prompt_lists_previously_disclosed(self) -> None:
        ctx = NegotiationContext(
            stage=NegotiationStage.FIRST_RESPONSE,
            disclosed={"destination_country": "Nigeria", "volume_band": "3-4k"},
        )
        text = render_guidance_prompt(ctx)
        assert "PREVIOUSLY DISCLOSED" in text
        assert "destination_country" in text
        assert "volume_band" in text


class TestNextStage:
    def test_advances_by_one(self) -> None:
        assert next_stage(NegotiationStage.COLD_OUTREACH) == NegotiationStage.FIRST_RESPONSE
        assert next_stage(NegotiationStage.FIRST_RESPONSE) == NegotiationStage.COUNTER_OFFER

    def test_clamped_at_close(self) -> None:
        assert next_stage(NegotiationStage.CLOSE) == NegotiationStage.CLOSE


class TestExtractIntel:
    def test_extracts_price_and_incoterms(self) -> None:
        reply = (
            "Dear buyer, thank you for your enquiry. We can offer at $540/MT CFR Lagos, "
            "MOQ 12,500 MT. Payment DLC at sight accepted. Lead time 45 days after LC "
            "confirmation."
        )
        intel = extract_intel_keys(reply)
        assert intel["quoted_price_usd_mt"] == 540.0
        assert "CFR" in intel["quoted_incoterms"]
        assert intel["min_order_mt"] == 12500.0
        assert "DLC" in intel["accepted_payment"]
        assert intel["lead_time_days"] == 45

    def test_empty_reply_returns_empty(self) -> None:
        assert extract_intel_keys("") == {}
        assert extract_intel_keys("Thanks, will revert.") == {}

    def test_extracts_fob_when_present(self) -> None:
        reply = "We quote $470/MT FOB Santos. SBLC accepted."
        intel = extract_intel_keys(reply)
        assert "FOB" in intel["quoted_incoterms"]
        assert "SBLC" in intel["accepted_payment"]
