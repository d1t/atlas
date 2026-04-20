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
    @abstractmethod
    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str: ...

    @abstractmethod
    async def json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]: ...


class MockLLM(LLMClient):
    """Deterministic, offline LLM for dev and CI.

    Produces plausible, structured outputs based on the prompt content.
    Not a real model — just enough to let the UI and pipeline work.
    """

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        prompt = (system + "\n" + user).lower()
        if "ncnda" in prompt or "non-circumvention" in prompt:
            return _mock_ncnda(user)
        if "spa" in prompt or "sale and purchase" in prompt:
            return _mock_spa(user)
        if "loi" in prompt or "letter of intent" in prompt:
            return _mock_loi(user)
        if "imfpa" in prompt or "fpa" in prompt or "fee protection" in prompt:
            return _mock_imfpa(user)
        if "outreach" in prompt or "email" in prompt:
            return _mock_outreach(user)
        if "classify" in prompt or "classification" in prompt:
            return json.dumps(_mock_classification(user))
        if "supplier" in prompt and "list" in prompt:
            return json.dumps(_mock_suppliers(user))
        return f"[mock-llm] {user[:200]}..."

    async def json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        text = await self.complete(system, user, max_tokens)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


class OpenAILLM(LLMClient):
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
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider == "openai" and settings.openai_api_key:
        return OpenAILLM(settings.openai_api_key, settings.openai_model)
    if provider == "anthropic" and settings.anthropic_api_key:
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
    commodity = _extract(user, "commodity") or "the specified commodity"
    company = _extract(user, "company") or "your company"
    return (
        f"Subject: Potential Partnership — {commodity.title()} Sourcing\n\n"
        f"Dear {company} team,\n\n"
        f"My name is [Your Name] from Atlas Trade. We work with verified end buyers of "
        f"{commodity} and are exploring reliable supply partners. Based on public sources "
        f"we understand you are an established supplier in this space.\n\n"
        f"If relevant, we would appreciate a brief call to align on current availability, "
        f"indicative pricing (FOB/CIF), and standard Incoterms. We sign NCNDA prior to "
        f"any commercial disclosure.\n\n"
        f"Looking forward to your response.\n\n"
        f"Best regards,\n[Your Name]\nAtlas Trade\n"
    )


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
    m = re.search(rf"{key}\s*[:=]\s*([\w\s\-]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None
