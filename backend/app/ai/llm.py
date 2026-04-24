"""LLM abstraction layer.

Supports OpenAI, Anthropic, and a deterministic mock provider so the
system works end-to-end without API keys (PRD requirement: lightweight
and fast dev loop). All providers share a single interface.
"""
from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

from app.core.config import get_settings


class LLMClient(ABC):
    provider_name: str = "unknown"

    @abstractmethod
    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str: ...

    @abstractmethod
    async def json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]: ...


class MockLLM(LLMClient):
    """Deterministic, offline LLM for dev and CI.

    Produces plausible, structured outputs based on the prompt content.
    Not a real model — just enough to let the UI and pipeline work.
    """

    provider_name = "mock"

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        prompt = (system + "\n" + user).lower()
        # Structured JSON responses first — these are keyed by concrete tasks, not
        # generic words like "email" that appear in field lists.
        if "classify" in prompt or "classification" in prompt:
            return json.dumps(_mock_classification(user))
        if "supplier discovery" in prompt or (
            "supplier" in prompt and "json" in prompt and "array" in prompt
        ):
            return json.dumps(_mock_suppliers(user))
        # Email templates must be matched BEFORE generic doc-type keywords because the
        # email system prompts themselves reference other doc types (e.g. "execute an
        # NCNDA on interest", "draft SPA within 48 hours") which would otherwise hijack
        # the routing and return the wrong mock.
        # Match follow-up BEFORE counter-offer / cold-outreach because those
        # prompts reference counter-offer / outreach rules verbatim (for the
        # stage-3 anchoring guidance) and would otherwise steal the routing.
        if "follow_up_email" in prompt or "stage-aware follow-up" in prompt:
            return _mock_follow_up(user)
        if "counter-offer" in prompt or "counter_offer" in prompt or "counter offer" in prompt:
            return _mock_counter_offer(user)
        if (
            "cold outreach email" in prompt
            or "outreach email" in prompt
            or "draft an email" in prompt
            or "write an email" in prompt
        ):
            return _mock_outreach(user)
        # Legal document templates.
        if "ncnda" in prompt or "non-circumvention" in prompt:
            return _mock_ncnda(user)
        if "sale and purchase" in prompt or "spa" in prompt:
            return _mock_spa(user)
        if "letter of intent" in prompt or "loi" in prompt:
            return _mock_loi(user)
        if "imfpa" in prompt or "fee protection" in prompt or "fpa" in prompt:
            return _mock_imfpa(user)
        return f"[mock-llm] {user[:200]}..."

    async def json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        text = await self.complete(system, user, max_tokens)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


class OpenAILLM(LLMClient):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai package not installed; pip install openai") from exc
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        resp = await self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system + "\nRespond ONLY with valid JSON."},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content}


class AnthropicLLM(LLMClient):
    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("anthropic package not installed; pip install anthropic") from exc
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [block.text for block in resp.content if getattr(block, "type", "") == "text"]
        return "".join(parts)

    async def json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        text = await self.complete(
            system + "\nRespond ONLY with a single valid JSON object. No prose.",
            user,
            max_tokens,
        )
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"raw": text}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"raw": text}


@lru_cache
def get_llm() -> LLMClient:
    """Resolve the LLM backend.

    Precedence:
    1. Explicit LLM_PROVIDER + matching key → honour it.
    2. LLM_PROVIDER=mock (default) but a real key present → auto-upgrade to
       that provider. OpenAI wins if both keys are set, since the OpenAI
       adapter has native JSON-mode support and is the default recommendation.
    3. Otherwise → deterministic offline mock.
    """
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider == "openai" and settings.openai_api_key:
        return OpenAILLM(settings.openai_api_key, settings.openai_model)
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model)
    if provider in ("mock", "") and settings.openai_api_key:
        return OpenAILLM(settings.openai_api_key, settings.openai_model)
    if provider in ("mock", "") and settings.anthropic_api_key:
        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model)
    return MockLLM()


# ---------------- Mock generators (deterministic) ----------------

def _seeded(text: str, n: int) -> int:
    h = int(hashlib.sha1(text.encode()).hexdigest(), 16)
    return h % n


