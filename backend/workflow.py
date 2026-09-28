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
    invoice = db.get_invoice(invoice_id)
    if not invoice: raise KeyError(invoice_id)
    if target not in ALLOWED.get(invoice["status"], set()):
        raise InvalidTransition(f"Cannot move invoice {invoice_id} from {invoice['status']} to {target}")
    return db.update_invoice(invoice_id, status=target)
