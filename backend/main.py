import json, logging, os, secrets, shutil
from urllib.parse import parse_qs
from datetime import datetime, timezone
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from . import db, pipeline, workflow
from .models import CallResult
from .call_queue import CallQueue
from .seed import seed

ROOT = Path(__file__).parent; UPLOADS = ROOT / "uploads"; UPLOADS.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")
log = logging.getLogger("paycrew")
app = FastAPI(title="PayCrew API")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class Hub:
    def __init__(self): self.clients = set()
    async def send(self, payload):
        for ws in list(self.clients):
            try: await ws.send_json(payload)
            except Exception: self.clients.discard(ws)
    async def invoice(self, invoice): await self.send({"type":"invoice.updated", "invoice": invoice})
    async def status(self, invoice_id, status, message):
        invoice = workflow.transition(invoice_id, status); db.add_event(invoice_id, message)
        await self.invoice(invoice); await self.send({"type":"activity", "invoice_id":invoice_id, "message":message, "ts":datetime.now(timezone.utc).isoformat()})
        try:
            if status == "blocked":
                from .slack_notify import notify_blocked
                await notify_blocked(invoice, message)
            elif status == "awaiting_approval":
                from .slack_notify import notify_approval
                await notify_approval(invoice, message)
            elif status == "escalated":
                from .slack_notify import notify_escalated
                await notify_escalated(invoice, message)
        except Exception: log.exception("Slack notification failed for invoice %s", invoice_id)

hub = Hub(); queue = CallQueue(hub)

@app.on_event("startup")
async def startup(): db.init_db(); seed(); queue.start()

@app.get("/health")
def health(): return {"ok": True}

@app.get("/invoices")
def invoices(): return db.list_invoices()
@app.get("/invoices/{invoice_id}")
def invoice(invoice_id: int):
    value = db.get_invoice(invoice_id)
    if not value: raise HTTPException(404, "Invoice not found")
    return value
@app.get("/vendors")
def vendors(): return db.list_vendors()
@app.get("/events")
def events(): return db.list_events()

@app.post("/invoices/upload")
async def upload(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    created = []
    for file in files:
        if not file.filename.lower().endswith(".pdf"): raise HTTPException(400, "Only PDF uploads are accepted")
        path = UPLOADS / f"{secrets.token_hex(8)}.pdf"
        with path.open("wb") as out: shutil.copyfileobj(file.file, out)
        with db.connect() as c:
            cur = c.execute("INSERT INTO invoices(filename,pdf_path,status) VALUES (?,?,?)", (file.filename, str(path), "received")); invoice_id = cur.lastrowid
        invoice = db.get_invoice(invoice_id); created.append(invoice); await hub.invoice(invoice)
        background_tasks.add_task(pipeline.process, invoice_id, hub, queue)
    return created

async def decide(invoice_id: int, approved: bool, actor="dashboard"):
    invoice = db.get_invoice(invoice_id)
    if not invoice: raise HTTPException(404, "Invoice not found")
    if invoice["status"] != "awaiting_approval": raise HTTPException(409, "Invoice is not awaiting approval")
    if approved:
        from .payments import pay
        try: pay(invoice)
        except Exception as exc: raise HTTPException(502, "Payment provider failed; invoice remains awaiting approval") from exc
        await hub.status(invoice_id, "settled", f"Approved by {actor}, paid via Stripe")
    else: await hub.status(invoice_id, "blocked", f"Rejected by {actor}")
    return db.get_invoice(invoice_id)

@app.post("/invoices/{invoice_id}/approve")
async def approve(invoice_id: int): return await decide(invoice_id, True)
@app.post("/invoices/{invoice_id}/reject")
async def reject(invoice_id: int): return await decide(invoice_id, False)

@app.post("/webhooks/call-result")
async def call_result(payload: CallResult, x_paycrew_secret: str | None = Header(None)):
    expected = os.getenv("PAYCREW_WEBHOOK_SECRET")
    if expected and not secrets.compare_digest(x_paycrew_secret or "", expected): raise HTTPException(401, "Invalid webhook secret")
    invoice = db.get_invoice(payload.invoice_id)
    if not invoice: raise HTTPException(404, "Invoice not found")
    if invoice["status"] != "calling": raise HTTPException(409, "Invoice is not awaiting a call result")
    accepted = queue.result(payload.invoice_id, payload.outcome.value, payload.summary, payload.provider_call_id, payload.transcript)
    if not accepted: raise HTTPException(409, "Call result was already recorded")
    return {"ok": True}

@app.post("/webhooks/slack")
async def slack_webhook(request: Request):
    body = await request.body()
    from .slack_notify import valid_signature
    if not valid_signature(body, request.headers.get("X-Slack-Request-Timestamp"), request.headers.get("X-Slack-Signature")):
        raise HTTPException(401, "Invalid Slack signature")
    values = parse_qs(body.decode())
    payload = json.loads(values["payload"][0])
    action = payload["actions"][0]
    invoice_id = int(action["value"])
    approved = action["action_id"] == "approve_invoice"
    if action["action_id"] not in {"approve_invoice", "reject_invoice"}: raise HTTPException(422, "Unknown Slack action")
    actor = payload.get("user", {}).get("name") or payload.get("user", {}).get("username") or "Slack user"
    invoice = await decide(invoice_id, approved, actor)
    return {"response_type":"ephemeral", "text":f"Invoice {invoice_id} {'approved and paid' if approved else 'rejected'}."}

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    body = await request.body(); signature = request.headers.get("Stripe-Signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret: raise HTTPException(503, "Stripe webhook is not configured")
    import stripe
    try: event = stripe.Webhook.construct_event(body, signature, secret)
    except Exception as exc: raise HTTPException(400, "Invalid Stripe webhook") from exc
    if event["type"] == "transfer.created":
        transfer = event["data"]["object"]; invoice_id = transfer.get("metadata", {}).get("invoice_id")
        if invoice_id:
            with db.connect() as c: c.execute("UPDATE payments SET status='paid' WHERE invoice_id=?", (int(invoice_id),))
            await hub.invoice(db.get_invoice(int(invoice_id)))
    return {"ok": True}

@app.post("/demo/reset")
async def reset(): db.clear_demo(); return {"ok": True}

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept(); hub.clients.add(ws)
    try:
        await ws.send_json({"type":"invoices.snapshot", "invoices":db.list_invoices()})
        while True: await ws.receive_text()
    except WebSocketDisconnect: hub.clients.discard(ws)
