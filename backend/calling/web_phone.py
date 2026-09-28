"""Browser "vendor phone": the vendor keeps {PUBLIC_BASE_URL}/phone/<digits of phone_on_file> open on their phone.
start_verification_call(CALL_PROVIDER=web) makes that page ring; Answer starts a Vapi web call with the same
assistant as a phone call. Vapi posts end-of-call-report to /webhooks/vapi?token=<one-time token>.
"""
import os
import re
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from .results import post_result_sync
from .script import vapi_assistant

router = APIRouter()
_lock = threading.Lock()
RINGING: dict[str, dict] = {}   # phone digits -> ringing call
ANSWERED: dict[str, dict] = {}  # call_id -> answered call
WEB_CALLS: dict[str, dict] = {}  # webhook token -> {invoice_id, call_id}; read by webhooks.py
PAGE_PATH = Path(__file__).with_name("phone.html")


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def ring(phone: str, v: dict) -> str:
    key = digits(phone)
    call_id = f"web_{v['invoice_id']}_{secrets.token_hex(4)}"
    with _lock:
        RINGING[key] = {"call_id": call_id, "v": v, "token": secrets.token_urlsafe(18)}
    threading.Timer(float(os.environ.get("PHONE_RING_SECONDS", "60")), _ring_timeout, args=(key, call_id)).start()
    return call_id


def _result(e: dict, outcome: str, summary: str) -> dict:
    return {"invoice_id": int(e["v"]["invoice_id"]), "provider_call_id": e["call_id"],
            "outcome": outcome, "summary": summary, "transcript": ""}


def _take_ringing(key: str, call_id: str | None) -> dict | None:
    with _lock:
        e = RINGING.get(key)
        if e and (call_id is None or e["call_id"] == call_id):
            return RINGING.pop(key)
    return None


def _ring_timeout(key: str, call_id: str) -> None:
    e = _take_ringing(key, call_id)
    if e:
        post_result_sync(_result(e, "no_answer", "Nobody answered the verification call."))


@router.get("/phone/{number}", response_class=HTMLResponse)
def phone_page(number: str):
    html = PAGE_PATH.read_text(encoding="utf-8")
    html = html.replace("__VAPI_PUBLIC_KEY__", os.environ.get("VAPI_PUBLIC_KEY", "")).replace("__NUMBER__", digits(number))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/phone/{number}/poll")
def poll(number: str):
    e = RINGING.get(digits(number))
    if not e:
        return {"ringing": False}
    v = e["v"]
    return {"ringing": True, "call_id": e["call_id"], "company_name": v["company_name"],
            "vendor_name": v["vendor_name"], "invoice_number": v["invoice_number"], "amount_display": v["amount_display"]}


@router.post("/phone/{number}/answer")
def answer(number: str, body: dict):
    e = _take_ringing(digits(number), body.get("call_id"))
    if not e:
        raise HTTPException(status_code=409, detail="This call is no longer ringing.")
    with _lock:
        ANSWERED[e["call_id"]] = e
        WEB_CALLS[e["token"]] = {"invoice_id": int(e["v"]["invoice_id"]), "call_id": e["call_id"]}
    base = os.environ["PUBLIC_BASE_URL"].rstrip("/")
    return {"assistant": vapi_assistant(e["v"], {"url": f"{base}/webhooks/vapi?token={e['token']}"})}


@router.post("/phone/{number}/log")
def phone_log(number: str, body: dict):
    print(f"[phone {digits(number)[-4:]}] {body.get('event')}: {body.get('detail')}", flush=True)
    return {"ok": True}


@router.post("/phone/{number}/decline")
def decline(number: str, body: dict):
    """Decline while ringing -> no_answer. Called after Answer when the web call failed to start -> unclear."""
    call_id = body.get("call_id")
    e = _take_ringing(digits(number), call_id)
    if e:
        post_result_sync(_result(e, "no_answer", "The vendor declined the verification call."))
        return {"ok": True}
    with _lock:
        e = ANSWERED.pop(call_id, None) if call_id else None
    if e:
        reason = str(body.get("reason") or "unknown error")[:200]
        post_result_sync(_result(e, "unclear", f"The verification call could not connect ({reason})."))
    return {"ok": True}