def _mock_suppliers(user: str) -> list[dict]:
    commodity = _extract(user, "commodity") or "sugar"
    country = _extract(user, "country") or "Brazil"
    names = [
        f"{country} {commodity.title()} Industries",
        f"Grupo {commodity.title()} do {country}",
        f"{country}Trade Agro Ltd",
        f"Atlas {commodity.title()} Exports",
        f"{country} Commodity Partners",
        f"Global {commodity.title()} Mills SA",
        f"{country} Agri Cooperative",
        f"{commodity.title()}Corp International",
    ]
    types = ["mill", "trader", "broker"]
    return [
        {
            "name": n,
            "type": types[i % 3],
            "country": country,
            "commodity": commodity.lower(),
            "website": f"https://{n.lower().replace(' ', '').replace(',', '')[:24]}.example.com",
            "email": f"sales@{n.lower().replace(' ', '')[:16]}.com",
            "description": f"{n} is a {types[i % 3]} operating in {country} "
            f"specializing in {commodity} exports.",
            "source": "mock-discovery",
        }
        for i, n in enumerate(names)
    ]


def _mock_classification(user: str) -> dict:
    u = user.lower()
    if "mill" in u or "factory" in u or "processing" in u:
        t = "mill"
    elif "broker" in u or "intermediary" in u or "agent" in u:
        t = "broker"
    elif "trading" in u or "trader" in u or "merchant" in u:
        t = "trader"
    else:
        t = ["mill", "trader", "broker"][_seeded(user, 3)]

    confidence = 0.55 + (_seeded(user, 40) / 100)
    reasoning = f"Classified as {t} based on textual signals in the description."
    return {"type": t, "confidence": round(confidence, 2), "reasoning": reasoning}


def _mock_outreach(user: str) -> str:
    """Cold outreach. Deliberately contains NO offer price — rule #1 of negotiation
    is 'don't anchor yourself against yourself'. We ask the supplier to quote first.

    Stage-1 disclosure rules (mirrors ``document_generation.DOC_SYSTEMS["outreach_email"]``):

    * NEVER emit the exact target tonnage; use opportunity_disclosure.volume_disclosure
      (a band) verbatim, or "vessel-scale parcel (exploratory)" when missing.
    * NEVER name a destination port; use opportunity_disclosure.geo_disclosure (region).
    * Frame as a SINGLE deal — no "12-month rolling" / "monthly programme" language.
    * Honest BATNA framing: acknowledge multi-origin evaluation when applicable.
    * Phrase asks as real questions (≥3 '?' chars) so the supplier must reply.
    * If sender.company_name is empty, omit the company-name clause entirely
      rather than leaking a placeholder.
    """
    ctx = _parse_inputs(user)
    sender = ctx.get("sender") or {}
    supplier = ctx.get("supplier") or {}
    deal = ctx.get("deal") or {}
    disclosure = ctx.get("opportunity_disclosure") or {}

    commodity = (
        supplier.get("commodity")
        or deal.get("commodity")
        or disclosure.get("commodity")
        or _extract(user, "commodity")
        or "the specified commodity"
    )
    supplier_name = supplier.get("name") or _extract(user, "company") or "your company"
    supplier_country = supplier.get("country") or "your region"

    volume_band = (
        disclosure.get("volume_disclosure")
        or "vessel-scale parcel (exploratory)"
    )
    geo = disclosure.get("geo_disclosure") or "the destination region"
    evaluating_origins = bool(disclosure.get("evaluating_origins", True))

    sender_name = sender.get("full_name") or ""
    sender_company = sender.get("company_name") or ""
    sender_title = sender.get("title") or "Trader"
    sender_email = sender.get("email") or ""
    sender_phone = sender.get("phone") or ""

    spec_guidance = _default_spec_for(commodity)

    # Identification clause: include the company only if we have one.
    if sender_name and sender_company:
        intro = (
            f"I'm {sender_name} at {sender_company}. We structure physical "
            f"{commodity} supply for end-buyers under bank-instrument-backed "
            f"contracts."
        )
    elif sender_name:
        intro = (
            f"I'm {sender_name}, structuring physical {commodity} supply for "
            f"end-buyers under bank-instrument-backed contracts."
        )
    else:
        intro = (
            f"Reaching out regarding physical {commodity} supply, structured "
            f"under bank-instrument-backed contracts."
        )

    batna_line = (
        "We are evaluating two origins for this enquiry this week and would "
        "like to include you in the comparison."
        if evaluating_origins
        else "We would like to include you in this enquiry."
    )

    signature_lines = [sender_name] if sender_name else []
    if sender_company:
        signature_lines.append(f"{sender_title}, {sender_company}")
    else:
        signature_lines.append(sender_title)
    if sender_email:
        signature_lines.append(sender_email)
    if sender_phone:
        signature_lines.append(sender_phone)

    return (
        f"Subject: {commodity.title()} sourcing enquiry — {volume_band} "
        f"to {geo}\n\n"
        f"Dear {supplier_name} team,\n\n"
        f"{intro}\n\n"
        f"Your name came up as an established producer in {supplier_country}. "
        f"{batna_line}\n\n"
        f"Single-cargo enquiry. Indicative parameters:\n"
        f"- Volume: {volume_band}\n"
        f"{spec_guidance}\n"
        f"- Incoterms interest: FOB and CFR (please indicate both)\n"
        f"- Payment instrument family: DLC at sight or SBLC, top-50 bank "
        f"(tenor on NCNDA)\n"
        f"- Loading window: subject to your origination cycle and our "
        f"LC issuance timing\n\n"
        f"To progress, could you share:\n"
        f"- Your indicative FOB and CFR levels (USD/MT) to {geo}?\n"
        f"- Minimum order quantity and typical lead time from LC confirmation?\n"
        f"- Accepted payment instruments and acceptable tenor?\n\n"
        f"We are ready to execute an NCNDA on interest, and end-buyer LOI / "
        f"proof of funds is available on request. Happy to take a 20-minute "
        f"call this week.\n\n"
        f"Best regards,\n" + "\n".join(signature_lines) + "\n"
    )


