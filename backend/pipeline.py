import asyncio
from . import brainbase_payer, db, rules, payments
from .stripe_gateway import StripeConfigurationError
from .agents.crew import Crew, CrewResult

async def process(invoice_id, notify, queue):
    invoice = db.get_invoice(invoice_id)
    await notify.status(invoice_id, "analyzing", "Analysis crew started: 6 agents")
    try:
        with open(invoice["pdf_path"], "rb") as pdf: pdf_bytes = pdf.read()
        result = await Crew(invoice_id, notify.invoice).run(pdf_bytes)
    except Exception as exc:
        result = CrewResult(error=str(exc))
    if result.error:
        await notify.status(invoice_id, "escalated", "Invoice extraction failed; needs a human"); return
    fields, vendor, level, reasons = result.fields, result.vendor, result.level, result.reasons
    updated = db.update_invoice(invoice_id, vendor_name_raw=fields.get("vendor_name"), vendor_id=vendor["id"] if vendor else None,
        invoice_number=fields.get("invoice_number"), amount_cents=fields.get("amount_cents") or 0, currency=fields.get("currency") or "usd",
        due_date=fields.get("due_date"), bank_last4_claimed=fields.get("bank_last4"), po_reference=fields.get("po_reference"),
        fields_json=fields, risk_level=level, risk_reasons=reasons)
    await notify.invoice(updated)
    if not vendor:
        await notify.status(invoice_id, "escalated", "Unknown vendor; needs a human"); return
    if level == "low":
        try:
            receipt = await asyncio.to_thread(payments.pay, invoice_id)
        except (payments.PaymentError, StripeConfigurationError) as exc:
            if db.get_invoice(invoice_id)["status"] == "analyzing":
                await notify.status(invoice_id, "escalated", str(exc))
            return
        if isinstance(receipt, brainbase_payer.Dispatch):
            await notify.status(invoice_id, "paying", receipt.message)
        else:
            await notify.payment(receipt)
    else:
        await notify.status(invoice_id, "flagged", "; ".join(reasons)); await queue.enqueue(invoice_id)
