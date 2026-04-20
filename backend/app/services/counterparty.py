"""Module 2: Counterparty Intelligence Engine.

Combines:
  * LLM classification (mill / trader / broker)
  * Rule-based risk scoring (0-100)
  * Red-flag detection

Risk model is deliberately simple and auditable — PRD requires human-in-the-loop,
so every adjustment to the score carries a reason.
"""
from __future__ import annotations

import re

from app.ai import get_llm
from app.models.supplier import Supplier

GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "aol.com",
    "protonmail.com",
    "mail.ru",
    "yandex.com",
    "qq.com",
    "163.com",
}


class CounterpartyService:
    def __init__(self) -> None:
        self.llm = get_llm()

    async def classify(self, supplier: Supplier) -> dict:
        system = (
            "You classify counterparties in commodity trading. Categories: mill, trader, "
            "broker. Return JSON with keys: type, confidence (0-1), reasoning."
        )
        user = (
            f"Name: {supplier.name}\n"
            f"Country: {supplier.country or 'unknown'}\n"
            f"Commodity: {supplier.commodity or 'unknown'}\n"
            f"Description: {supplier.description or 'n/a'}\n"
            f"Website: {supplier.website or 'n/a'}\n"
            "Classify this counterparty."
        )
        result = await self.llm.json(system, user, max_tokens=300)
        t = (result.get("type") or "unknown").lower()
        if t not in {"mill", "trader", "broker", "unknown"}:
            t = "unknown"
        confidence = float(result.get("confidence", 0.5) or 0.5)
        reasoning = str(result.get("reasoning") or "")
        return {"type": t, "confidence": max(0.0, min(1.0, confidence)), "reasoning": reasoning}

    def score(self, supplier: Supplier) -> dict:
        """Rule-based risk & credibility scoring. Pure function — easy to test."""
        credibility = 50
        risk = 50
        flags: list[str] = []

        email_domain = self._email_domain(supplier.email)
        if email_domain in GENERIC_EMAIL_DOMAINS:
            flags.append("generic_email_domain")
            credibility -= 20
            risk += 15

        if not supplier.website:
            flags.append("no_website")
            credibility -= 15
            risk += 10
        elif supplier.website and email_domain:
            site_host = self._host(supplier.website)
            if site_host and email_domain not in site_host and site_host not in email_domain:
                flags.append("email_website_mismatch")
                credibility -= 10
                risk += 8

        if not supplier.country:
            flags.append("no_country")
            credibility -= 5
            risk += 5

        desc = (supplier.description or "").lower()
        if any(kw in desc for kw in ["guaranteed", "best price", "too good", "no lc needed"]):
            flags.append("suspicious_language")
            credibility -= 15
            risk += 20

        if supplier.type == "mill":
            credibility += 10
        elif supplier.type == "broker":
            risk += 5

        credibility = max(0, min(100, credibility))
        risk = max(0, min(100, risk))

        return {"credibility_score": credibility, "risk_score": risk, "red_flags": flags}

    def _email_domain(self, email: str | None) -> str | None:
        if not email or "@" not in email:
            return None
        return email.split("@", 1)[1].strip().lower()

    def _host(self, url: str | None) -> str | None:
        if not url:
            return None
        m = re.search(r"https?://([^/]+)", url)
        host = (m.group(1) if m else url).lower()
        return host.removeprefix("www.")
