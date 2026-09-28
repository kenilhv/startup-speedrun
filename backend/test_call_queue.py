"""Call queue robustness: caller failures, missing phones, mock outcomes, restart recovery."""
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from . import db
from .test_support import OfflineTestCase


class CallQueueTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.patch("backend.main.UPLOADS", self.root)

    def flagged(self, vendor="CleanVendor Inc", number="INV-910"):
        v = db.find_vendor(vendor)
        with db.connect() as c:
            return c.execute("""INSERT INTO invoices(vendor_id,vendor_name_raw,invoice_number,amount_cents,currency,
                bank_last4_claimed,status,risk_level) VALUES (?,?,?,48000,'usd','9921','flagged','critical')""",
                (v["id"] if v else None, vendor, number)).lastrowid

    def wait(self, invoice_id, statuses, seconds=3):
        deadline = time.time() + seconds
        while time.time() < deadline:
            invoice = db.get_invoice(invoice_id)
            if invoice["status"] in statuses:
                return invoice
            time.sleep(0.03)
        self.fail(f"invoice {invoice_id} stuck in {db.get_invoice(invoice_id)['status']}, wanted {statuses}")

    def client(self):
        from backend.main import app
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def test_mock_outcomes_per_vendor(self):
        with patch.dict(os.environ, {"MOCK_CALL_OUTCOMES": "CleanVendor Inc=denied"}):
            from backend.main import queue
            clean, fast = self.flagged(), self.flagged("FastConsult LLC", "INV-F42")
            self.client()
            for i in (clean, fast):
                queue.pending.put_nowait(i)
            self.assertEqual(self.wait(clean, {"blocked"})["call"]["outcome"], "denied")
            self.assertEqual(self.wait(fast, {"awaiting_approval"})["call"]["outcome"], "confirmed")

    def test_caller_exception_retries_then_escalates_and_queue_survives(self):
        from backend.main import queue
        with patch("backend.calling.caller.start_verification_call", side_effect=RuntimeError("provider down")):
            first = self.flagged()
            self.client()
            queue.pending.put_nowait(first)
            invoice = self.wait(first, {"escalated"})
            self.assertEqual(invoice["call"]["attempt"], 2)
        second = self.flagged("FastConsult LLC", "INV-F42")
        queue.pending.put_nowait(second)
        self.wait(second, {"awaiting_approval"})  # worker is still alive

    def test_missing_phone_escalates_for_real_calls(self):
        with patch.dict(os.environ, {"MOCK_CALLS": "false"}):
            invoice_id = self.flagged()
            from backend.main import queue
            self.client()
            queue.pending.put_nowait(invoice_id)
            invoice = self.wait(invoice_id, {"escalated"})
            self.assertIsNone(invoice["call"])

    def test_restart_recovers_interrupted_calls(self):
        invoice_id = self.flagged()
        with db.connect() as c:
            c.execute("UPDATE invoices SET status='calling' WHERE id=?", (invoice_id,))
            c.execute("INSERT INTO calls(invoice_id,attempt,status) VALUES (?,1,'in_progress')", (invoice_id,))
        self.client()  # startup runs recovery
        invoice = self.wait(invoice_id, {"awaiting_approval"})
        self.assertEqual(invoice["call"]["attempt"], 2)
