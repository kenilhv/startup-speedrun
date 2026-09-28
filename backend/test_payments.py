"""Payment state-machine checks. No provider requests or real money involved."""
import hashlib
import hmac
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import stripe
from fastapi.testclient import TestClient

from . import db, payments, stripe_gateway, workflow
from .test_support import OfflineTestCase


class PaymentSafetyTests(OfflineTestCase):
    def test_missing_placeholder_and_live_credentials_are_refused(self):
        with patch("backend.stripe_gateway.stripe.StripeClient") as constructor:
            for key in ("", "sk_test_...", "sk_live_offline_not_a_credential", "rk_live_offline"):
                with self.subTest(key_type=key.split("_")[:2]), patch.dict(os.environ, {"STRIPE_SECRET_KEY": key, "PAYMENT_PROVIDER": "stripe_test"}):
                    with self.assertRaises(stripe_gateway.StripeConfigurationError):
                        stripe_gateway.test_client()
            constructor.assert_not_called()

    def test_brainbase_route_disables_legacy_stripe_even_with_a_key(self):
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_offline_placeholder"}), \
             patch("backend.stripe_gateway.stripe.StripeClient") as constructor:
            with self.assertRaisesRegex(stripe_gateway.StripeConfigurationError, "Legacy Stripe calls are disabled"):
                stripe_gateway.test_client()
            constructor.assert_not_called()

    def test_missing_key_does_not_create_simulated_payment(self):
        invoice_id = self.invoice()
        with patch("backend.payments.test_client", stripe_gateway.test_client):
            with self.assertRaises(stripe_gateway.StripeConfigurationError):
                payments.pay(invoice_id)
        self.assertIsNone(self.payment(invoice_id))
        self.assertEqual(db.get_invoice(invoice_id)["status"], "analyzing")

    def test_network_connections_are_blocked(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(AssertionError, "Network disabled"):
                connection.connect(("127.0.0.1", 1))

    def test_verified_transfer_settles_invoice_once(self):
        invoice_id = self.invoice()
        first = payments.pay(invoice_id)
        second = payments.pay(invoice_id)
        self.assertEqual(first.transfer_id, second.transfer_id)
        self.assertEqual(self.create_transfer.call_count, 1)
        self.assertEqual(len(db.list_events()), 1)
        self.assertEqual(db.get_invoice(invoice_id)["status"], "settled")
        self.assertIsNotNone(first.activity)
        self.assertIsNone(second.activity)

    def test_actual_sdk_resource_shape_is_supported_offline(self):
        self.create_transfer.side_effect = lambda params, opts: stripe.Transfer.construct_from(
            self.fake_transfer(params, opts), None)
        invoice_id = self.invoice()
        payments.pay(invoice_id)
        self.assertEqual(db.get_invoice(invoice_id)["status"], "settled")

    def test_ineligible_states_do_not_contact_provider(self):
        for status in ("received", "flagged", "calling", "blocked", "escalated", "awaiting_approval"):
            with self.subTest(status=status):
                with self.assertRaises(payments.PaymentConflict):
                    payments.pay(self.invoice(status=status))
        self.create_transfer.assert_not_called()

    def test_invalid_amount_currency_bank_and_destination_do_not_send(self):
        for changes in ({"amount_cents": 0}, {"amount_cents": -1}, {"amount_cents": 100_000_000},
                        {"currency": "eur"}, {"bank_last4_claimed": "9999"},
                        {"bank_last4_claimed": None}, {"invoice_number": ""}, {"vendor_id": None}):
            with self.subTest(changes=changes), self.assertRaises(payments.PaymentConflict):
                payments.pay(self.invoice(**changes))
        self.create_transfer.assert_not_called()

    def test_high_risk_auto_payment_is_refused(self):
        with self.assertRaises(payments.PaymentConflict):
            payments.pay(self.invoice(risk_level="high"))
        self.create_transfer.assert_not_called()

    def test_approved_invoice_records_approver(self):
        invoice_id = self.invoice(status="awaiting_approval", risk_level="critical", bank_last4_claimed="9999")
        payments.pay(invoice_id, approved_by="offline-owner")
        self.assertEqual(self.payment(invoice_id)["approved_by"], "offline-owner")

    def test_timeout_retains_key_and_frozen_payload_for_retry(self):
        invoice_id = self.invoice()
        self.create_transfer.side_effect = TimeoutError("offline timeout")
        with self.assertRaises(payments.PaymentError):
            payments.pay(invoice_id)
        original = self.payment(invoice_id)
        self.assertEqual(original["status"], "unknown")
        db.update_invoice(invoice_id, amount_cents=900)
        self.create_transfer.side_effect = self.fake_transfer
        payments.pay(invoice_id)
        retry = self.create_transfer.call_args
        self.assertEqual(retry.args[0]["amount"], 100)
        self.assertEqual(retry.args[1]["idempotency_key"], original["idempotency_key"])

    def test_expired_unknown_payment_requires_manual_reconciliation(self):
        invoice_id = self.invoice()
        self.create_transfer.side_effect = TimeoutError()
        with self.assertRaises(payments.PaymentError):
            payments.pay(invoice_id)
        with db.connect() as c:
            c.execute("UPDATE payments SET started_at=? WHERE invoice_id=?", (time.time() - 24 * 3600, invoice_id))
        with self.assertRaises(payments.PaymentConflict):
            payments.pay(invoice_id)
        self.assertEqual(self.create_transfer.call_count, 1)

    def test_definite_provider_rejection_is_not_paid(self):
        invoice_id = self.invoice()
        self.create_transfer.side_effect = stripe.InvalidRequestError("offline invalid request", "amount", http_status=400)
        with self.assertRaises(payments.PaymentError):
            payments.pay(invoice_id)
        first_key = self.payment(invoice_id)["idempotency_key"]
        self.assertEqual(self.payment(invoice_id)["status"], "failed")
        self.create_transfer.side_effect = self.fake_transfer
        payments.pay(invoice_id)
        self.assertNotEqual(self.payment(invoice_id)["idempotency_key"], first_key)

    def test_mismatched_or_live_transfer_never_settles(self):
        invalid = ({"livemode": True}, {"amount": 999}, {"currency": "eur"},
                   {"destination": "acct_other"}, {"metadata": {}}, {"reversed": True},
                   {"amount_reversed": 1}, {"object": "charge"}, {"id": "ch_wrong"})
        for changes in invalid:
            with self.subTest(changes=changes):
                invoice_id = self.invoice()
                self.create_transfer.side_effect = lambda params, opts: {**self.fake_transfer(params, opts), **changes}
                with self.assertRaises(payments.PaymentError):
                    payments.pay(invoice_id)
                self.assertEqual(self.payment(invoice_id)["status"], "unknown")
                self.assertNotEqual(db.get_invoice(invoice_id)["status"], "settled")

    def test_concurrent_approvals_make_one_provider_call(self):
        invoice_id = self.invoice(status="awaiting_approval")
        entered, release = Event(), Event()
        def delayed(params, options):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("offline synchronization timeout")
            return self.fake_transfer(params, options)
        self.create_transfer.side_effect = delayed
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(payments.pay, invoice_id, "offline-owner")
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(payments.PaymentConflict):
                    payments.pay(invoice_id, "offline-owner")
            finally:
                release.set()
            self.assertTrue(first.result().transfer_id.startswith("tr_offline_"))
        self.assertEqual(self.create_transfer.call_count, 1)

    def test_unresolved_payment_cannot_be_rejected_or_reset(self):
        invoice_id = self.invoice(status="awaiting_approval")
        payments._reserve(invoice_id, "offline-owner")
        with self.assertRaises(workflow.InvalidTransition):
            workflow.transition(invoice_id, "blocked")
        with self.assertRaises(ValueError):
            db.clear_demo()
        self.assertIsNotNone(self.payment(invoice_id))

    def test_workflow_cannot_mark_invoice_settled_without_transfer(self):
        with self.assertRaises(workflow.InvalidTransition):
            workflow.transition(self.invoice(), "settled")

    def test_legacy_payment_without_request_record_is_not_trusted(self):
        invoice_id = self.invoice()
        with db.connect() as c:
            c.execute("INSERT INTO payments(invoice_id,amount_cents,status,stripe_transfer_id) VALUES (?,100,'paid','tr_legacy')", (invoice_id,))
        with self.assertRaises(payments.PaymentConflict):
            payments.pay(invoice_id)
        self.create_transfer.assert_not_called()


class StripeWebhookTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.invoice_id = self.invoice()
        reservation = payments._reserve(self.invoice_id, None)
        self.transfer = self.fake_transfer(json.loads(reservation["request_json"]),
                                           {"idempotency_key": reservation["idempotency_key"]})
        self.event = {"id": "evt_offline_1", "type": "transfer.created", "livemode": False,
                      "data": {"object": self.transfer}}

    def test_verified_event_is_idempotent(self):
        self.assertIsNotNone(payments.handle_transfer_event(self.event))
        self.assertIsNone(payments.handle_transfer_event(self.event))
        self.assertEqual(len(db.list_events()), 1)
        self.assertEqual(self.payment(self.invoice_id)["status"], "paid")

    def test_live_event_is_rejected(self):
        self.event["livemode"] = True
        with self.assertRaises(payments.PaymentMismatch):
            payments.handle_transfer_event(self.event)
        self.assertEqual(self.payment(self.invoice_id)["status"], "pending")

    def test_unrelated_event_is_ignored(self):
        self.transfer["metadata"]["paycrew_payment_ref"] = "unrelated"
        self.assertIsNone(payments.handle_transfer_event(self.event))
        self.assertEqual(self.payment(self.invoice_id)["status"], "pending")

    def test_wrong_amount_event_is_rejected(self):
        self.transfer["amount"] += 1
        with self.assertRaises(payments.PaymentMismatch):
            payments.handle_transfer_event(self.event)
        self.assertEqual(self.payment(self.invoice_id)["status"], "pending")

    def test_signed_http_event_and_replay(self):
        from .main import app
        body = json.dumps(self.event).encode()
        secret = "offline-webhook-secret"
        timestamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": secret}), TestClient(app) as client:
            headers = {"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"}
            self.assertEqual(client.post("/webhooks/stripe", content=body, headers=headers).status_code, 200)
            self.assertEqual(client.post("/webhooks/stripe", content=body, headers=headers).status_code, 200)
        self.assertEqual(len(db.list_events()), 1)

    def test_missing_or_invalid_http_signature_is_rejected(self):
        from .main import app
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "offline-webhook-secret"}), TestClient(app) as client:
            self.assertEqual(client.post("/webhooks/stripe", json=self.event).status_code, 400)
            response = client.post("/webhooks/stripe", json=self.event, headers={"Stripe-Signature": "t=1,v1=invalid"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.payment(self.invoice_id)["status"], "pending")


class SetupSafetyTests(OfflineTestCase):
    def test_setup_retry_preserves_original_payload_and_key(self):
        from .seed import _setup_request
        first = _setup_request("offline-setup", {"amount": 100})
        second = _setup_request("offline-setup", {"amount": 999})
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(json.loads(second["request_json"]), {"amount": 100})

    def test_funding_rerun_retrieves_existing_intent(self):
        from .seed import fund_platform
        client = Mock()
        result = {"id": "pi_offline", "livemode": False, "status": "succeeded", "amount": 2_000_000}
        client.v1.payment_intents.create.return_value = result
        client.v1.payment_intents.retrieve.return_value = result
        self.assertEqual(fund_platform(client), fund_platform(client))
        client.v1.payment_intents.create.assert_called_once()
        client.v1.payment_intents.retrieve.assert_called_once_with("pi_offline")

    def test_database_failure_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with db.connect() as c:
                c.execute("INSERT INTO events(message) VALUES ('must roll back')")
                raise RuntimeError("offline transaction failure")
        self.assertEqual(db.list_events(), [])