def _mock_counter_offer(user: str) -> str:
    """Counter-offer email. Price IS included here, anchored to the live futures
    market reference (from the Yahoo Finance integration) when provided.
    """
    ctx = _parse_inputs(user)
    sender = ctx.get("sender") or {}
    supplier = ctx.get("supplier") or {}
    deal = ctx.get("deal") or {}
    mkt = ctx.get("market_reference") or {}
    quote = ctx.get("supplier_quote") or {}

    commodity = (
        supplier.get("commodity")
        or deal.get("commodity")
        or _extract(user, "commodity")
        or "the specified commodity"
    )
    supplier_name = supplier.get("name") or "your team"
    volume_mt = deal.get("volume_mt")
    incoterms = deal.get("incoterms") or "CFR"

    supplier_price = _as_float(quote.get("price_mt") or quote.get("price"))
    market_price = _as_float(mkt.get("price_mt") or mkt.get("price"))
    ticker = mkt.get("ticker") or "ICE futures"
    exchange = mkt.get("exchange") or "ICE"

    # Counter at 3.5% below supplier quote, floored at market + a 5% basis.
    if supplier_price:
        counter = supplier_price * 0.965
        if market_price:
            counter = max(counter, market_price * 1.05)
        counter_str = f"${counter:,.2f}/MT"
        anchor_line = (
            f"Referencing {exchange} {ticker} at ${market_price:,.2f}/MT with a working "
            f"basis to cover freight and LC finance, "
            if market_price
            else ""
        )
        price_block = (
            f"{anchor_line}our working level on this enquiry is {counter_str} {incoterms}, "
            f"against your indication of ${supplier_price:,.2f}/MT."
        )
    else:
        price_block = (
            f"We would like to see a CFR level that reflects current {exchange} "
            f"{ticker} fundamentals plus a reasonable freight-and-finance basis. Please "
            f"share your basis rationale and we will revert with a firm working level."
        )

    volume_line = (
        f"Confirmed volume: {int(volume_mt):,} MT/month over a 12-month rolling "
        f"contract, with upside on performance."
        if isinstance(volume_mt, (int, float)) and volume_mt > 0
        else "Volume as previously discussed; we can scale on performance."
    )

    sender_name = sender.get("full_name") or "[Your Name]"
    sender_company = sender.get("company_name") or "[Your Company]"
    sender_title = sender.get("title") or "Trader"
    sender_email = sender.get("email") or ""
    sender_phone = sender.get("phone") or ""
    signature_lines = [sender_name, f"{sender_title}, {sender_company}"]
    if sender_email:
        signature_lines.append(sender_email)
    if sender_phone:
        signature_lines.append(sender_phone)

    return (
        f"Subject: Re: {commodity.title()} — working level and path to LC\n\n"
        f"Dear {supplier_name},\n\n"
        f"Thank you for the indication — the specs align with what our end-buyer is "
        f"looking for. To move to LC stage, we need the price to reflect current market.\n\n"
        f"{price_block}\n\n"
        f"{volume_line}\n\n"
        f"To make the number workable on your side, we can offer either (a) a "
        f"non-refundable 2% performance deposit on LC issuance, or (b) DLC at sight on "
        f"a 30-day tenor instead of 60. Either improves your cost of capital and gives "
        f"you room to tighten your offer.\n\n"
        f"If the counter works, we will issue draft SPA within 48 hours. If you prefer "
        f"to hold closer to your original level, propose a volume upsize or tighter "
        f"spec and we will re-run the math.\n\n"
        f"Best regards,\n" + "\n".join(signature_lines) + "\n"
    )


