"""Game-theory-aware negotiation strategy for supplier / buyer outreach.

A cold outreach — and every follow-up after it — is a move in a multi-stage
bargaining game with asymmetric information. The goal of this module is to
encode the tactics a senior trader would apply implicitly, so the LLM prompt
gets a hard structural guide (what to reveal / ask / hold / how to deadline /
how to anchor) instead of re-inventing strategy on every call.

Core principles we enforce:

1. **Anchoring discipline.** Whoever quotes first sets the midpoint. We never
   quote a price before the counterparty has quoted theirs, and when we DO
   counter we anchor against an objective reference (ICE futures + a
   transparent basis) — not against their number.

2. **Progressive disclosure.** Information is leverage. We reveal facts in
   stages, each stage trading one piece of signal for a bigger piece of intel.
   Early stages hide destination-port, buyer identity, exact volume, deposit
   willingness; late stages require them for execution.

3. **Commitment escalation.** Each stage asks the counterparty to commit more
   (sign NCNDA, send SCO, send FCO, share bank reference). Cheap first asks
   filter tyre-kickers; expensive later asks cement the deal.

4. **Credible deadlines.** Every email carries a reason-to-respond-now tied to
   an external event (shipment window, origination cycle, futures roll) —
   never a fake 'respond in 24h or we walk' bluff.

5. **BATNA signalling.** We signal we have alternatives without bluffing about
   them. Phrases like 'we are comparing two origins this week' are honest
   (we ARE comparing two origins) and tilt pricing without lying.

6. **Non-price levers.** At every stage we offer ways the counterparty can
   defend margin without giving on price: tonnage upsize, LC tenor, packaging,
   inspection agency choice, deposit timing.

This module is provider-agnostic — it emits structured guidance, and the
LLM system-prompt builder in ``document_generation.py`` composes that into
hard constraints for the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class NegotiationStage(IntEnum):
    """The five-stage supplier-side bargaining game.

    Buyer side uses the same numbering with mirrored disclosure tables
    (see ``BUYER_DISCLOSURE_MATRIX``).
    """

    COLD_OUTREACH = 1
    """Cold email. We know nothing; they know nothing. Goal: earn a reply
    + filter for realness. Reveal identity/commodity/geo/volume-band. Ask
    for FOB/CFR indicative levels, MOQ, incoterms, NCNDA readiness.
    Withhold: destination port, exact volume, buyer identity, payment
    ceiling, our target price."""

    FIRST_RESPONSE = 2
    """Supplier has replied (SCO / indicative quote received). Confirm
    they're real; escalate commitment ask. Reveal a tighter volume band
    and destination-country (not port). Ask for full SCO / payment-terms
    range / accepted inspection agency / origination cycle."""

    COUNTER_OFFER = 3
    """We now have a quote worth countering. This is the ONLY stage where
    we lead with a price — and it MUST be anchored to market (ICE futures
    + transparent basis). Offer two levers to defend margin (deposit,
    shorter LC tenor). Reveal: working-level price with methodology, a
    soft-commitment signal. Ask for: B&F (bid-and-float) or tiered
    price-vs-volume matrix."""

    TERMS_NEGOTIATION = 4
    """Price is roughly agreed. Negotiating non-price terms that compound
    into 3-8% of total value: packaging, freight split, inspection, LC
    format, tenor, loading window, demurrage. Reveal: port of discharge,
    deposit percentage, inspection preference. Ask for: delivery window
    confirmation, packaging spec, freight-split willingness."""

    CLOSE = 5
    """Pre-SPA / pre-contract. All material terms on the table. Final
    disclosure: end-buyer identity (via NCNDA), LC issuance timeline,
    signing authority. Ask for: draft SPA / pro-forma invoice, loading
    port, banking instrument confirmation, final commercial invoice."""

    @property
    def label(self) -> str:
        mapping = {
            1: "Cold outreach",
            2: "SCO received",
            3: "Counter-offer",
            4: "Terms negotiation",
            5: "Close / SPA",
        }
        return mapping[int(self)]


# --- disclosure matrix (supplier side) ------------------------------------------
#
# Each stage lists three buckets:
#   reveal: facts we will volunteer in the email at this stage
#   ask:    facts we will ASK the counterparty to reveal at this stage
#   hold:   facts we will deliberately withhold (and that the LLM must not leak)
#
# Tactics lists the specific levers active in this stage (BATNA framing,
# deadline, anchoring rule, non-price lever, commitment escalation).

SUPPLIER_DISCLOSURE_MATRIX: dict[NegotiationStage, dict[str, list[str]]] = {
    NegotiationStage.COLD_OUTREACH: {
        "reveal": [
            "our identity (company + sender name + title)",
            "commodity and quality spec family (e.g. ICUMSA 45, grade #2)",
            "approximate monthly volume BAND (e.g. '2,000-5,000 MT/month')",
            "destination region (continent, not port)",
            "incoterms interest (FOB and/or CFR — plural, to avoid anchoring)",
            "payment instrument family (DLC / SBLC) without tenor",
            "readiness to sign NCNDA on interest",
        ],
        "ask": [
            "indicative FOB and CFR levels (USD/MT) to the region",
            "minimum order quantity and typical parcel size",
            "lead time from LC confirmation to shipment",
            "accepted payment instruments and acceptable tenor",
            "typical loading port and monthly production capacity",
        ],
        "hold": [
            "exact monthly volume (give a band only)",
            "destination port / city (reveal country at stage 2, port at stage 4)",
            "end-buyer identity",
            "our target buy price or any USD/MT number",
            "our maximum tolerable LC tenor",
            "whether a deposit is available (reveal at stage 4)",
            "whether end-buyer LOI / proof-of-funds exists (offer 'on request' only)",
        ],
        "tactics": [
            "BATNA: hint at multiple origins under evaluation ('comparing two origins this week')",
            "Deadline: reference an origination cycle or shipment window, not a made-up '24h'",
            "Anchoring: NEVER state a USD/MT number. Ask them to anchor first.",
            "Commitment escalation (cheap ask): NCNDA-on-interest + indicative levels",
            "Non-price lever: mention willingness to consider tonnage upsize for pricing improvement",
        ],
    },
    NegotiationStage.FIRST_RESPONSE: {
        "reveal": [
            "a narrower volume band than stage 1 (e.g. '3,000-4,000 MT/month')",
            "destination country (still NOT the port)",
            "preferred loading window quarter / month range",
            "type of end-buyer (e.g. 'West African refinery', not the name)",
            "willingness to execute NCNDA before next exchange",
        ],
        "ask": [
            "full Soft Corporate Offer (SCO) on their letterhead",
            "acceptable payment-term RANGE (not just one point)",
            "inspection agency preference (SGS / Intertek / Cotecna)",
            "origination cycle / next available shipment slot",
            "whether they can tier price against tonnage",
        ],
        "hold": [
            "end-buyer identity",
            "destination port",
            "exact LC tenor we can support",
            "deposit percentage we can pre-pay (reveal at stage 4)",
            "any USD/MT price from our side",
        ],
        "tactics": [
            "BATNA: acknowledge their SCO as 'one of the offers on the desk this week'",
            "Deadline: align the next step to THEIR origination cycle to make it credible",
            "Anchoring: still do NOT quote. Request B&F or tiered matrix instead.",
            "Commitment escalation: NCNDA now, SCO on letterhead now",
            "Non-price lever: tonnage-for-price tier, LC tenor trade-off",
        ],
    },
    NegotiationStage.COUNTER_OFFER: {
        "reveal": [
            "our WORKING LEVEL in USD/MT, anchored to futures + basis methodology",
            "soft-commitment signal ('ready to issue LC within X working days on agreement')",
            "two non-price levers we can accept to defend their margin",
            "target loading window narrowed to a single month",
        ],
        "ask": [
            "acceptance at the working level, or a counter-counter with specific terms",
            "a tiered price-vs-volume matrix (B&F)",
            "confirmation of inspection agency and port",
            "proforma invoice draft at agreed price for review",
        ],
        "hold": [
            "end-buyer identity (still held until NCNDA executed)",
            "exact deposit percentage (reveal in stage 4 as a concession)",
            "ceiling price — NEVER reveal our maximum tolerance",
        ],
        "tactics": [
            "Anchoring: cite the exchange ticker and price explicitly, then the basis",
            "BATNA: 'this is the level at which we can execute this week' (credible, not threatening)",
            "Deadline: tie the working level to the futures close / weekly basis refresh",
            "Commitment escalation: proforma invoice draft, LC-readiness statement",
            "Non-price lever: 'we can look at deposit OR shorter LC tenor if you need to defend margin'",
        ],
    },
    NegotiationStage.TERMS_NEGOTIATION: {
        "reveal": [
            "destination port (now safe to disclose)",
            "deposit percentage we can pre-pay (if price holds)",
            "preferred inspection agency",
            "packaging spec preference",
            "demurrage tolerance at loadport",
        ],
        "ask": [
            "final delivery window confirmation",
            "packaging spec sign-off",
            "freight-split willingness (if CFR negotiation is hybrid)",
            "loading port nomination and laytime terms",
            "bank reference for LC issuance workflow",
        ],
        "hold": [
            "end-buyer legal identity until NCNDA counter-signed",
            "our maximum demurrage tolerance per day",
        ],
        "tactics": [
            "Anchoring: keep price fixed, trade only on non-price terms",
            "BATNA: 'our back-to-back buyer is holding the slot subject to these terms'",
            "Deadline: LC issuance timeline drives the cadence",
            "Commitment escalation: bank reference exchange, draft SPA review",
            "Non-price lever: freight split, demurrage, packaging upgrade trade-offs",
        ],
    },
    NegotiationStage.CLOSE: {
        "reveal": [
            "end-buyer legal name (under NCNDA)",
            "LC issuance bank and SWIFT routing",
            "signing authority and expected counter-signature date",
            "loading port nomination",
        ],
        "ask": [
            "draft SPA / signed proforma for final legal review",
            "banking instrument acceptance and pre-advice timing",
            "final commercial invoice format",
            "loading programme and vessel nomination window",
        ],
        "hold": [
            "internal margin and back-to-back buyer pricing (never disclosed)",
        ],
        "tactics": [
            "Anchoring: price is locked; do not reopen",
            "Deadline: LC issuance deadline is the hard stop",
            "Commitment escalation: full SPA + LC pre-advice",
            "Non-price lever: vessel nomination flexibility as final trade",
        ],
    },
}

# --- disclosure matrix (buyer side) ---------------------------------------------

BUYER_DISCLOSURE_MATRIX: dict[NegotiationStage, dict[str, list[str]]] = {
    NegotiationStage.COLD_OUTREACH: {
        "reveal": [
            "our identity and sourcing capability (origins we cover)",
            "commodity and spec family",
            "approximate monthly SUPPLY CAPACITY band",
            "incoterms interest (FOB / CFR plural)",
        ],
        "ask": [
            "their indicative BID levels (USD/MT) by origin",
            "their monthly demand band and destination port",
            "payment instruments they can issue and tenor",
            "inspection and packaging preferences",
        ],
        "hold": [
            "our supplier identity",
            "our supplier's quoted price",
            "our margin target",
            "our minimum acceptable sell price",
        ],
        "tactics": [
            "BATNA: signal multiple offtake conversations in parallel",
            "Anchoring: do NOT quote. Ask them to bid first.",
            "Commitment escalation: NCNDA on interest, LOI on firm enquiry",
            "Non-price lever: longer contract tenor, firmer offtake schedule",
        ],
    },
    NegotiationStage.FIRST_RESPONSE: {
        "reveal": [
            "narrower supply band",
            "origin country (not mill)",
            "typical loading cadence",
        ],
        "ask": [
            "firm LOI with monthly tonnage and price ceiling",
            "proof-of-funds or bank comfort letter",
            "preferred inspection agency and port",
            "willingness to accept tiered pricing by volume",
        ],
        "hold": [
            "our supplier identity and origin mill",
            "our buy price",
        ],
        "tactics": [
            "BATNA: 'one of several offtake conversations live this week'",
            "Commitment escalation: LOI + POF now",
            "Anchoring: still do not quote a sell price",
        ],
    },
    NegotiationStage.COUNTER_OFFER: {
        "reveal": [
            "our SELL level in USD/MT, anchored to market + margin methodology",
            "origin country and mill type (still not the mill name)",
            "loading window commitment subject to LC",
        ],
        "ask": [
            "acceptance at sell level or counter with specific terms",
            "LC draft / bank pre-advice timing",
            "final destination port and laytime terms",
        ],
        "hold": [
            "mill identity (until NCNDA)",
            "our buy price (never revealed)",
        ],
        "tactics": [
            "Anchoring: reference market benchmark + explainable basis",
            "Commitment escalation: LC pre-advice exchange",
            "Non-price lever: volume upsize for price improvement",
        ],
    },
    NegotiationStage.TERMS_NEGOTIATION: {
        "reveal": [
            "mill identity (under NCNDA)",
            "inspection and packaging acceptance",
            "loading port nomination",
        ],
        "ask": [
            "final LC format and issuance date",
            "demurrage and laytime acceptance",
            "signed draft SPA",
        ],
        "hold": [
            "our buy price and margin",
        ],
        "tactics": [
            "Anchoring: price locked; trade on non-price terms",
            "Commitment escalation: bank reference, draft SPA",
        ],
    },
    NegotiationStage.CLOSE: {
        "reveal": [
            "full supply chain under SPA",
            "LC issuance schedule",
        ],
        "ask": [
            "fully executed SPA + LC pre-advice",
            "final commercial invoice and shipping docs",
        ],
        "hold": [
            "internal margin (never disclosed)",
        ],
        "tactics": [
            "Deadline: LC issuance is the hard stop",
            "Commitment escalation: full execution",
        ],
    },
}


# --- context + guidance ---------------------------------------------------------


@dataclass
class NegotiationContext:
    """Everything the strategy engine needs to tailor a single email.

    ``intel`` is the running dossier of what we've learned about the
    counterparty (their claimed origin, last quoted price, last quoted
    incoterms, payment tolerance hints, etc.) — free-form JSON so new
    signals can be added without migrations.

    ``disclosed`` is the running audit trail of what we've TOLD them —
    used by the prompt to enforce 'do not repeat things they already
    know' and by the UI to show the user what's been leaked.
    """

    stage: NegotiationStage
    side: str = "supplier"  # "supplier" (we are buying) or "buyer" (we are selling)
    intel: dict[str, Any] = field(default_factory=dict)
    disclosed: dict[str, Any] = field(default_factory=dict)
    market_reference: dict[str, Any] | None = None
    supplier_quote: dict[str, Any] | None = None
    last_supplier_response: str | None = None

    def matrix(self) -> dict[str, list[str]]:
        if self.side == "buyer":
            return BUYER_DISCLOSURE_MATRIX[self.stage]
        return SUPPLIER_DISCLOSURE_MATRIX[self.stage]


def build_disclosure_guidance(ctx: NegotiationContext) -> dict[str, Any]:
    """Build the structured guidance block that will be injected into the LLM
    system prompt. Returns a dict the prompt builder can render deterministically.
    """
    matrix = ctx.matrix()
    return {
        "stage": int(ctx.stage),
        "stage_label": ctx.stage.label,
        "side": ctx.side,
        "reveal": matrix["reveal"],
        "ask": matrix["ask"],
        "hold": matrix["hold"],
        "tactics": matrix["tactics"],
        "known_intel": ctx.intel,
        "previously_disclosed": ctx.disclosed,
        "market_reference": ctx.market_reference,
        "supplier_quote": ctx.supplier_quote,
    }


def render_guidance_prompt(ctx: NegotiationContext) -> str:
    """Render the guidance as a compact text block suitable for inlining into
    a system prompt. Uses plain ascii bullets so there is no Markdown ambiguity.
    """
    g = build_disclosure_guidance(ctx)
    lines: list[str] = [
        f"NEGOTIATION CONTEXT — stage {g['stage']} of 5: {g['stage_label']} ({g['side']} side).",
        "REVEAL (you may mention these in the email):",
    ]
    lines += [f"  - {r}" for r in g["reveal"]]
    lines.append("ASK (include AT LEAST three of these as explicit questions):")
    lines += [f"  - {a}" for a in g["ask"]]
    lines.append("HOLD (NEVER mention these — they are leverage for later stages):")
    lines += [f"  - {h}" for h in g["hold"]]
    lines.append("TACTICS (apply these in the body of the email):")
    lines += [f"  - {t}" for t in g["tactics"]]
    if g["previously_disclosed"]:
        lines.append(
            "PREVIOUSLY DISCLOSED (do not repeat these facts as if new): "
            f"{', '.join(sorted(str(k) for k in g['previously_disclosed']))}."
        )
    if g["known_intel"]:
        lines.append(
            "KNOWN INTEL (use for targeting, but do not reveal we know these): "
            f"{', '.join(sorted(str(k) for k in g['known_intel']))}."
        )
    if g["market_reference"]:
        m = g["market_reference"]
        price = m.get("price_mt") or m.get("price")
        lines.append(
            "MARKET REFERENCE available: "
            f"{m.get('exchange') or 'exchange'} {m.get('ticker') or ''} "
            f"at ${price:.2f}/MT — use this to anchor any counter."
            if isinstance(price, (int, float))
            else f"MARKET REFERENCE available: {m.get('exchange')} {m.get('ticker')}"
        )
    return "\n".join(lines)


def next_stage(stage: NegotiationStage) -> NegotiationStage:
    """Clamp-and-advance. Stage 5 stays at 5."""
    return NegotiationStage(min(int(stage) + 1, int(NegotiationStage.CLOSE)))


def extract_intel_keys(supplier_response: str) -> dict[str, Any]:
    """Very conservative intel extractor for the follow-up flow.

    We deliberately keep this regex-based rather than LLM-based so the
    audit trail is deterministic. The LLM in ``follow_up_email`` still
    reads the full raw response — this is only the structured
    ``intel`` dossier that the UI surfaces.
    """
    import re

    out: dict[str, Any] = {}
    text = supplier_response or ""

    # Price quote: first USD/MT number in the reply.
    m = re.search(r"\$?\s*([0-9]{2,4}(?:\.[0-9]{1,2})?)\s*/?\s*(?:MT|USD/MT|per MT)", text, re.I)
    if m:
        try:
            out["quoted_price_usd_mt"] = float(m.group(1))
        except ValueError:
            pass

    # Incoterms.
    for term in ["FOB", "CFR", "CIF", "FCA", "DAP", "EXW"]:
        if re.search(rf"\b{term}\b", text):
            out.setdefault("quoted_incoterms", []).append(term)

    # Minimum order quantity.
    m = re.search(r"(?:MOQ|minimum\s+(?:order|quantity))[^0-9]{0,20}([0-9,]{3,})\s*MT", text, re.I)
    if m:
        try:
            out["min_order_mt"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Payment tolerance signals.
    if re.search(r"\bSBLC\b", text, re.I):
        out.setdefault("accepted_payment", []).append("SBLC")
    if re.search(r"\bDLC\b|\bdocumentary\s+LC\b|\bL/?C\s+at\s+sight\b", text, re.I):
        out.setdefault("accepted_payment", []).append("DLC")

    # Lead time.
    m = re.search(r"([0-9]{1,3})\s*(?:days?|working\s+days?)\s*(?:lead|after\s+LC)", text, re.I)
    if m:
        try:
            out["lead_time_days"] = int(m.group(1))
        except ValueError:
            pass

    return out
