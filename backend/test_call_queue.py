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
        with db.connect() as c:
            c.execute("UPDATE vendors SET phone_on_file='+15550100001' WHERE name='CleanVendor Inc'")
        with patch.dict(os.environ, {"MOCK_CALLS": "false"}), \
             patch("backend.calling.caller.start_verification_call", side_effect=RuntimeError("provider down")):
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

    def test_real_provider_gets_contract_args_and_webhook_settles(self):
        seen = {}

        def fake_call(invoice_id, vendor_name, phone, invoice_number, amount, last4, company):
            seen.update(invoice_id=invoice_id, vendor=vendor_name, phone=phone, amount=amount, last4=last4)
            return "web_1_abcd"
        with db.connect() as c:
            c.execute("UPDATE vendors SET phone_on_file='+15550100001' WHERE name='CleanVendor Inc'")
        with patch.dict(os.environ, {"MOCK_CALLS": "false"}), \
             patch("backend.calling.caller.start_verification_call", side_effect=fake_call):
            invoice_id = self.flagged()
            from backend.main import queue
            client = self.client()
            queue.pending.put_nowait(invoice_id)
            self.wait(invoice_id, {"calling"})
            deadline = time.time() + 2
            while time.time() < deadline and not seen:
                time.sleep(0.02)
            self.assertEqual((seen["phone"], seen["amount"], seen["last4"]), ("+15550100001", "$480.00", "9921"))
            r = client.post("/webhooks/call-result", headers={"X-PayCrew-Secret": "offline-test-secret"},
                            json={"invoice_id": invoice_id, "provider_call_id": "web_1_abcd", "outcome": "denied",
                                  "summary": "Vendor did not change bank details."})
            self.assertEqual(r.status_code, 200)
            invoice = self.wait(invoice_id, {"blocked"})
            self.assertEqual(invoice["call"]["summary"], "Vendor did not change bank details.")

    def test_kenil_routes_are_mounted(self):
        client = self.client()
        self.assertFalse(client.get("/phone/15550100001/poll").json()["ringing"])
        self.assertIn("text/html", client.get("/phone/15550100001").headers["content-type"])

    def test_restart_recovers_interrupted_calls(self):
        invoice_id = self.flagged()
        with db.connect() as c:
            c.execute("UPDATE invoices SET status='calling' WHERE id=?", (invoice_id,))
            c.execute("INSERT INTO calls(invoice_id,attempt,status) VALUES (?,1,'in_progress')", (invoice_id,))
        self.client()  # startup runs recovery
        invoice = self.wait(invoice_id, {"awaiting_approval"})
        self.assertEqual(invoice["call"]["attempt"], 2)


class PhoneEndedTests(OfflineTestCase):
    """The vendor's phone page reports the end of a web call with its transcript."""

    def setUp(self):
        super().setUp()
        self.patch("backend.main.UPLOADS", self.root)

    def test_hangup_with_transcript_settles_the_call(self):
        from fastapi.testclient import TestClient
        from backend.main import app, queue
        from backend.calling import web_phone
        self.patch("backend.calling.web_phone.CLASSIFIERS", [])  # keyword fallback, no Claude
        posted = []
        self.patch("backend.calling.web_phone.post_result_sync", side_effect=lambda r: posted.append(r))
        with TestClient(app) as client:
            web_phone.ANSWERED["web_9_x"] = {"call_id": "web_9_x", "token": "t", "v": {"invoice_id": "9"}}
            r = client.post("/phone/15550100001/ended", json={"call_id": "web_9_x", "transcript": [
                {"role": "assistant", "text": "Did your team request that change?"},
                {"role": "user", "text": "No, we didn't change anything."}]})
            self.assertEqual(r.json(), {"ok": True, "outcome": "denied"})
            self.assertEqual(posted[0]["outcome"], "denied")
            self.assertIn("VENDOR: No, we didn't", posted[0]["transcript"])
            self.assertEqual(client.post("/phone/15550100001/ended", json={"call_id": "web_9_x"}).json(), {"ok": False})

    def test_keyword_fallback(self):
        from backend.calling.web_phone import _heuristic
        self.assertEqual(_heuristic("AGENT: ok?\nVENDOR: Yes we switched banks")[0], "confirmed")
        self.assertEqual(_heuristic("AGENT: ok?\nVENDOR: nope that's not us")[0], "denied")
        self.assertEqual(_heuristic("AGENT: hello?")[0], "no_answer")