def _mock_follow_up(user: str) -> str:
    """Stage-aware follow-up. Reads the `negotiation` block from the injected
    inputs and produces an email that obeys the disclosure matrix for the
    current stage. Stage 3 is the only stage that contains a price.
    """
    ctx = _parse_inputs(user)
    sender = ctx.get("sender") or {}
    supplier = ctx.get("supplier") or {}
    deal = ctx.get("deal") or {}
    neg = ctx.get("negotiation") or {}
    mkt = ctx.get("market_reference") or neg.get("market_reference") or {}
    quote = ctx.get("supplier_quote") or {}

    stage = int(neg.get("stage") or 2)
    commodity = (
        supplier.get("commodity")
        or deal.get("commodity")
        or _extract(user, "commodity")
        or "the commodity"
    )
    supplier_name = supplier.get("name") or "your team"
    sender_name = sender.get("full_name") or "[Your Name]"
    sender_company = sender.get("company_name") or "[Your Company]"
    sender_title = sender.get("title") or "Trader"
    sender_email = sender.get("email") or ""
    sender_phone = sender.get("phone") or ""
    signature_lines = [sender_name, f"{sender_title}, {sender_company}"]
    if sender_email:
        signature_lines.append(sender_email)
    if sender_phone:
        signature_lines.append(sender_phone)
    signature = "\n".join(signature_lines)

    if stage >= 5:
        body = (
            f"Subject: {commodity.title()} — SPA readiness & LC pre-advice\n\n"
            f"Dear {supplier_name},\n\n"
            f"Thank you for confirming terms. We are ready to progress to SPA and LC "
            f"pre-advice this week.\n\n"
            f"Next steps:\n"
            f"- Can you share a draft SPA on your standard template for legal review?\n"
            f"- What is the earliest LC pre-advice date your bank can accommodate?\n"
            f"- Please confirm the loading programme and vessel nomination window.\n\n"
            f"Our end-buyer's NCNDA is executed and we are clear to disclose identity on "
            f"your counter-signed copy. Target execution aligned to the current shipment "
            f"window.\n\n"
            f"Best regards,\n{signature}\n"
        )
    elif stage == 4:
        body = (
            f"Subject: {commodity.title()} — terms confirmation & SPA draft\n\n"
            f"Dear {supplier_name},\n\n"
            f"Price is workable. To move to SPA we need to settle the non-price terms "
            f"and lock the loading window.\n\n"
            f"Asks:\n"
            f"- Can you confirm the final delivery window and laytime / demurrage terms?\n"
            f"- Which packaging spec suits your line (50kg PP, 1 MT jumbo, bulk)?\n"
            f"- Are you open to a freight-split on CFR, or holding fully on your side?\n"
            f"- Please share a bank reference so we can align on LC format.\n\n"
            f"On our side, we can confirm destination port and a 2% non-refundable "
            f"performance deposit on LC issuance. Timeline is driven by LC-issuance "
            f"cutoff this cycle.\n\n"
            f"Best regards,\n{signature}\n"
        )
    elif stage == 3:
        supplier_price = _as_float(quote.get("price_mt") or quote.get("price"))
        market_price = _as_float(mkt.get("price_mt") or mkt.get("price"))
        ticker = mkt.get("ticker") or "ICE futures"
        exchange = mkt.get("exchange") or "ICE"
        if supplier_price:
            counter = supplier_price * 0.965
            if market_price:
                counter = max(counter, market_price * 1.05)
            anchor = (
                f"Referencing {exchange} {ticker} at ${market_price:,.2f}/MT with a "
                f"working basis for freight and LC finance, "
                if market_price
                else ""
            )
            price_block = (
                f"{anchor}our working level is ${counter:,.2f}/MT CFR against your "
                f"indication."
            )
        else:
            price_block = (
                f"Anchored to {exchange} {ticker} plus a transparent freight-and-finance "
                f"basis, we would like to see the CFR level reflect current market."
            )
        body = (
            f"Subject: {commodity.title()} — working level & path to LC\n\n"
            f"Dear {supplier_name},\n\n"
            f"Thanks for the SCO. Specs and cycle are aligned; to move to LC stage we "
            f"need the price to reflect current market.\n\n"
            f"{price_block}\n\n"
            f"To protect your margin at that level, we can offer either (a) a 2% "
            f"non-refundable performance deposit on LC issuance, or (b) shorter DLC "
            f"tenor (30 days instead of 60). Either improves your cost of capital.\n\n"
            f"Questions:\n"
            f"- Can you confirm acceptance at the working level, or counter with a "
            f"tiered matrix against tonnage?\n"
            f"- Shall we draft proforma invoice at agreed price for review?\n"
            f"- Which inspection agency do you prefer — SGS, Intertek, or Cotecna?\n\n"
            f"We are comparing two origins this cycle; happy to progress with whoever "
            f"lands first on a workable level.\n\n"
            f"Best regards,\n{signature}\n"
        )
    else:  # stage == 2
        body = (
            f"Subject: {commodity.title()} — next steps on your SCO\n\n"
            f"Dear {supplier_name},\n\n"
            f"Thank you for the indicative offer. To progress we'd like to firm up a few "
            f"points before exchanging banking details.\n\n"
            f"Asks:\n"
            f"- Can you share a full SCO on letterhead covering spec, packaging, and "
            f"loading port?\n"
            f"- What is your acceptable payment-term RANGE (instrument and tenor)?\n"
            f"- Which inspection agency do you prefer at load — SGS, Intertek, or "
            f"Cotecna?\n"
            f"- When is your next origination slot and typical cycle length?\n\n"
            f"On our side, we are ready to counter-sign NCNDA this week and narrow the "
            f"destination to a specific country on your confirmation. We are reviewing a "
            f"parallel origin, so turnaround on the full SCO helps us hold the slot for "
            f"you.\n\n"
            f"Best regards,\n{signature}\n"
        )
    return body


