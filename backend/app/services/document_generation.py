"""Module 4: Document Generation Engine.

LLM-templating for trade docs. Structured inputs -> editable Markdown output,
with optional DOCX export.
"""
from __future__ import annotations

import io
import json
from typing import Any

from docx import Document as DocxDocument

from app.ai import get_llm

_INPUT_GUIDE = (
    "The inputs JSON may contain: "
    "`sender` (the writer: full_name, company_name, title, email, phone), "
    "`supplier` (recipient: name, country, commodity, email, website, type), "
    "`deal` (title, commodity, volume_mt, buy_price, sell_price, freight_estimate, "
    "incoterms, currency, structure). Always use sender.full_name and sender.company_name "
    "in the signature and greeting — never use placeholders like '[Your Name]' or 'Atlas'. "
    "Address the supplier by supplier.name. Reference the actual commodity from "
    "supplier.commodity or deal.commodity, never 'the specified commodity'."
)

DOC_SYSTEMS = {
    "outreach_email": (
        "You are a senior commodity trader writing a cold outreach email to a potential "
        "supplier. This is the FIRST message — the goal is to earn a reply, not to close "
        "a deal. "
        f"{_INPUT_GUIDE} "
        "NEGOTIATION RULES (non-negotiable): "
        "(1) Do NOT quote any offer price, target price, bid price, or total deal value. "
        "Never invent a USD/MT or USD/lb number. Pricing is decided after the supplier "
        "responds — leading with a price weakens the buyer's position. "
        "(2) If inputs contain buy_price or sell_price, treat them as INTERNAL ONLY and "
        "do not mention them. "
        "(3) Anchor on SPECS, not price: monthly volume, quality spec (e.g. ICUMSA 45 for "
        "sugar, grade #2 for corn), packaging (jumbo bags / 50kg / bulk), destination port, "
        "desired first-shipment window, incoterms preference (FOB / CFR / CIF). "
        "(4) Ask the supplier for THEIR indicative pricing: FOB and CFR to destination, "
        "minimum order quantity, lead time from LC confirmation, payment terms accepted "
        "(SBLC, DLC at sight, etc). "
        "(5) Signal seriousness without committing: mention readiness to execute NCNDA on "
        "interest, and that end-buyer LOI or proof-of-funds is available on request. "
        "(6) No filler, no hype, no emojis. Do not praise the supplier's reputation in "
        "more than one short clause. "
        "STRUCTURE (follow exactly): subject line that names commodity + monthly volume + "
        "destination country; one-line identification of sender.company_name and its "
        "activity; one-line reason this supplier is relevant (reference their country / "
        "mill / product if known); a short bulleted or line-separated spec block with the "
        "requirements from rule 3; the explicit ask from rule 4; the NCNDA readiness "
        "line from rule 5; soft close offering a 20-minute call this week. "
        "Under 180 words. Plain text (not Markdown). Sign off with the sender's full "
        "name, title, company, email, and phone on separate lines."
    ),
    "counter_offer_email": (
        "You are a senior commodity trader writing a counter-offer email in response to a "
        "supplier's quote. "
        f"{_INPUT_GUIDE} "
        "Additional context: inputs may include `market_reference` "
        "(exchange, ticker, price in USD/MT, source) and `supplier_quote` "
        "(supplier's offered price in USD/MT, incoterms, payment terms). "
        "NEGOTIATION RULES: "
        "(1) This IS the email where a price appears — but it must be JUSTIFIED, not a "
        "random lowball. If market_reference is provided, explicitly anchor the counter "
        "to that futures price plus a transparent basis: 'based on ICE SB=F at "
        "$<price>/MT plus a $<basis>/MT freight-and-finance basis, our working level is "
        "$<counter>/MT CFR <port>'. "
        "(2) If market_reference is not provided, anchor to prevailing market context in "
        "general terms and request the supplier's basis rationale. "
        "(3) The counter should sit BELOW the midpoint between market reference and the "
        "supplier's quote — rule of thumb 3-5% below the supplier quote, adjusted by "
        "volume and payment terms. "
        "(4) Give the supplier two levers they can use to justify a smaller concession: "
        "willingness to pre-pay a deposit, or to accept DLC at sight on a shorter tenor. "
        "This protects the price without looking stubborn. "
        "(5) Never apologise for the counter. Never signal time pressure on the buyer "
        "side. Frame it as 'working level at which we can execute LC'. "
        "(6) Close with a concrete next step: either acceptance at the counter, or a "
        "counter-counter with specs the supplier can improve (tonnage upsize, tenor, "
        "ICUMSA grade). "
        "Under 200 words. Plain text. Sign off as in the outreach email."
    ),
    "spa_buyer": (
        "You are a commodity trade lawyer. Generate a complete, professional SALE AND "
        "PURCHASE AGREEMENT (SPA) from the buyer's perspective using the provided inputs. "
        "Output well-structured Markdown with numbered clauses. "
        f"{_INPUT_GUIDE} "
        "In the signature block, name the buyer as sender.company_name and the seller as "
        "supplier.name."
    ),
    "spa_supplier": (
        "You are a commodity trade lawyer. Generate a complete, professional SALE AND "
        "PURCHASE AGREEMENT (SPA) from the supplier/seller's perspective using the provided "
        "inputs. Output well-structured Markdown with numbered clauses. "
        f"{_INPUT_GUIDE} "
        "In the signature block, name the seller as sender.company_name and the buyer as "
        "supplier.name."
    ),
    "ncnda": (
        "You are a commodity trade lawyer. Draft a professional NON-CIRCUMVENTION, "
        "NON-DISCLOSURE AGREEMENT (NCNDA) using the inputs. Standard 3-year term unless "
        "overridden. Markdown output. "
        f"{_INPUT_GUIDE} "
        "Party A is sender.company_name, Party B is supplier.name. Include real "
        "signature blocks."
    ),
    "fpa": (
        "You are a commodity trade lawyer. Draft a FEE PROTECTION AGREEMENT (FPA) using "
        "the inputs. Markdown output. "
        f"{_INPUT_GUIDE}"
    ),
    "imfpa": (
        "You are a commodity trade lawyer. Draft an IRREVOCABLE MASTER FEE PROTECTION "
        "AGREEMENT (IMFPA) for intermediaries using the inputs. Markdown output. "
        f"{_INPUT_GUIDE}"
    ),
    "loi": (
        "You are a commodity trade lawyer. Draft a non-binding LETTER OF INTENT (LOI) "
        "from the buyer (sender.company_name) confirming readiness to purchase from "
        "supplier.name. Markdown output. "
        f"{_INPUT_GUIDE}"
    ),
}

