"""Start a demo verification call through the running dev server (calls must start in the server process).

Run from backend/:  python -m calling.test_call +14155550123 [--provider web|vapi|mock] [--vendor fastconsult]
With --provider web, open {PUBLIC_BASE_URL}/phone/<digits> on the phone first and tap "Go online".
"""
import argparse
import os
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

DEMO = {
    "cleanvendor": dict(invoice_id=4, vendor_name="CleanVendor Inc", invoice_number="INV-910",
                        amount_display="$480.00", bank_last4_claimed="9921"),
    "fastconsult": dict(invoice_id=5, vendor_name="FastConsult LLC", invoice_number="INV-F42",
                        amount_display="$4,500.00", bank_last4_claimed="7788"),
}

if __name__ == "__main__":
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("phone", help="E.164, e.g. +14155550123")
    p.add_argument("--provider", choices=["web", "vapi", "mock", "brainbase"])
    p.add_argument("--vendor", choices=list(DEMO), default="cleanvendor")
    args = p.parse_args()
    u = urlsplit(os.environ.get("CALL_RESULT_URL", "http://127.0.0.1:8000/webhooks/call-result"))
    r = httpx.post(f"{u.scheme}://{u.netloc}/dev/call", timeout=40,
                   json={"phone": args.phone, "vendor": args.vendor, "provider": args.provider})
    print(r.status_code, r.text)
