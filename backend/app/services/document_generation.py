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
        "You are a commodity trader writing a cold outreach email to a potential supplier. "
        f"{_INPUT_GUIDE} "
        "Tone: professional, concise, warm but not salesy. Include a subject line. "
        "Under 200 words. Sign off with the sender's full name, title, company, and email."
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
