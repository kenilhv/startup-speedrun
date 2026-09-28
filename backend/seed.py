import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from . import db

load_dotenv(Path(__file__).with_name(".env"))

VENDORS = [
    ("OfficeSupplyCo", "0011", "+14155550121"), ("PaperWorks Ltd", "0022", "+14155550122"),
    ("TechSoftware Inc", "0033", "+14155550123"), ("CleanVendor Inc", "0042", os.getenv("CLEAN_VENDOR_PHONE", "+14155550124")),
    ("FastConsult LLC", "0055", os.getenv("FAST_CONSULT_PHONE", "+14155550125")),
]
def seed():
    db.init_db()
    with db.connect() as c:
        c.executemany("INSERT INTO vendors(name,bank_last4_on_file,phone_on_file) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET bank_last4_on_file=excluded.bank_last4_on_file, phone_on_file=excluded.phone_on_file", VENDORS)

def provision_stripe():
    """One-time test-mode setup. Requires STRIPE_SECRET_KEY; safe to rerun."""
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key: raise RuntimeError("Set STRIPE_SECRET_KEY before provisioning Stripe")
    import stripe
    stripe.api_key = key
    with db.connect() as c:
        vendors = c.execute("SELECT id,name,stripe_account_id FROM vendors").fetchall()
        for vendor in vendors:
            if vendor["stripe_account_id"]: continue
            account = stripe.Account.create(type="custom", country="US", business_type="company", capabilities={"transfers":{"requested":True}}, metadata={"paycrew_vendor_id":vendor["id"], "paycrew_vendor_name":vendor["name"]})
            c.execute("UPDATE vendors SET stripe_account_id=? WHERE id=?", (account.id, vendor["id"]))
    # Funds the platform in test mode using the card stipulated in MASTER.md.
    stripe.PaymentIntent.create(amount=2_000_000, currency="usd", payment_method_data={"type":"card", "card":{"number":"4000000000000077", "exp_month":12, "exp_year":2030, "cvc":"123"}}, confirm=True, payment_method_types=["card"], description="PayCrew demo platform funding")

if __name__ == "__main__":
    seed()
    if "--stripe" in sys.argv: provision_stripe()