def _as_float(x: object) -> float | None:
    try:
        if x is None:
            return None
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _default_spec_for(commodity: str) -> str:
    c = (commodity or "").lower()
    if "sugar" in c:
        return "- Spec: ICUMSA 45 refined or VHP raw (please confirm your grades)"
    if "wheat" in c:
        return "- Spec: milling grade, protein ≥ 11.5%, moisture ≤ 13.5%"
    if "corn" in c or "maize" in c:
        return "- Spec: feed grade #2 yellow, moisture ≤ 14%"
    if "coffee" in c:
        return "- Spec: Arabica or Robusta, grade to be confirmed"
    if "cocoa" in c:
        return "- Spec: main-crop beans, ≤ 8% moisture, fermented"
    if "cotton" in c:
        return "- Spec: Middling 1-1/8\" or equivalent, micronaire 3.8–4.9"
    if "soy" in c:
        return "- Spec: non-GMO if available, protein ≥ 34%, moisture ≤ 13%"
    return "- Spec: please share your standard export grades and certifications"


def _parse_inputs(user: str) -> dict:
    """Best-effort extraction of the JSON block we inject into the prompt."""
    match = re.search(r"\{.*\}", user, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _mock_ncnda(user: str) -> str:
    return (
        "# NON-CIRCUMVENTION, NON-DISCLOSURE AGREEMENT (NCNDA)\n\n"
        "This Agreement is entered into by the undersigned Parties, herein referred to as "
        "Party A and Party B, on the Effective Date below.\n\n"
        "## 1. Non-Circumvention\n"
        "Each Party agrees not to circumvent, avoid or bypass the other Party, directly or "
        "indirectly, in any transaction originating from introductions made under this "
        "Agreement, for a period of three (3) years.\n\n"
        "## 2. Non-Disclosure\n"
        "Neither Party shall disclose to any third party the identity of counterparties, "
        "pricing, volumes, or any other confidential information exchanged.\n\n"
        "## 3. Term\n"
        "This Agreement shall remain in force for thirty-six (36) months from the "
        "Effective Date.\n\n"
        "## 4. Governing Law\n"
        "This Agreement shall be governed by the laws of England and Wales.\n\n"
        f"Context / Deal Inputs:\n```\n{user[:500]}\n```\n\n"
        "Signed: ____________________   Date: __________\n"
        "Signed: ____________________   Date: __________\n"
    )


def _mock_spa(user: str) -> str:
    return (
        "# SALE AND PURCHASE AGREEMENT (SPA)\n\n"
        "This Sale and Purchase Agreement is made between the Seller and the Buyer.\n\n"
        "## 1. Commodity & Specifications\n"
        "The Seller agrees to sell, and the Buyer agrees to purchase, the commodity "
        "described in Annex A, meeting the specifications therein.\n\n"
        "## 2. Quantity & Tolerance\n"
        "Total contract quantity with ±5% tolerance at Seller's option.\n\n"
        "## 3. Price & Incoterms\n"
        "Price per metric ton as defined in the commercial terms. Delivery per Incoterms "
        "2020 (see Annex A).\n\n"
        "## 4. Payment\n"
        "Payment by irrevocable, confirmed, transferable Letter of Credit at sight, issued "
        "by a top 50 world bank acceptable to the Seller.\n\n"
        "## 5. Inspection\n"
        "SGS or equivalent at loading port, final at loading, costs shared 50/50.\n\n"
        "## 6. Force Majeure\n"
        "Standard ICC 2020 Force Majeure Clause applies.\n\n"
        "## 7. Arbitration\n"
        "Disputes resolved via ICC Arbitration, London, English language.\n\n"
        f"## Annex A — Commercial Terms\n```\n{user[:800]}\n```\n\n"
        "Signed: _______________  Seller\n"
        "Signed: _______________  Buyer\n"
    )


def _mock_loi(user: str) -> str:
    return (
        "# LETTER OF INTENT (LOI)\n\n"
        "To Whom It May Concern,\n\n"
        "We, the undersigned Buyer, hereby confirm with full corporate authority and legal "
        "responsibility that we are ready, willing and able to purchase the commodity under "
        "the following terms, subject to agreed contract (SPA):\n\n"
        f"```\n{user[:700]}\n```\n\n"
        "This LOI is non-binding and subject to the execution of a mutually acceptable SPA. "
        "Proof of Funds available upon request under NCNDA.\n\n"
        "Signed: _______________  Date: __________\n"
        "Authorized Representative\n"
    )


def _mock_imfpa(user: str) -> str:
    return (
        "# IRREVOCABLE MASTER FEE PROTECTION AGREEMENT (IMFPA)\n\n"
        "This Agreement protects the rights and commissions of all intermediaries listed "
        "below for transactions resulting from their introductions.\n\n"
        "## 1. Fee Structure\n"
        "Commissions payable per metric ton, as listed in Schedule 1, deducted and paid "
        "automatically via the Paymaster upon each shipment / LC payment.\n\n"
        "## 2. Irrevocability\n"
        "This Agreement is irrevocable for the duration of the underlying contract, "
        "including all extensions, renewals and add-ons.\n\n"
        "## 3. Payment Instructions\n"
        "Fees shall be wired to the bank accounts designated in Schedule 1 within 48 hours "
        "of each payment received by the Seller.\n\n"
        f"## Schedule 1 — Inputs\n```\n{user[:600]}\n```\n\n"
        "Signed by all Parties below:\n____________  ____________  ____________\n"
    )


def _extract(text: str, key: str) -> str | None:
    m = re.search(rf"{key}\s*[:=]\s*([^\n,;]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None
