"""Provider webhooks -> contract JSON -> POST {CALL_RESULT_URL} (Zubair's /webhooks/call-result).

Mount in the backend:  from calling.webhooks import router;  app.include_router(router)

  POST /webhooks/vapi                    Vapi end-of-call-report (header X-PayCrew-Secret, or ?token= for web calls)
  GET  /phone/<digits>                   browser vendor phone (see web_phone.py)
  POST /webhooks/brainbase/result        posted by the Based flow at the end of the conversation
  POST /webhooks/brainbase/call-ended    Brainbase custom webhook (?token=<secret>), catches no-answer
"""
import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from . import web_phone
from .caller import CALL_INVOICE
from .script import OUTCOMES

router = APIRouter()
router.include_router(web_phone.router)
log = logging.getLogger("paycrew.calling")

REPORTED: set[str] = set()
VAPI_NO_ANSWER = {"customer-did-not-answer", "customer-busy", "voicemail"}
BRAINBASE_NO_ANSWER = {"failed", "voicemail", "no-answer", "no_answer", "busy"}
DEFAULT_SUMMARY = {
    "no_answer": "Nobody answered the verification call.",
    "unclear": "The call ended without a clear answer from the vendor.",
    "confirmed": "Vendor confirmed they requested the bank change.",
    "denied": "Vendor says they did not request the bank change.",
}


def _check_secret(secret: str | None) -> None:
    if not secret or secret != os.environ.get("PAYCREW_WEBHOOK_SECRET"):
        raise HTTPException(status_code=401, detail="bad secret")


def _dig(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def build_result(invoice_id, provider_call_id: str, outcome: str | None, summary: str | None,
                 transcript: str | None) -> dict:
    if outcome not in OUTCOMES:
        outcome = "unclear"
    return {
        "invoice_id": int(invoice_id),
        "provider_call_id": provider_call_id,
        "outcome": outcome,
        "summary": (summary or "").strip() or DEFAULT_SUMMARY[outcome],
        "transcript": transcript or "",
    }


async def post_call_result(result: dict) -> None:
    call_id = result["provider_call_id"]
    if call_id in REPORTED:
        return
    REPORTED.add(call_id)
    url = os.environ.get("CALL_RESULT_URL", "http://127.0.0.1:8000/webhooks/call-result")
    headers = {"X-PayCrew-Secret": os.environ.get("PAYCREW_WEBHOOK_SECRET", "")}
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=result, headers=headers)
            r.raise_for_status()
            log.info("call result posted: %s", result)
            return
        except Exception as e:
            log.error("posting call result failed (attempt %d): %s", attempt, e)
            await asyncio.sleep(1)
    REPORTED.discard(call_id)


def parse_vapi_report(msg: dict, web: dict | None = None) -> dict | None:
    call = msg.get("call") or {}
    if web:
        call_id, invoice_id = web["call_id"], web["invoice_id"]
    else:
        call_id = call.get("id")
        invoice_id = (CALL_INVOICE.get(call_id)
                      or _dig(call, "assistant", "metadata", "invoice_id")
                      or _dig(call, "assistantOverrides", "variableValues", "invoice_id"))
    if not call_id or invoice_id is None:
        log.error("vapi report without call id / invoice id: call=%s", call_id)
        return None
    reason = msg.get("endedReason") or ""
    transcript = _dig(msg, "artifact", "transcript") or msg.get("transcript") or ""
    analysis = msg.get("analysis") or {}
    data = analysis.get("structuredData") or {}

    if reason in VAPI_NO_ANSWER or not transcript.strip():
        outcome, summary = "no_answer", f"Nobody answered the verification call ({reason or 'no transcript'})."
    else:
        outcome = data.get("outcome")
        summary = data.get("summary") or analysis.get("summary")
    return build_result(invoice_id, call_id, outcome, summary, transcript)


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request, background: BackgroundTasks, token: str | None = None,
                       x_paycrew_secret: str | None = Header(default=None)):
    web = web_phone.WEB_CALLS.get(token) if token else None
    if not web:
        _check_secret(x_paycrew_secret)
    msg = (await request.json()).get("message") or {}
    if msg.get("type") == "end-of-call-report":
        if web:
            web_phone.ANSWERED.pop(web["call_id"], None)
        result = parse_vapi_report(msg, web)
        if result:
            background.add_task(post_call_result, result)
    return {"ok": True}


@router.post("/webhooks/brainbase/result")
async def brainbase_result(payload: dict, background: BackgroundTasks,
                           x_paycrew_secret: str | None = Header(default=None)):
    _check_secret(x_paycrew_secret)
    result = build_result(payload["invoice_id"], payload["provider_call_id"], payload.get("outcome"),
                          payload.get("summary"), payload.get("transcript"))
    background.add_task(post_call_result, result)
    return {"ok": True}


async def _brainbase_fallback(result: dict) -> None:
    # The flow's own POST normally lands first; only report if it never did.
    await asyncio.sleep(8)
    await post_call_result(result)


@router.post("/webhooks/brainbase/call-ended")
async def brainbase_call_ended(request: Request, background: BackgroundTasks, token: str | None = None):
    _check_secret(token)
    body = await request.json()
    if isinstance(body.get("data"), dict):
        body = body["data"]
    raw = body.get("raw_data") or {}
    call_id = raw.get("paycrew_call_id") or raw.get("id")
    invoice_id = raw.get("invoice_id") or CALL_INVOICE.get(call_id)
    if not call_id or invoice_id is None:
        log.error("brainbase call-ended without paycrew ids: %s", body)
        return {"ok": True}
    status = str(body.get("call_status") or _dig(body, "log", "status") or "").lower()
    transcript = body.get("transcript") or ""
    if isinstance(transcript, list):
        transcript = "\n".join(str(t) for t in transcript)
    no_answer = status in BRAINBASE_NO_ANSWER or not str(transcript).strip()
    outcome = "no_answer" if no_answer else "unclear"
    reason = body.get("disconnection_reason") or status or "no transcript"
    summary = f"Nobody answered the verification call ({reason})." if no_answer else None
    background.add_task(_brainbase_fallback, build_result(invoice_id, call_id, outcome, summary, transcript))
    return {"ok": True}
