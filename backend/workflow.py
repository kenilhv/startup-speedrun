"""Single source of truth for allowed invoice state transitions."""
from . import db

ALLOWED = {
    "received": {"analyzing"},
    "analyzing": {"settled", "flagged", "escalated"},
    "flagged": {"calling", "escalated"},
    "calling": {"awaiting_approval", "blocked", "flagged", "escalated"},
    "awaiting_approval": {"settled", "blocked"},
    "settled": set(), "blocked": set(), "escalated": set(),
}

class InvalidTransition(ValueError): pass

def transition(invoice_id: int, target: str):
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        invoice = c.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not invoice:
            raise KeyError(invoice_id)
        if target not in ALLOWED.get(invoice["status"], set()):
            raise InvalidTransition(f"Cannot move invoice {invoice_id} from {invoice['status']} to {target}")
        payment = c.execute("SELECT status FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone()
        if target == "settled":
            raise InvalidTransition("Settlement must come from a verified Stripe transfer")
        if target == "blocked" and payment and payment["status"] in {"pending", "unknown", "paid"}:
            raise InvalidTransition("Cannot reject a payment already sent to Stripe or awaiting reconciliation")
        c.execute("UPDATE invoices SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (target, invoice_id))
    return db.get_invoice(invoice_id)
