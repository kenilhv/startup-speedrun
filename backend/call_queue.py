import asyncio
from . import db

class CallQueue:
    def __init__(self, notify):
        self.pending, self.events, self.outcomes, self.notify = asyncio.Queue(), {}, {}, notify
        self.worker = None
        self.loop = None

    def start(self):
        loop = asyncio.get_running_loop()
        if self.loop is not loop:
            self.pending, self.events, self.outcomes, self.worker, self.loop = asyncio.Queue(), {}, {}, None, loop
        if not self.worker or self.worker.done(): self.worker = asyncio.create_task(self._run())

    async def enqueue(self, invoice_id):
        await self.pending.put(invoice_id)

    def result(self, invoice_id, outcome, summary, provider_call_id=None, transcript=None):
        with db.connect() as c:
            call = c.execute("SELECT id,status FROM calls WHERE invoice_id=? ORDER BY id DESC LIMIT 1", (invoice_id,)).fetchone()
            if not call or call["status"] != "in_progress": return False
            c.execute("UPDATE calls SET provider_call_id=COALESCE(?,provider_call_id), outcome=?, summary=?, transcript=?, status='done', ended_at=CURRENT_TIMESTAMP WHERE id=?", (provider_call_id, outcome, summary, transcript, call["id"]))
        self.outcomes[invoice_id] = (outcome, summary)
        event = self.events.get(invoice_id)
        if event: event.outcome, event.summary = outcome, summary; event.set()
        return True

    async def _run(self):
        while True:
            invoice_id = await self.pending.get()
            invoice = db.get_invoice(invoice_id)
            if not invoice or invoice["status"] not in ("flagged", "calling"): continue
            vendor = db.find_vendor(invoice["vendor_name_raw"])
            if not vendor: continue
            with db.connect() as c: attempt = c.execute("SELECT count(*) FROM calls WHERE invoice_id=?", (invoice_id,)).fetchone()[0] + 1
            with db.connect() as c: c.execute("INSERT INTO calls(invoice_id,attempt,status,started_at) VALUES (?,?,?,CURRENT_TIMESTAMP)", (invoice_id, attempt, "in_progress"))
            await self.notify.status(invoice_id, "calling", f"Calling {vendor['name']} on file number")
            event = self.events[invoice_id] = asyncio.Event(); event.outcome = "no_answer"; event.summary = "Timed out waiting for verification"
            if invoice_id in self.outcomes:
                event.outcome, event.summary = self.outcomes.pop(invoice_id); event.set()
            try:
                from .calling.caller import start_verification_call
                provider_id = start_verification_call(invoice_id, vendor["name"], vendor["phone_on_file"], invoice["invoice_number"], f"${(invoice['amount_cents'] or 0)/100:,.2f}", invoice.get("bank_last4_claimed"))
                if provider_id:
                    with db.connect() as c: c.execute("UPDATE calls SET provider_call_id=? WHERE invoice_id=? AND attempt=?", (provider_id, invoice_id, attempt))
                    if str(provider_id).startswith("mock-"): self.result(invoice_id, "confirmed", f"Mock verification for ${(invoice['amount_cents'] or 0)/100:,.2f}", provider_id)
            except ImportError: pass
            try: await asyncio.wait_for(event.wait(), 180)
            except asyncio.TimeoutError: pass
            outcome, summary = event.outcome, event.summary
            with db.connect() as c: c.execute("UPDATE calls SET status='done',outcome=?,summary=?,ended_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND attempt=?", (outcome, summary, invoice_id, attempt))
            if outcome == "confirmed": await self.notify.status(invoice_id, "awaiting_approval", "Vendor confirmed bank change by phone")
            elif outcome == "denied": await self.notify.status(invoice_id, "blocked", f"Fraud stopped: ${(invoice['amount_cents'] or 0)/100:,.2f} payment to {vendor['name']} blocked")
            elif attempt < 2: await self.enqueue(invoice_id); await self.notify.status(invoice_id, "flagged", "No clear response; retrying verification")
            else: await self.notify.status(invoice_id, "escalated", "Verification failed twice; needs a human")
            self.events.pop(invoice_id, None); self.outcomes.pop(invoice_id, None)
