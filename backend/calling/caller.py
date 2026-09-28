"""Outbound vendor verification calls. CALL_PROVIDER:
  web       - rings the vendor's browser "phone" page ({PUBLIC_BASE_URL}/phone/<digits>) and runs a Vapi web call
  vapi      - real phone call via Vapi (needs an imported Twilio/Telnyx number)
  mock      - no call; scripted result after MOCK_CALL_DELAY seconds
  brainbase - legacy Brainbase voice deployment API

start_verification_call is synchronous (one HTTP request, ~1s). From async code call it with
    await asyncio.to_thread(start_verification_call, ...)
"""
import os
import threading
import uuid

import httpx

from . import web_phone
from .results import post_result_sync
from .script import call_vars, opening_line, vapi_assistant

VAPI_API = "https://api.vapi.ai"

# provider_call_id -> invoice_id, read by webhooks.py (same process when the router is mounted in the backend)
CALL_INVOICE: dict[str, int] = {}


def start_verification_call(invoice_id: int, vendor_name: str, phone_on_file: str,
                            invoice_number: str, amount_display: str,
                            bank_last4_claimed: str, company_name: str = "Acme Supplies") -> str:
    """Starts ONE outbound call. Returns provider_call_id. Raises on failure."""
    v = call_vars(invoice_id, vendor_name, invoice_number, amount_display, bank_last4_claimed, company_name)
    provider = os.environ.get("CALL_PROVIDER", "web").lower()
    if provider == "web":
        call_id = web_phone.ring(phone_on_file, v)
    elif provider == "vapi":
        call_id = _start_vapi(phone_on_file, v)
    elif provider == "brainbase":
        call_id = _start_brainbase(phone_on_file, v)
    elif provider == "mock":
        call_id = _start_mock(v)
    else:
        raise ValueError(f"Unknown CALL_PROVIDER {provider!r}")
    CALL_INVOICE[call_id] = invoice_id
    return call_id


MOCK_SUMMARY = {
    "denied": "Vendor says they did not change bank details and will alert their finance team.",
    "confirmed": "Vendor confirmed they switched banks last week and requested the new account.",
    "unclear": "The person who answered wasn't sure and asked us to hold the payment.",
    "no_answer": "Nobody answered the verification call.",
}


def _start_mock(v: dict) -> str:
    call_id = f"mock_{v['invoice_id']}_{uuid.uuid4().hex[:8]}"
    outcome = os.environ.get("MOCK_OUTCOME") or (
        "confirmed" if "fastconsult" in v["vendor_name"].lower().replace(" ", "") else "denied")
    delay = float(os.environ.get("MOCK_CALL_DELAY", "6"))
    threading.Timer(delay, _post_mock_result, args=(v, call_id, outcome)).start()
    return call_id


def _post_mock_result(v: dict, call_id: str, outcome: str) -> None:
    post_result_sync({
        "invoice_id": int(v["invoice_id"]),
        "provider_call_id": call_id,
        "outcome": outcome,
        "summary": MOCK_SUMMARY.get(outcome, MOCK_SUMMARY["unclear"]),
        "transcript": f"AGENT: {opening_line(v)}\nVENDOR: (mock call, outcome={outcome})",
    })


def _public_base() -> str:
    return os.environ["PUBLIC_BASE_URL"].rstrip("/")


def _start_brainbase(phone: str, v: dict) -> str:
    # make-batch-calls returns no call id, so we mint one and the flow echoes it back.
    call_id = f"pc_{v['invoice_id']}_{uuid.uuid4().hex[:8]}"
    row = {
        **v,
        "id": call_id,
        "phoneNumber": phone,
        "paycrew_call_id": call_id,
        "opening_line": opening_line(v),
        "result_url": f"{_public_base()}/webhooks/brainbase/result",
        "paycrew_secret": os.environ["PAYCREW_WEBHOOK_SECRET"],
    }
    worker = os.environ["BRAINBASE_WORKER_ID"]
    deployment = os.environ["BRAINBASE_DEPLOYMENT_ID"]
    api = os.environ.get("BRAINBASE_API_BASE", "https://brainbase-monorepo-api.onrender.com")
    r = httpx.post(
        f"{api}/api/workers/{worker}/deployments/voice/{deployment}/make-batch-calls",
        headers={"x-api-key": os.environ["BRAINBASE_API_KEY"]},
        json={"data": [row], "batch_size": 1},
        timeout=30,
    )
    if r.is_error:
        raise RuntimeError(f"Brainbase make-batch-calls failed: {r.status_code} {r.text}")
    return call_id


def _start_vapi(phone: str, v: dict) -> str:
    assistant = vapi_assistant(v, {
        "url": f"{_public_base()}/webhooks/vapi",
        "headers": {"X-PayCrew-Secret": os.environ["PAYCREW_WEBHOOK_SECRET"]},
    })
    r = httpx.post(
        f"{VAPI_API}/call",
        headers={"Authorization": f"Bearer {os.environ['VAPI_API_KEY']}"},
        json={
            "name": f"PayCrew {v['invoice_number']}"[:40],
            "phoneNumberId": os.environ["VAPI_PHONE_NUMBER_ID"],
            "customer": {"number": phone},
            "assistant": assistant,
        },
        timeout=30,
    )
    if r.is_error:
        raise RuntimeError(f"Vapi create call failed: {r.status_code} {r.text}")
    return r.json()["id"]
