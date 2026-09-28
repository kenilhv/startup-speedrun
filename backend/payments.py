"""Durable, test-only Stripe transfers. No implicit simulated payments."""
import json
import time
from dataclasses import dataclass
from uuid import uuid4

import stripe

from . import db
from .stripe_gateway import resource_dict, test_client

# Stripe may discard idempotency keys after 24 hours. Stop automatic retries early.
RETRY_WINDOW = 23 * 60 * 60
LEASE_SECONDS = 120


class PaymentError(RuntimeError):
    pass


class PaymentConflict(PaymentError):
    pass


class PaymentMismatch(PaymentError):
    pass


@dataclass
class Receipt:
    invoice_id: int
    transfer_id: str
    activity: dict | None = None


def _reserve(invoice_id, approved_by):
    now = time.time()
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        invoice = c.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not invoice:
            raise PaymentConflict("Invoice not found")
        payment = c.execute("SELECT * FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone()
        if payment and payment["status"] == "paid":
            if not payment["idempotency_key"] or not (payment["stripe_transfer_id"] or "").startswith("tr_"):
                raise PaymentConflict("Legacy payment has no verified Stripe transfer; reconcile it manually")
            return dict(payment)
        if payment and payment["status"] in {"pending", "unknown"}:
            if not payment["request_json"] or not payment["started_at"]:
                raise PaymentConflict("Legacy payment needs manual reconciliation")
            if payment["lease_until"] > now:
                raise PaymentConflict("A Stripe request is already in progress")
            if now - payment["started_at"] >= RETRY_WINDOW:
                raise PaymentConflict("Stripe result is unresolved and the retry window expired; reconcile before retrying")
            # Reuse both the exact payload and key, including the original approval.
            c.execute("UPDATE payments SET lease_until=?,status='pending' WHERE id=?", (now + LEASE_SECONDS, payment["id"]))
            return dict(payment)
        eligible = (invoice["status"] == "analyzing" and invoice["risk_level"] == "low" and not approved_by)
        eligible |= invoice["status"] == "awaiting_approval" and bool(approved_by)
        if not eligible:
            raise PaymentConflict("Invoice is not eligible for this payment action")
        amount = invoice["amount_cents"]
        if type(amount) is not int or not 0 < amount <= 99_999_999:
            raise PaymentConflict("Invoice amount must be positive integer cents within Stripe limits")
        if invoice["currency"] != "usd":
            raise PaymentConflict("This PayCrew integration supports USD invoices only")
        if not invoice["invoice_number"]:
            raise PaymentConflict("Invoice number is required")
        vendor = c.execute("SELECT * FROM vendors WHERE id=?", (invoice["vendor_id"],)).fetchone()
        if not vendor or not (vendor["stripe_account_id"] or "").startswith("acct_"):
            raise PaymentConflict("Vendor needs a Stripe connected account; run backend.seed --stripe")
        if not approved_by and (not invoice["bank_last4_claimed"] or invoice["bank_last4_claimed"] != vendor["bank_last4_on_file"]):
            raise PaymentConflict("Bank details must match for automatic payment")
        key = f"paycrew-transfer-{uuid4().hex}"
        params = {
            "amount": amount, "currency": "usd", "destination": vendor["stripe_account_id"],
            "metadata": {"invoice_id": str(invoice_id), "invoice_number": invoice["invoice_number"],
                         "paycrew_payment_ref": key},
        }
        c.execute("""INSERT INTO payments(invoice_id,amount_cents,status,idempotency_key,request_json,started_at,lease_until,approved_by)
            VALUES (?,?,'pending',?,?,?,?,?) ON CONFLICT(invoice_id) DO UPDATE SET
            status='pending',stripe_transfer_id=NULL,amount_cents=excluded.amount_cents,
            idempotency_key=excluded.idempotency_key,request_json=excluded.request_json,
            started_at=excluded.started_at,lease_until=excluded.lease_until,
            error_code=NULL,approved_by=excluded.approved_by""",
            (invoice_id, amount, key, json.dumps(params), now, now + LEASE_SECONDS, approved_by))
        return dict(c.execute("SELECT * FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone())


def _validate_transfer(payment, transfer):
    params = json.loads(payment["request_json"])
    metadata = transfer.get("metadata") or {}
    if (transfer.get("object") != "transfer" or not str(transfer.get("id", "")).startswith("tr_")
            or transfer.get("livemode") is not False or transfer.get("reversed")
            or transfer.get("amount_reversed", 0) != 0
            or any(transfer.get(k) != params[k] for k in ("amount", "currency", "destination"))
            or metadata.get("paycrew_payment_ref") != payment["idempotency_key"]
            or metadata.get("invoice_id") != str(payment["invoice_id"])):
        raise PaymentMismatch("Stripe transfer does not match the reserved test payment")


def _settle(c, payment, transfer):
    _validate_transfer(payment, transfer)
    transfer_id = transfer["id"]
    if payment["status"] == "paid":
        if payment["stripe_transfer_id"] != transfer_id:
            raise PaymentMismatch("A different transfer is already recorded for this invoice")
        return Receipt(payment["invoice_id"], transfer_id)
    c.execute("UPDATE payments SET status='paid',stripe_transfer_id=?,lease_until=0,error_code=NULL WHERE id=?", (transfer_id, payment["id"]))
    c.execute("UPDATE invoices SET status='settled',updated_at=CURRENT_TIMESTAMP WHERE id=?", (payment["invoice_id"],))
    message = f"Stripe test transfer {transfer_id} created: ${payment['amount_cents'] / 100:,.2f} to vendor account"
    if payment["approved_by"]:
        message += f"; approved by {payment['approved_by']}"
    event_id = c.execute("INSERT INTO events(invoice_id,type,message) VALUES (?,'payment.paid',?)", (payment["invoice_id"], message)).lastrowid
    event = dict(c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())
    return Receipt(payment["invoice_id"], transfer_id, event)


def pay(invoice_id: int, approved_by: str | None = None) -> Receipt:
    client = test_client()  # Refuse missing/live credentials before reserving anything.
    payment = _reserve(invoice_id, approved_by)
    if payment["status"] == "paid":
        return Receipt(invoice_id, payment["stripe_transfer_id"])
    key = payment["idempotency_key"]
    try:
        transfer = resource_dict(client.v1.transfers.create(json.loads(payment["request_json"]), {"idempotency_key": key}))
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT * FROM payments WHERE idempotency_key=?", (key,)).fetchone()
            if not current:
                raise PaymentMismatch("Reserved payment disappeared; manual reconciliation required")
            return _settle(c, current, transfer)
    except Exception as exc:
        # A transport/server failure can mean Stripe created the transfer. Never mark
        # that as a definitive failure, and never replace its idempotency key.
        definite = isinstance(exc, (stripe.InvalidRequestError, stripe.AuthenticationError, stripe.PermissionError))
        definite &= getattr(exc, "http_status", None) in {400, 401, 403, 404}
        code = getattr(exc, "code", None) or type(exc).__name__
        with db.connect() as c:
            c.execute("UPDATE payments SET status=?,error_code=?,lease_until=0 WHERE idempotency_key=? AND status!='paid'",
                      ("failed" if definite else "unknown", code, key))
            # A valid webhook can have completed the payment while the request timed out.
            current = c.execute("SELECT * FROM payments WHERE idempotency_key=?", (key,)).fetchone()
            if current and current["status"] == "paid":
                return Receipt(invoice_id, current["stripe_transfer_id"])
        raise PaymentError(f"Stripe payment {'failed' if definite else 'needs reconciliation'} ({code})") from exc


def handle_transfer_event(event):
    """Called only after signature verification. Replays and foreign events are harmless."""
    event = resource_dict(event)
    if event.get("livemode") is not False:
        raise PaymentMismatch("Only Stripe test events are accepted")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id.startswith("evt_"):
        raise PaymentMismatch("Invalid Stripe event ID")
    if event.get("type") != "transfer.created":
        return None
    transfer = (event.get("data") or {}).get("object") or {}
    ref = (transfer.get("metadata") or {}).get("paycrew_payment_ref")
    if not ref:
        return None
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM stripe_events WHERE id=?", (event_id,)).fetchone():
            return None
        payment = c.execute("SELECT * FROM payments WHERE idempotency_key=?", (ref,)).fetchone()
        if not payment:
            return None  # Unrelated payment or a previous demo run; do not trust invoice_id alone.
        receipt = _settle(c, payment, transfer)
        c.execute("INSERT INTO stripe_events(id,type) VALUES (?,?)", (event_id, event["type"]))
        return receipt
