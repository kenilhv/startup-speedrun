"""Brainbase payer connector. Every HTTP call is faked; no task is ever created for real."""
import json
import os
import time
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from . import brainbase_payer, db, payments, workflow
from .test_support import OfflineTestCase

URL = "https://buy.example.com/officesupply"
ENABLED = {
    "BRAINBASE_PAYMENTS_ENABLED": "true", "BRAINBASE_TOKEN": "bb_offline_token",
    "BRAINBASE_PAYER_AGENT_ID": "agent-offline", "BRAINBASE_PAY_MAX_CENTS": "100000",
}


def response(status, body=None):
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://offline"))


class FakeBrainbase:
    def __init__(self):
        self.posts, self.tasks, self.events = [], {}, {}
        self.post_error = None

    def __call__(self, method, path, *, json_body=None, headers=None, params=None):
        if method == "POST":
            self.posts.append((json_body, headers))
            if self.post_error:
                raise self.post_error
            return response(201, {"id": "task-1", "agent_id": json_body["agent_id"], "status": "queued"})
        if path.endswith("/events"):
            return response(200, {"items": self.events.get(path.split("/")[3], [])})
        return response(200, {"id": path.split("/")[3], "status": self.tasks.get(path.split("/")[3], "running")})

    def finish(self, report, status="success", task="task-1", extra_text="Done."):
        self.tasks[task] = status
        text = f"{extra_text}\n{json.dumps(report)}" if report is not None else extra_text
        self.events[task] = [{"id": "e2", "type": "assistant.message", "data": {"content": [{"type": "text", "content": text}]}},
                             {"id": "e1", "type": "user.message", "data": {"content": [{"type": "text", "content": "PAYCREW PAYMENT REQUEST"}]}}]


class BrainbasePayerTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, ENABLED))
        with db.connect() as c:
            c.execute("UPDATE vendors SET payment_url=? WHERE name='OfficeSupplyCo'", (URL,))
        self.bb = FakeBrainbase()
        self.patch("backend.brainbase_payer._http", side_effect=self.bb)

    def dispatch(self, **invoice):
        invoice_id = self.invoice(**invoice)
        result = payments.pay(invoice_id)
        workflow.transition(invoice_id, "paying")
        return invoice_id, result

    def report(self, key, **changes):
        return {"paycrew_payment_ref": key, "status": "paid", "amount_cents": 100, "currency": "usd",
                "merchant_url": URL + "?session=abc", "order_id": "ord_123", **changes}

    def test_disabled_by_default_and_unconfigured_is_refused_without_a_row(self):
        with patch.dict(os.environ, {"BRAINBASE_PAYMENTS_ENABLED": ""}):
            self.assertFalse(brainbase_payer.enabled())
        with patch.dict(os.environ, {"BRAINBASE_TOKEN": ""}):
            invoice_id = self.invoice()
            with self.assertRaisesRegex(brainbase_payer.PayerNotConfigured, "Brainbase \\+ Link.*BRAINBASE_TOKEN"):
                payments.pay(invoice_id)
            self.assertIsNone(self.payment(invoice_id))
        self.assertEqual(self.bb.posts, [])

    def test_dispatch_reserves_before_post_and_sends_idempotency_key(self):
        seen = {}

        def post(method, path, **kw):
            seen["row"] = self.payment(invoice_id)
            return self.bb(method, path, **kw)
        self.patch("backend.brainbase_payer._http", side_effect=post)
        invoice_id = self.invoice()
        dispatch = payments.pay(invoice_id)
        self.assertIsInstance(dispatch, brainbase_payer.Dispatch)
        self.assertEqual(seen["row"]["status"], "pending")
        body, headers = self.bb.posts[0]
        self.assertEqual(headers["Idempotency-Key"], seen["row"]["idempotency_key"])
        self.assertEqual(body["agent_id"], "agent-offline")
        message = body["initial_messages"][0]["content"]
        self.assertIn(URL, message); self.assertIn("$1.00", message); self.assertIn(seen["row"]["idempotency_key"], message)
        row = self.payment(invoice_id)
        self.assertEqual((row["provider"], row["provider_ref"], row["status"]), ("brainbase", "task-1", "pending"))

    def test_refusals_never_contact_brainbase(self):
        cases = [dict(amount_cents=500000), dict(currency="eur"), dict(bank_last4_claimed="9999"),
                 dict(status="flagged"), dict(risk_level="critical")]
        for change in cases:
            with self.subTest(change=change):
                invoice_id = self.invoice(**change)
                with self.assertRaises(payments.PaymentConflict):
                    payments.pay(invoice_id)
        with db.connect() as c:
            c.execute("UPDATE vendors SET payment_url=NULL")
        with self.assertRaisesRegex(payments.PaymentConflict, "checkout link"):
            payments.pay(self.invoice())
        self.assertEqual(self.bb.posts, [])

    def test_matching_report_settles_once(self):
        invoice_id, dispatch = self.dispatch()
        key = self.payment(invoice_id)["idempotency_key"]
        self.assertEqual(brainbase_payer.poll_once(), [])  # still running
        self.bb.finish(self.report(key))
        [outcome] = brainbase_payer.poll_once()
        self.assertEqual((outcome.kind, outcome.order_id), ("settled", "ord_123"))
        invoice = db.get_invoice(invoice_id)
        self.assertEqual(invoice["status"], "settled")
        self.assertEqual((invoice["payment"]["status"], invoice["payment"]["order_id"], invoice["payment"]["task_id"]), ("paid", "ord_123", "task-1"))
        self.assertEqual(brainbase_payer.poll_once(), [])

    def test_mismatched_reports_never_settle(self):
        bad = [dict(amount_cents=999), dict(currency="eur"), dict(merchant_url="https://evil.example/pay"),
               dict(order_id=""), dict(paycrew_payment_ref="paycrew-bb-other"), dict(status="processing")]
        for change in bad:
            with self.subTest(change=change):
                with db.connect() as c:
                    c.execute("DELETE FROM payments"); c.execute("DELETE FROM invoices")
                invoice_id, _ = self.dispatch()
                key = self.payment(invoice_id)["idempotency_key"]
                self.bb.finish(self.report(key, **change))
                [outcome] = brainbase_payer.poll_once()
                self.assertEqual(outcome.kind, "escalated")
                self.assertEqual(self.payment(invoice_id)["status"], "unknown")
                self.assertEqual(db.get_invoice(invoice_id)["status"], "paying")

    def test_stripe_payment_link_handoff_counts_as_same_merchant(self):
        self.assertTrue(brainbase_payer._same_merchant("https://checkout.stripe.com/c/pay/cs_live_1", "https://buy.stripe.com/abc"))
        self.assertFalse(brainbase_payer._same_merchant("https://checkout.evil.com/pay", "https://buy.stripe.com/abc"))

    def test_failed_task_or_report_is_failed_not_paid(self):
        invoice_id, _ = self.dispatch()
        self.bb.finish(None, status="fail")
        [outcome] = brainbase_payer.poll_once()
        self.assertIn("ended with 'fail'", outcome.message)
        self.assertEqual(self.payment(invoice_id)["status"], "failed")

    def test_two_reports_are_ambiguous(self):
        invoice_id, _ = self.dispatch()
        key = self.payment(invoice_id)["idempotency_key"]
        self.bb.finish(self.report(key), extra_text=json.dumps(self.report(key, order_id="ord_other")))
        [outcome] = brainbase_payer.poll_once()
        self.assertEqual(outcome.kind, "escalated")
        self.assertEqual(self.payment(invoice_id)["status"], "unknown")

    def test_timeout_marks_unknown(self):
        invoice_id, _ = self.dispatch()
        [outcome] = brainbase_payer.poll_once(now=time.time() + 4000)
        self.assertIn("no final result", outcome.message)
        self.assertEqual(self.payment(invoice_id)["status"], "unknown")

    def test_ambiguous_post_keeps_key_and_is_not_resent_automatically(self):
        self.bb.post_error = httpx.ConnectTimeout("offline")
        invoice_id = self.invoice(status="awaiting_approval")
        with self.assertRaises(payments.PaymentError):
            payments.pay(invoice_id, "owner")
        row = self.payment(invoice_id)
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(len(self.bb.posts), 1)
        self.assertEqual(brainbase_payer.poll_once(), [])  # nothing to poll without a task id
        # An explicit re-approval resends the identical request with the same key.
        self.bb.post_error = None
        payments.pay(invoice_id, "owner")
        self.assertEqual(self.bb.posts[1][1]["Idempotency-Key"], row["idempotency_key"])
        self.assertEqual(self.bb.posts[0][0], self.bb.posts[1][0])

    def test_running_task_blocks_second_dispatch(self):
        invoice_id = self.invoice(status="awaiting_approval")
        payments.pay(invoice_id, "owner")
        with self.assertRaisesRegex(payments.PaymentConflict, "already running"):
            payments.pay(invoice_id, "owner")
        self.assertEqual(len(self.bb.posts), 1)

    def test_conflict_on_post_is_unknown_not_failed(self):
        self.patch("backend.brainbase_payer._http", return_value=response(409, {"detail": "exists"}))
        invoice_id = self.invoice()
        with self.assertRaisesRegex(payments.PaymentError, "may already be running"):
            payments.pay(invoice_id)
        self.assertEqual(self.payment(invoice_id)["status"], "unknown")

    def test_manual_resolution_unblocks_reset(self):
        invoice_id, _ = self.dispatch()
        with db.connect() as c:
            c.execute("UPDATE payments SET status='unknown'")
        with self.assertRaises(ValueError):
            db.clear_demo()
        brainbase_payer.resolve_manually(invoice_id, paid_order_id="ord_seen_in_link")
        self.assertEqual(db.get_invoice(invoice_id)["status"], "settled")
        self.assertEqual(db.get_invoice(invoice_id)["payment"]["order_id"], "ord_seen_in_link")
        db.clear_demo()

    def test_demo_charge_pays_token_amount_and_shows_invoice_total(self):
        with patch.dict(os.environ, {"BRAINBASE_DEMO_CHARGE_CENTS": "100", "BRAINBASE_PAY_MAX_CENTS": "100"}):
            invoice_id, _ = self.dispatch(amount_cents=80000)
            message = self.bb.posts[0][0]["initial_messages"][0]["content"]
            self.assertIn("$1.00 USD (100 cents)", message)
            self.assertIn("invoice total of $800.00", message)
            key = self.payment(invoice_id)["idempotency_key"]
            self.bb.finish(self.report(key, amount_cents=100))
            [outcome] = brainbase_payer.poll_once()
        self.assertIn("$800.00", outcome.message)
        payment = db.get_invoice(invoice_id)["payment"]
        self.assertEqual((payment["status"], payment["amount_cents"], payment["charged_cents"]), ("paid", 80000, 100))

    def test_demo_charge_rejects_a_report_of_the_full_amount(self):
        with patch.dict(os.environ, {"BRAINBASE_DEMO_CHARGE_CENTS": "100"}):
            invoice_id, _ = self.dispatch(amount_cents=80000)
            key = self.payment(invoice_id)["idempotency_key"]
            self.bb.finish(self.report(key, amount_cents=80000))
            [outcome] = brainbase_payer.poll_once()
        self.assertEqual(outcome.kind, "escalated")

    def test_refused_post_is_failed(self):
        self.patch("backend.brainbase_payer._http", return_value=response(403, {"detail": "no"}))
        invoice_id = self.invoice()
        with self.assertRaisesRegex(payments.PaymentConflict, "HTTP 403"):
            payments.pay(invoice_id)
        self.assertEqual(self.payment(invoice_id)["status"], "failed")


class BrainbasePayerApiTests(BrainbasePayerTests):
    """End to end through the API: approve -> paying -> watcher settles."""

    def test_approve_moves_to_paying_then_settles(self):
        from backend.main import app, payment_watcher
        self.patch("backend.main.UPLOADS", self.root)
        payment_watcher.interval = 0.05
        with TestClient(app) as client:
            invoice_id = self.invoice(status="awaiting_approval")
            response = client.post(f"/invoices/{invoice_id}/approve")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "paying")
            key = self.payment(invoice_id)["idempotency_key"]
            self.bb.finish(self.report(key))
            deadline = time.time() + 2
            while time.time() < deadline and db.get_invoice(invoice_id)["status"] != "settled":
                time.sleep(0.03)
            self.assertEqual(db.get_invoice(invoice_id)["status"], "settled")
            self.assertEqual(client.post(f"/invoices/{invoice_id}/approve").status_code, 409)
