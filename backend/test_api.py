"""Run: py -m unittest backend.test_api"""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ["MOCK_CALLS"] = "true"
from fastapi.testclient import TestClient
from backend import db

class PayCrewApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "paycrew.db"
        from backend.main import app
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_seeds_contract_vendors(self):
        vendors = self.client.get("/vendors").json()
        self.assertEqual({v["name"] for v in vendors}, {"OfficeSupplyCo", "PaperWorks Ltd", "TechSoftware Inc", "CleanVendor Inc", "FastConsult LLC"})

    def test_upload_rejects_non_pdf(self):
        response = self.client.post("/invoices/upload", files=[("files", ("invoice.txt", b"no", "text/plain"))])
        self.assertEqual(response.status_code, 400)

    def test_approval_requires_correct_state(self):
        with db.connect() as c:
            invoice_id = c.execute("INSERT INTO invoices(filename,pdf_path,status,amount_cents) VALUES (?,?,?,?)", ("a.pdf", "a.pdf", "received", 100)).lastrowid
        self.assertEqual(self.client.post(f"/invoices/{invoice_id}/approve").status_code, 409)

    def _upload(self, body):
        response = self.client.post("/invoices/upload", files=[("files", ("invoice.pdf", body.encode(), "application/pdf"))])
        self.assertEqual(response.status_code, 200)
        return response.json()[0]["id"]

    def _wait_for(self, invoice_id, statuses):
        deadline = time.time() + 2
        while time.time() < deadline:
            invoice = self.client.get(f"/invoices/{invoice_id}").json()
            if invoice["status"] in statuses: return invoice
            time.sleep(.03)
        self.fail(f"invoice {invoice_id} did not reach {statuses}: {invoice}")

    def test_clean_invoice_settles(self):
        invoice_id = self._upload("Vendor: OfficeSupplyCo\nInvoice Number: INV-441\nTotal: $800.00\nBank: 0011")
        invoice = self._wait_for(invoice_id, {"settled"})
        self.assertEqual(invoice["risk_level"], "low")
        self.assertEqual(invoice["payment"]["status"], "paid")

    def test_confirmed_bank_change_needs_approval_then_settles(self):
        invoice_id = self._upload("Vendor: FastConsult LLC\nInvoice Number: INV-F42\nTotal: $4500.00\nBank: 7788")
        invoice = self._wait_for(invoice_id, {"awaiting_approval"})
        self.assertEqual(invoice["risk_level"], "critical")
        paid = self.client.post(f"/invoices/{invoice_id}/approve")
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(paid.json()["status"], "settled")

    def test_unknown_vendor_escalates_without_entering_call_queue(self):
        invoice_id = self._upload("Vendor: Totally Unknown Co\nInvoice Number: X-1\nTotal: $99.00\nBank: 1234")
        invoice = self._wait_for(invoice_id, {"escalated"})
        self.assertEqual(invoice["risk_reasons"], ["Unknown vendor"])
        self.assertIsNone(invoice["call"])

    def test_duplicate_invoice_is_flagged(self):
        first = self._upload("Vendor: OfficeSupplyCo\nInvoice Number: INV-DUP\nTotal: $10.00\nBank: 0011")
        self._wait_for(first, {"settled"})
        duplicate = self._upload("Vendor: OfficeSupplyCo\nInvoice Number: INV-DUP\nTotal: $10.00\nBank: 0011")
        invoice = self._wait_for(duplicate, {"awaiting_approval"})
        self.assertEqual(invoice["risk_reasons"], ["Duplicate invoice"])

    def test_webhook_requires_secret_and_valid_payload(self):
        os.environ["PAYCREW_WEBHOOK_SECRET"] = "test-secret"
        with db.connect() as c:
            invoice_id = c.execute("INSERT INTO invoices(filename,pdf_path,status) VALUES (?,?,?)", ("a.pdf", "a.pdf", "calling")).lastrowid
            c.execute("INSERT INTO calls(invoice_id,attempt,status) VALUES (?,?,?)", (invoice_id, 1, "in_progress"))
        payload = {"invoice_id": invoice_id, "outcome": "denied", "summary": "No"}
        self.assertEqual(self.client.post("/webhooks/call-result", json=payload).status_code, 401)
        accepted = self.client.post("/webhooks/call-result", json=payload, headers={"X-PayCrew-Secret":"test-secret"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.client.post("/webhooks/call-result", json=payload, headers={"X-PayCrew-Secret":"test-secret"}).status_code, 409)
        del os.environ["PAYCREW_WEBHOOK_SECRET"]

    def test_full_invoice_contract_has_vendor_call_and_payment_keys(self):
        invoice_id = self._upload("Vendor: OfficeSupplyCo\nInvoice Number: INV-CONTRACT\nTotal: $10.00\nBank: 0011")
        invoice = self._wait_for(invoice_id, {"settled"})
        self.assertTrue({"vendor", "call", "payment", "risk_reasons", "bank_last4_claimed", "bank_last4_on_file"}.issubset(invoice))
        self.assertEqual(invoice["vendor"]["name"], "OfficeSupplyCo")

if __name__ == "__main__": unittest.main()
