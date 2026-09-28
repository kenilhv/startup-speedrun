import asyncio, json, logging, os, secrets, shutil
from urllib.parse import parse_qs
from datetime import datetime, timezone
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from . import brainbase_payer, db, pipeline, workflow, payments
from .stripe_gateway import StripeConfigurationError
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
    async def payment(self, receipt):
        invoice = db.get_invoice(receipt.invoice_id)
        await self.invoice(invoice)
        if receipt.activity:
            await self.send({"type": "activity", "invoice_id": receipt.invoice_id,
                             "message": receipt.activity["message"], "ts": receipt.activity["created_at"]})
            try:
                from .slack_notify import notify_paid
                await notify_paid(invoice, receipt.activity["message"])
            except Exception: log.exception("Slack paid notification failed for invoice %s", receipt.invoice_id)
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

pipeline_tasks = set()
from .calling.webhooks import router as calling_router  # Kenil: /phone/<digits>, /webhooks/vapi, /webhooks/brainbase/*
app.include_router(calling_router)
from .calling import web_phone as _web_phone
hub = Hub(); queue = CallQueue(hub);
async def _call_live(event):  # live transcript / call start -> every board
    await hub.send({"type": "call.live", **event})
_web_phone.LIVE_HOOKS.append(_call_live)
from .agents.call_judge import classify as _classify_call
_web_phone.CLASSIFIERS.append(_classify_call)
payment_watcher = brainbase_payer.PaymentWatcher(hub)

from .slack_socket import SlackSocket
slack_socket = SlackSocket(lambda invoice_id, approved, actor: decide(invoice_id, approved, actor))

@app.on_event("startup")
async def startup():
    db.init_db(); seed(); queue.start(); payment_watcher.start()
    try:
        if slack_socket.start(): log.info("Slack buttons via Socket Mode")
    except Exception: log.exception("Slack Socket Mode failed to start; buttons on the board still work")

@app.on_event("shutdown")
async def shutdown(): slack_socket.stop()

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
        # One task per file so invoices are analyzed in parallel, not one after another.
        task = asyncio.create_task(pipeline.process(invoice_id, hub, queue))
        pipeline_tasks.add(task); task.add_done_callback(pipeline_tasks.discard)
    return created

async def decide(invoice_id: int, approved: bool, actor="dashboard"):
    invoice = db.get_invoice(invoice_id)
    if not invoice: raise HTTPException(404, "Invoice not found")
    if approved and invoice["status"] == "settled" and invoice.get("payment"):
        if (invoice["payment"].get("stripe_transfer_id") or "").startswith("tr_"):
            return invoice
    if invoice["status"] != "awaiting_approval": raise HTTPException(409, "Invoice is not awaiting approval")
    if approved:
        try:
            receipt = await asyncio.to_thread(payments.pay, invoice_id, actor)
        except StripeConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
        except payments.PaymentConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except payments.PaymentError as exc:
            raise HTTPException(502, str(exc)) from exc
        if isinstance(receipt, brainbase_payer.Dispatch):
            await hub.status(invoice_id, "paying", f"Approved by {actor}. {receipt.message}")
        else:
            await hub.payment(receipt)
    else:
        try:
            await hub.status(invoice_id, "blocked", f"Rejected by {actor}")
        except workflow.InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
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
    done = "rejected" if not approved else ("approved and paid" if invoice["status"] == "settled" else "approved; Brainbase is paying it")
    return {"response_type":"ephemeral", "text":f"Invoice {invoice_id} {done}."}

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    body = await request.body(); signature = request.headers.get("Stripe-Signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret: raise HTTPException(503, "Stripe webhook is not configured")
    import stripe
    if not signature:
        raise HTTPException(400, "Missing Stripe signature")
    try:
        event = stripe.Webhook.construct_event(body, signature, secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(400, "Invalid Stripe webhook") from exc
    try:
        receipt = await asyncio.to_thread(payments.handle_transfer_event, event)
    except payments.PaymentMismatch as exc:
        raise HTTPException(400, str(exc)) from exc
    if receipt:
        await hub.payment(receipt)
    return {"ok": True}

@app.post("/demo/reset")
async def reset():
    try:
        db.clear_demo()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept(); hub.clients.add(ws)
    try:
        await ws.send_json({"type":"invoices.snapshot", "invoices":db.list_invoices()})
        while True: await ws.receive_text()
    except WebSocketDisconnect: hub.clients.discard(ws)
