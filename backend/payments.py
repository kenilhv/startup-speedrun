import os
from . import db

def pay(invoice):
    """Create an idempotent Stripe test transfer, or an explicit mock transfer locally."""
    vendor = invoice.get("vendor") or {}
    amount = invoice.get("amount_cents") or 0
    if amount <= 0: raise ValueError("Invoice must have a positive amount")
    key = os.getenv("STRIPE_SECRET_KEY")
    if key:
        import stripe
        stripe.api_key = key
        if not vendor.get("stripe_account_id"): raise ValueError("Vendor has no Stripe connected account")
        transfer = stripe.Transfer.create(amount=amount, currency=invoice.get("currency") or "usd", destination=vendor["stripe_account_id"], metadata={"invoice_id": invoice["id"], "invoice_number": invoice.get("invoice_number") or ""}, idempotency_key=f"invoice-{invoice['id']}")
        transfer_id, payment_status = transfer.id, "pending"
    else:
        transfer_id, payment_status = f"mock_transfer_{invoice['id']}", "paid"
    with db.connect() as c:
        c.execute("INSERT INTO payments(invoice_id,stripe_transfer_id,amount_cents,status) VALUES (?,?,?,?) ON CONFLICT(invoice_id) DO UPDATE SET stripe_transfer_id=excluded.stripe_transfer_id,status=excluded.status", (invoice["id"], transfer_id, amount, payment_status))
    return transfer_id