DOC_TITLES = {
    "outreach_email": "Supplier Outreach Email",
    "counter_offer_email": "Counter-Offer Email",
    "spa_buyer": "Sale & Purchase Agreement (Buyer)",
    "spa_supplier": "Sale & Purchase Agreement (Supplier)",
    "ncnda": "Non-Circumvention, Non-Disclosure Agreement",
    "fpa": "Fee Protection Agreement",
    "imfpa": "Irrevocable Master Fee Protection Agreement",
    "loi": "Letter of Intent",
}


class DocumentGenerationService:
    def __init__(self) -> None:
        self.llm = get_llm()

    async def generate(self, doc_type: str, inputs: dict[str, Any]) -> tuple[str, str]:
        if doc_type not in DOC_SYSTEMS:
            raise ValueError(f"Unknown document type: {doc_type}")
        system = DOC_SYSTEMS[doc_type]
        user = (
            "Structured inputs (JSON):\n"
            f"{json.dumps(inputs, indent=2, default=str)}\n\n"
            f"Generate the {doc_type.replace('_', ' ')} document now."
        )
        content = await self.llm.complete(system, user, max_tokens=2000)
        title = DOC_TITLES[doc_type]
        return title, content.strip()

    def to_docx(self, title: str, markdown_content: str) -> bytes:
        """Basic Markdown -> DOCX conversion. Not feature-complete, but usable."""
        doc = DocxDocument()
        doc.add_heading(title, level=0)
        for line in markdown_content.splitlines():
            stripped = line.rstrip()
            if not stripped:
                doc.add_paragraph("")
                continue
            if stripped.startswith("### "):
                doc.add_heading(stripped[4:], level=3)
            elif stripped.startswith("## "):
                doc.add_heading(stripped[3:], level=2)
            elif stripped.startswith("# "):
                doc.add_heading(stripped[2:], level=1)
            elif stripped.startswith(("- ", "* ")):
                doc.add_paragraph(stripped[2:], style="List Bullet")
            else:
                doc.add_paragraph(stripped)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
