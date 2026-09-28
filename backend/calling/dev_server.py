"""Stand-in for Zubair's backend so calls can be tested solo.

Run from backend/:  python -m uvicorn calling.dev_server:app --port 8000
Then expose it:     cloudflared tunnel --url http://localhost:8000   (set PUBLIC_BASE_URL to that URL)
"""
import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException

from .caller import start_verification_call
from .test_call import DEMO
from .webhooks import router

app = FastAPI(title="PayCrew calling dev server")
app.include_router(router)
results: list[dict] = []


@app.post("/webhooks/call-result")
async def call_result(payload: dict, x_paycrew_secret: str | None = Header(default=None)):
    if x_paycrew_secret != os.environ.get("PAYCREW_WEBHOOK_SECRET"):
        raise HTTPException(status_code=401, detail="bad secret")
    results.append(payload)
    print("\n=== CALL RESULT ===\n" + json.dumps(payload, indent=2), flush=True)
    return {"ok": True}


@app.post("/dev/call")
async def dev_call(body: dict):
    if body.get("provider"):
        os.environ["CALL_PROVIDER"] = body["provider"]
    call_id = await asyncio.to_thread(start_verification_call, phone_on_file=body["phone"],
                                      **DEMO[body.get("vendor") or "cleanvendor"])
    return {"provider_call_id": call_id, "provider": os.environ.get("CALL_PROVIDER", "web")}


@app.get("/results")
def list_results():
    return results


@app.get("/health")
def health():
    return {"ok": True}
