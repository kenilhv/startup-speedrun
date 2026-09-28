import re

def extract_invoice(pdf_bytes: bytes) -> dict:
    """Replace with Shresth's extractor. This deterministic fallback keeps the integration usable."""
    text = pdf_bytes.decode("utf-8", errors="ignore")
    def pick(pattern):
        m = re.search(pattern, text, re.I); return m.group(1).strip() if m else None
    amount_text = text.replace("$", "")
    amount_match = re.search(r"(?:total|amount)\s*:?\s*([\d,]+(?:\.\d{2})?)", amount_text, re.I)
    amount = amount_match.group(1) if amount_match else None
    return {"vendor_name": pick(r"vendor\s*:\s*([^\n\r]+)"), "invoice_number": pick(r"invoice\s*(?:number|#)?\s*:\s*([^\n\r]+)"),
            "amount_cents": int(float(amount.replace(',', '')) * 100) if amount else 0, "currency": "usd",
            "due_date": pick(r"due\s*date\s*:\s*([\d-]+)"), "bank_last4": pick(r"(?:bank|account).*?(\d{4})"),
            "po_reference": pick(r"(?:po|purchase order)\s*(?:#|reference)?\s*:\s*([^\n\r]+)"), "confidence": 0.4}
