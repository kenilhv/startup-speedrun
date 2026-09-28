import asyncio
import logging
import os

from . import db

log = logging.getLogger("paycrew.calls")


def _usd(cents):
    return f"${(cents or 0) / 100:,.2f}"


def mock_outcome(vendor_name):
    """MOCK_CALL_OUTCOMES="CleanVendor Inc=denied,FastConsult LLC=confirmed"; anyone else confirms."""
    raw = os.getenv("MOCK_CALL_OUTCOMES", "CleanVendor Inc=denied")
    table = dict(pair.split("=", 1) for pair in raw.split(",") if "=" in pair)
    outcome = table.get(vendor_name, "confirmed").strip()
    return outcome if outcome in {"confirmed", "denied", "no_answer", "unclear"} else "confirmed"


MOCK_SUMMARIES = {
    "confirmed": "Vendor confirmed the new bank account ending {last4} is theirs (mock call).",
    "denied": "Vendor said they did not change their bank details and will alert their finance team (mock call).",
    "no_answer": "No one answered (mock call).",
    "unclear": "The answer was unclear (mock call).",
}


class CallQueue:
    """One verification call at a time. Survives caller errors and server restarts."""

    def __init__(self, notify):
        self.pending, self.events, self.outcomes, self.notify = asyncio.Queue(), {}, {}, notify
        self.worker = None
        self.loop = None

    def start(self):
        loop = asyncio.get_running_loop()
        if self.loop is not loop:
            self.pending, self.events, self.outcomes, self.worker, self.loop = asyncio.Queue(), {}, {}, None, loop
        self._recover()
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self._run())

    def _recover(self):
        """Calls cut off by a restart count as unanswered; their invoices go back in the queue."""
        with db.connect() as c:
            c.execute("""UPDATE calls SET status='done', outcome=COALESCE(outcome,'no_answer'),
                summary=COALESCE(summary,'Server restarted during the call'), ended_at=CURRENT_TIMESTAMP
                WHERE status='in_progress'""")
            c.execute("UPDATE invoices SET status='flagged', updated_at=CURRENT_TIMESTAMP WHERE status='calling'")
            ids = [r[0] for r in c.execute("SELECT id FROM invoices WHERE status='flagged' ORDER BY id")]
        for invoice_id in ids:
            self.pending.put_nowait(invoice_id)

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
            try:
                await self._verify(invoice_id)
            except Exception:
                log.exception("Verification call for invoice %s crashed", invoice_id)
                try:
                    with db.connect() as c:
                        c.execute("UPDATE calls SET status='done', outcome=COALESCE(outcome,'unclear'), ended_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND status='in_progress'", (invoice_id,))
                    invoice = db.get_invoice(invoice_id)
                    if invoice and invoice["status"] in ("flagged", "calling"):
                        await self.notify.status(invoice_id, "escalated", "Verification call failed unexpectedly; needs a human")
                except Exception:
                    log.exception("Could not escalate invoice %s", invoice_id)
            finally:
                self.events.pop(invoice_id, None); self.outcomes.pop(invoice_id, None)

    async def _verify(self, invoice_id):
        invoice = db.get_invoice(invoice_id)
        if not invoice or invoice["status"] != "flagged":
            return  # already handled (duplicate queue entry, approval, reset)
        mock = os.getenv("MOCK_CALLS") == "true"
        vendor = db.find_vendor(invoice["vendor_name_raw"])
        if not vendor:
            await self.notify.status(invoice_id, "escalated", "Unknown vendor; no number on file to call"); return
        if not vendor.get("phone_on_file") and not mock:
            await self.notify.status(invoice_id, "escalated", f"No phone number on file for {vendor['name']}; needs a human"); return

        with db.connect() as c:
            attempt = c.execute("SELECT count(*) FROM calls WHERE invoice_id=?", (invoice_id,)).fetchone()[0] + 1
            c.execute("INSERT INTO calls(invoice_id,attempt,status,started_at) VALUES (?,?,?,CURRENT_TIMESTAMP)", (invoice_id, attempt, "in_progress"))
        # Register before dialing so an instant webhook still lands.
        event = self.events[invoice_id] = asyncio.Event()
        event.outcome, event.summary = "no_answer", "Timed out waiting for verification"
        self.outcomes.pop(invoice_id, None)
        await self.notify.status(invoice_id, "calling", f"Calling {vendor['name']} on file number")

        amount = _usd(invoice["amount_cents"])
        try:
            from .calling.caller import start_verification_call
            provider_id = await asyncio.to_thread(
                start_verification_call, invoice_id, vendor["name"], vendor["phone_on_file"],
                invoice["invoice_number"], amount, invoice.get("bank_last4_claimed"))
        except ImportError:
            provider_id = None
        except Exception as exc:  # contract: the caller raises when it cannot start a call
            log.warning("Caller failed for invoice %s: %s", invoice_id, exc)
            provider_id = None
            self.result(invoice_id, "unclear", f"Call could not be started: {exc}")

        if provider_id:
            with db.connect() as c:
                c.execute("UPDATE calls SET provider_call_id=? WHERE invoice_id=? AND attempt=?", (provider_id, invoice_id, attempt))
            if str(provider_id).startswith("mock-"):
                outcome = mock_outcome(vendor["name"])
                summary = MOCK_SUMMARIES[outcome].format(last4=invoice.get("bank_last4_claimed") or "????")
                delay = float(os.getenv("MOCK_CALL_DELAY_SECONDS", "5"))
                asyncio.get_running_loop().call_later(delay, self.result, invoice_id, outcome, summary, provider_id)

        try:
            await asyncio.wait_for(event.wait(), float(os.getenv("CALL_TIMEOUT_SECONDS", "180")))
        except asyncio.TimeoutError:
            pass
        outcome, summary = event.outcome, event.summary
        with db.connect() as c:  # timeout: no webhook recorded anything
            c.execute("UPDATE calls SET status='done',outcome=?,summary=?,ended_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND attempt=? AND status='in_progress'", (outcome, summary, invoice_id, attempt))

        if outcome == "confirmed":
            await self.notify.status(invoice_id, "awaiting_approval", "Vendor confirmed bank change by phone")
        elif outcome == "denied":
            await self.notify.status(invoice_id, "blocked", f"Fraud stopped: {amount} payment to {vendor['name']} blocked")
        elif attempt < 2:
            await self.notify.status(invoice_id, "flagged", "No clear response; retrying verification")
            await self.enqueue(invoice_id)
        else:
            await self.notify.status(invoice_id, "escalated", "Verification failed twice; needs a human")
