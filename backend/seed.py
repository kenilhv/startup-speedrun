import os
import argparse
import json
import time
from uuid import uuid4
from pathlib import Path
from dotenv import load_dotenv
from . import db
from .stripe_gateway import resource_dict, test_client

load_dotenv(Path(__file__).with_name(".env"))

VENDORS = [
    ("OfficeSupplyCo", "0011", None), ("PaperWorks Ltd", "0022", None),
    ("TechSoftware Inc", "0033", None), ("CleanVendor Inc", "0042", None),
    ("FastConsult LLC", "0055", None),
]
def seed():
    db.init_db()
    with db.connect() as c:
        for name, bank, phone in VENDORS:
            variable = {"CleanVendor Inc": "CLEAN_VENDOR_PHONE", "FastConsult LLC": "FAST_CONSULT_PHONE"}.get(name)
            phone = os.getenv(variable) or None if variable else phone
            c.execute("""INSERT INTO vendors(name,bank_last4_on_file,phone_on_file) VALUES (?,?,?)
                ON CONFLICT(name) DO UPDATE SET phone_on_file=COALESCE(excluded.phone_on_file,vendors.phone_on_file)""", (name, bank, phone))

def _setup_request(name, params):
    """Persist exact input before sending it; a rerun recovers the same Stripe object."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM stripe_setup WHERE name=?", (name,)).fetchone()
        if not row:
            key = f"paycrew-setup-{uuid4().hex}"
            c.execute("INSERT INTO stripe_setup(name,idempotency_key,request_json,started_at) VALUES (?,?,?,?)", (name, key, json.dumps(params), time.time()))
            row = c.execute("SELECT * FROM stripe_setup WHERE name=?", (name,)).fetchone()
        if not row["object_id"] and time.time() - row["started_at"] >= 23 * 3600:
            raise RuntimeError(f"Unresolved setup operation {name}; inspect Stripe request logs before retrying")
        return dict(row)


def provision_accounts(client):
    accounts = []
    for vendor in db.list_vendors():
        # Only provision the five contract vendors, not old provisional/local entries.
        if vendor["name"] not in {v[0] for v in VENDORS}:
            continue
        if vendor["stripe_account_id"]:
            account = resource_dict(client.v1.accounts.retrieve(vendor["stripe_account_id"]))
        else:
            operation = _setup_request(f"vendor:{vendor['id']}", {
                "type": "custom", "country": "US", "business_type": "company",
                "company": {"name": vendor["name"]},
                "capabilities": {"transfers": {"requested": True}},
                "business_profile": {"product_description": "PayCrew sandbox invoice payment demonstration"},
                "external_account": {"object": "bank_account", "country": "US", "currency": "usd",
                                     "routing_number": "110000000", "account_number": "000123456789",
                                     "account_holder_name": vendor["name"], "account_holder_type": "company"},
                "metadata": {"paycrew_vendor_id": str(vendor["id"]), "paycrew_vendor_name": vendor["name"]},
            })
            if operation["object_id"]:
                account = resource_dict(client.v1.accounts.retrieve(operation["object_id"]))
            else:
                account = resource_dict(client.v1.accounts.create(json.loads(operation["request_json"]), {"idempotency_key": operation["idempotency_key"]}))
            if not str(account.get("id", "")).startswith("acct_"):
                raise RuntimeError("Stripe returned an invalid account")
            with db.connect() as c:
                c.execute("UPDATE stripe_setup SET object_id=? WHERE name=?", (account["id"], operation["name"]))
                c.execute("UPDATE vendors SET stripe_account_id=? WHERE id=?", (account["id"], vendor["id"]))
        accounts.append({"vendor": vendor["name"], "account_id": account["id"],
                         "transfers": (account.get("capabilities") or {}).get("transfers"),
                         "requirements_due": (account.get("requirements") or {}).get("currently_due", [])})
    return accounts


def fund_platform(client):
    operation = _setup_request("platform-funding", {
        "amount": 2_000_000, "currency": "usd", "payment_method": "pm_card_bypassPending",
        "payment_method_types": ["card"], "confirm": True,
        "description": "PayCrew sandbox platform funding", "metadata": {"purpose": "paycrew_demo_funding"},
    })
    if operation["object_id"]:
        intent = resource_dict(client.v1.payment_intents.retrieve(operation["object_id"]))
    else:
        intent = resource_dict(client.v1.payment_intents.create(json.loads(operation["request_json"]), {"idempotency_key": operation["idempotency_key"]}))
    if intent.get("livemode") is not False or not str(intent.get("id", "")).startswith("pi_"):
        raise RuntimeError("Stripe returned an invalid sandbox PaymentIntent")
    with db.connect() as c:
        c.execute("UPDATE stripe_setup SET object_id=? WHERE name=?", (intent["id"], operation["name"]))
    if intent.get("status") != "succeeded":
        raise RuntimeError(f"Funding intent {intent['id']} has status {intent.get('status')}; inspect Stripe Dashboard")
    return {"payment_intent_id": intent["id"], "status": intent["status"], "amount_cents": intent["amount"]}


def stripe_status(client):
    balance = resource_dict(client.v1.balance.retrieve())
    if balance.get("livemode") is not False:
        raise RuntimeError("Stripe balance is not in test mode")
    return {"available": balance["available"], "pending": balance["pending"]}


def provision_stripe(accounts_only=False):
    client = test_client()
    accounts = provision_accounts(client)
    result = {"accounts": accounts}
    if not accounts_only:
        result["funding"] = fund_platform(client)
    result["balance"] = stripe_status(client)
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PayCrew vendors and Stripe sandbox setup")
    parser.add_argument("--stripe", action="store_true", help="Provision five test accounts and one $20,000 funding intent")
    parser.add_argument("--accounts-only", action="store_true", help="Provision accounts without funding")
    parser.add_argument("--check", action="store_true", help="Read the sandbox balance without creating Stripe objects")
    parser.add_argument("--reconcile", type=int, metavar="INVOICE_ID", help="Retry an unresolved transfer with its original request key")
    args = parser.parse_args()
    seed()
    try:
        if args.stripe or args.accounts_only:
            print(json.dumps(provision_stripe(args.accounts_only), indent=2))
        if args.check:
            print(json.dumps(stripe_status(test_client()), indent=2))
        if args.reconcile:
            from .payments import pay
            with db.connect() as c:
                row = c.execute("SELECT status FROM payments WHERE invoice_id=?", (args.reconcile,)).fetchone()
            if not row or row["status"] not in {"pending", "unknown", "paid"}:
                raise RuntimeError("Reconciliation requires an existing unresolved or completed payment")
            receipt = pay(args.reconcile)
            print(json.dumps({"invoice_id": receipt.invoice_id, "stripe_transfer_id": receipt.transfer_id}))
    except Exception as exc:
        # Do not dump request headers, SDK objects, or credentials to terminal output.
        import stripe
        if isinstance(exc, stripe.StripeError):
            parser.exit(1, f"Stripe setup failed ({getattr(exc, 'code', None) or type(exc).__name__}); check the sandbox request logs.\n")
        parser.exit(1, f"{exc}\n")
