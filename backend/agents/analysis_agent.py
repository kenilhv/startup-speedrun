"""PayCrew invoice analysis agent (owner: Shresth).

Called by the backend with raw PDF bytes; returns the contract dict from MASTER.md 4.4.
Never raises. Order: Claude (up to 2 tries) -> regex over the PDF text -> {"error": ...}.
"""
from __future__ import annotations

import base64
import os
import re
import sys
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

MODEL = "claude-opus-5"

PROMPT = (
    "You are an accounts payable clerk. Read this invoice and return only JSON with keys: "
    "vendor_name, invoice_number, amount_cents (integer, the total due in cents), currency "
    "(lowercase ISO code), due_date (YYYY-MM-DD or null), bank_last4 (last 4 digits of the "
    "payee bank account, or null), po_reference (or null), confidence (0-1). "
    "Do not guess missing values; use null."
)

KEYS = ("vendor_name", "invoice_number", "amount_cents", "currency", "due_date",
        "bank_last4", "po_reference", "confidence")


class InvoiceFields(BaseModel):
    vendor_name: Optional[str]
    invoice_number: Optional[str]
    amount_cents: Optional[int]
    currency: Optional[str]
    due_date: Optional[str]
    bank_last4: Optional[str]
    po_reference: Optional[str]
    confidence: float


def _log(msg: str) -> None:
    print(f"[analysis_agent] {msg}", file=sys.stderr)


# ---------- normalization ----------

def _to_cents(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = re.sub(r"[^\d.]", "", str(value))
    if not s:
        return None
    return int(round(float(s) * 100))


def _to_date(value) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _to_last4(value) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits[-4:] if len(digits) >= 4 else None


def _normalize(d: dict) -> dict:
    po = d.get("po_reference")
    if po and str(po).strip().lower() in ("none", "n/a", "null", ""):
        po = None
    return {
        "vendor_name": (d.get("vendor_name") or "").strip() or None,
        "invoice_number": (d.get("invoice_number") or "").strip() or None,
        "amount_cents": _to_cents(d.get("amount_cents")),
        "currency": (d.get("currency") or "usd").lower(),
        "due_date": _to_date(d.get("due_date")),
        "bank_last4": _to_last4(d.get("bank_last4")),
        "po_reference": po,
        "confidence": max(0.0, min(1.0, float(d.get("confidence") or 0))),
    }


def _usable(d: dict) -> bool:
    return bool(d.get("vendor_name") and d.get("invoice_number") and d.get("amount_cents"))


# ---------- Claude path ----------

def _extract_with_claude(pdf_bytes: bytes) -> dict:
    import anthropic  # imported lazily so the backend loads without the SDK configured

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf",
                    "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                }},
                {"type": "text", "text": PROMPT},
            ],
        }],
        output_format=InvoiceFields,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise ValueError(f"unusable response (stop_reason={response.stop_reason})")
    return _normalize(response.parsed_output.model_dump())


# ---------- regex fallback ----------

def _is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF")


def _pdf_text(pdf_bytes: bytes) -> str:
    if not _is_pdf(pdf_bytes):
        return pdf_bytes.decode("utf-8", errors="ignore")  # plain-text invoices (backend tests)
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _search(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_with_regex(pdf_bytes: bytes) -> dict:
    text = _pdf_text(pdf_bytes)
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), None)
    return _normalize({
        "vendor_name": _search(r"^\s*Vendor\s*:\s*([^\n]+)", text) or first_line,
        "invoice_number": _search(r"Invoice\s*(?:#|No\.?|Number)\s*:?\s*([A-Z0-9][A-Z0-9-]*)", text),
        "amount_cents": _search(r"^\s*Total(?:\s+due)?\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)", text),
        "currency": (_search(r"Total(?:\s+due)?\s*:?\s*\$?[\d,.]+\s+([A-Z]{3})\b", text) or "usd"),
        "due_date": _search(r"Due\s*date\s*:?\s*([^\n]+)", text),
        "bank_last4": _search(r"(?:account|acct|bank)\s*(?:ending(?:\s+in)?|no\.?|number|#)?\s*:?\s*[•*x\s]*(\d{4})\b", text),
        "po_reference": _search(r"PO\s*(?:reference|ref|#|number)\s*:?\s*([A-Za-z0-9-]+)", text),
        "confidence": 0.5,
    })


# ---------- public entry point ----------

def extract_invoice(pdf_bytes: bytes) -> dict:
    """Returns {vendor_name, invoice_number, amount_cents, currency, due_date,
    bank_last4, po_reference, confidence}. Never raises: on failure returns {"error": "..."}."""
    errors = []
    for attempt in (1, 2) if _is_pdf(pdf_bytes) else ():
        try:
            result = _extract_with_claude(pdf_bytes)
            if _usable(result):
                _log(f"claude ok (attempt {attempt})")
                return result
            errors.append(f"claude attempt {attempt}: missing required fields")
        except Exception as e:  # noqa: BLE001 - this function must never raise
            errors.append(f"claude attempt {attempt}: {type(e).__name__}: {e}")
            if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError") or \
                    "api_key" in str(e).lower() or "authentication" in str(e).lower():
                break  # retrying won't help
    _log(("; ".join(errors) or "not a PDF") + " -> regex fallback")

    try:
        result = _extract_with_regex(pdf_bytes)
        if _usable(result):
            return result
        errors.append("regex: missing required fields")
    except Exception as e:  # noqa: BLE001
        errors.append(f"regex: {type(e).__name__}: {e}")
    return {"error": "; ".join(errors)}


if __name__ == "__main__":
    import json

    for p in sys.argv[1:]:
        with open(p, "rb") as f:
            print(p, json.dumps(extract_invoice(f.read())))
