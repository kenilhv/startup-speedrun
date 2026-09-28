"""Browser "vendor phone": the vendor keeps {PUBLIC_BASE_URL}/phone/<digits of phone_on_file> open on their phone.
start_verification_call(CALL_PROVIDER=web) makes that page ring; Answer starts a Vapi web call with the same
assistant as a phone call. Vapi posts end-of-call-report to /webhooks/vapi?token=<one-time token>.
"""
import os
import re
import secrets
import threading
from pathlib import Path

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .results import post_result_sync
from .script import vapi_assistant

router = APIRouter()
_lock = threading.Lock()
RINGING: dict[str, dict] = {}   # phone digits -> ringing call
ANSWERED: dict[str, dict] = {}  # call_id -> answered call
WEB_CALLS: dict[str, dict] = {}  # webhook token -> {invoice_id, call_id}; read by webhooks.py
PAGE_PATH = Path(__file__).with_name("phone.html")
import hashlib as _hashlib
PAGE_VERSION = _hashlib.sha1(PAGE_PATH.read_bytes()).hexdigest()[:10]  # open pages reload when this changes
CLASSIFIERS: list = []  # main.py may register async (transcript_text, v) -> (outcome, summary)


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
    html = html.replace("__VAPI_PUBLIC_KEY__", os.environ.get("VAPI_PUBLIC_KEY", "")).replace("__NUMBER__", digits(number)).replace("__VERSION__", PAGE_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/phone/{number}/poll")
def poll(number: str):
    e = RINGING.get(digits(number))
    if not e:
        return {"ringing": False, "version": PAGE_VERSION}
    v = e["v"]
    return {"ringing": True, "version": PAGE_VERSION, "call_id": e["call_id"], "company_name": v["company_name"],
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


# Live call events for the board: main.py registers an async hook (event dict) here.
LIVE_HOOKS: list = []


async def publish_live(event: dict) -> None:
    for hook in LIVE_HOOKS:
        try:
            await hook(event)
        except Exception as exc:
            print(f"[phone] live hook failed: {exc}", flush=True)


@router.post("/phone/{number}/live")
async def phone_live(number: str, body: dict):
    """Transcript lines and call start, mirrored from the vendor's phone page."""
    call_id = body.get("call_id")
    with _lock:
        e = ANSWERED.get(call_id) if call_id else None
    if not e:
        return {"ok": False}
    await publish_live({
        "invoice_id": int(e["v"]["invoice_id"]), "call_id": call_id, "event": body.get("event", "transcript"),
        "role": "agent" if body.get("role") == "assistant" else "vendor",
        "text": str(body.get("text") or "")[:1000], "final": bool(body.get("final")),
    })
    return {"ok": True}


def _heuristic(text: str) -> tuple[str, str]:
    said = " ".join(l.split(":", 1)[1] for l in text.splitlines() if l.startswith("VENDOR:")).lower()
    if not said.strip():
        return "no_answer", "The vendor did not say anything on the call."
    if any(w in said for w in ("didn't", "did not", "not us", "never", "haven't", "no we", "no,", "nope", "fraud")) or said.strip().startswith("no"):
        return "denied", "Vendor said they did not request the bank change."
    if any(w in said for w in ("yes", "yeah", "correct", "that's right", "we did", "we changed", "switched")):
        return "confirmed", "Vendor confirmed they requested the bank change."
    return "unclear", "The vendor's answer was unclear."


@router.post("/phone/{number}/ended")
async def phone_ended(number: str, request: Request):
    """The vendor's phone reports the call ended, with the full transcript. Decide the outcome now."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    call_id = body.get("call_id")
    with _lock:
        e = ANSWERED.pop(call_id, None) if call_id else None
        for token, w in list(WEB_CALLS.items()):
            if w.get("call_id") == call_id:
                WEB_CALLS.pop(token, None)  # the provider webhook becomes a no-op for this call
    if not e:
        return {"ok": False}
    lines = [x for x in body.get("transcript") or [] if isinstance(x, dict) and x.get("text")]
    text = "\n".join(f"{'AGENT' if x.get('role') == 'assistant' else 'VENDOR'}: {str(x['text'])[:500]}" for x in lines)
    outcome, summary = _heuristic(text)
    for classify in CLASSIFIERS:
        try:
            outcome, summary = await classify(text, e["v"])
            break
        except Exception as exc:
            print(f"[phone] classifier failed, using keywords: {exc}", flush=True)
    invoice_id = int(e["v"]["invoice_id"])
    await publish_live({"invoice_id": invoice_id, "call_id": call_id, "event": "ended", "role": None,
                        "text": summary, "final": True, "outcome": outcome})
    await asyncio.to_thread(post_result_sync, {"invoice_id": invoice_id, "provider_call_id": call_id,
                                               "outcome": outcome, "summary": summary, "transcript": text})
    return {"ok": True, "outcome": outcome}


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
