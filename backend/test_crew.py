"""Analysis crew: six agents, live trace, Claude advisory only. Claude is faked; no network."""
import asyncio
import os
from pathlib import Path
from unittest.mock import patch

from . import db
from .agents import crew
from .test_support import OfflineTestCase

PDF = Path(__file__).parent / "demo_invoices"


class CrewTests(OfflineTestCase):
    def run_crew(self, pdf_bytes):
        with db.connect() as c:
            invoice_id = c.execute("INSERT INTO invoices(filename,pdf_path,status) VALUES ('x.pdf','x.pdf','analyzing')").lastrowid
        published = []

        async def publish(invoice):
            published.append([s["state"] for s in invoice["analysis"]])
        result = asyncio.run(crew.Crew(invoice_id, publish).run(pdf_bytes))
        return invoice_id, result, published

    def test_rules_only_crew_on_demo_pdfs(self):
        cases = {"INV-441": ("low", "auto_pay", "pass"), "INV-910": ("critical", "verify_by_phone", "fail"),
                 "INV-F42": ("critical", "verify_by_phone", "fail")}
        for number, (level, decision, fraud) in cases.items():
            with self.subTest(number=number):
                invoice_id, result, published = self.run_crew((PDF / f"{number}.pdf").read_bytes())
                self.assertEqual((result.level, result.decision), (level, decision))
                trace = db.get_invoice(invoice_id)["analysis"]
                self.assertEqual([s["id"] for s in trace], [a[0] for a in crew.AGENTS])
                self.assertTrue(all(s["state"] == "done" for s in trace))
                self.assertEqual(trace[3]["status"], fraud)
                self.assertIn("running", published[0])  # live progress was broadcast

    def test_po_matcher_findings(self):
        _, _, _ = self.run_crew((PDF / "INV-441.pdf").read_bytes())
        vendor = db.find_vendor("OfficeSupplyCo")
        self.assertEqual(crew.po_match(vendor, "PO-1001", 80000)[0], "pass")
        self.assertEqual(crew.po_match(vendor, "PO-1001", 99900)[0], "warn")
        self.assertEqual(crew.po_match(vendor, "PO-1005", 80000)[0], "fail")
        self.assertEqual(crew.po_match(vendor, None, 80000)[0], "warn")

    def test_claude_is_advisory_and_cannot_lower_risk(self):
        calls = []

        def fake(content, schema, *a, **k):
            calls.append(schema.__name__)
            if schema is crew.FraudReport:
                return crew.FraudReport(suspicious=False, signals=[], summary="Looks fine to me.")
            return crew.RiskExplanation(explanation="Bank details changed, so we call first.")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-offline"}), \
             patch("backend.agents.crew._claude", side_effect=fake), \
             patch("backend.agents.analysis_agent._extract_with_claude", side_effect=RuntimeError("offline")):
            invoice_id, result, _ = self.run_crew((PDF / "INV-910.pdf").read_bytes())
        self.assertEqual(calls, ["FraudReport", "RiskExplanation"])
        self.assertEqual(result.level, "critical")  # rules still decide
        trace = {s["id"]: s for s in db.get_invoice(invoice_id)["analysis"]}
        self.assertEqual(trace["fraud_signal"]["status"], "fail")  # rule-level bank mismatch kept
        self.assertEqual(trace["fraud_signal"]["engine"], "Claude")
        self.assertIn("call first", trace["risk_scorer"]["finding"])

    def test_claude_can_raise_risk_to_phone_check(self):
        def fake(content, schema, *a, **k):
            if schema is crew.FraudReport:
                return crew.FraudReport(suspicious=True, signals=[crew.Signal(severity="high", text="Asks for gift cards")],
                                        summary="Requests payment in gift cards.")
            return crew.RiskExplanation(explanation="Gift cards are a fraud sign.")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-offline"}), \
             patch("backend.agents.crew._claude", side_effect=fake), \
             patch("backend.agents.analysis_agent._extract_with_claude", side_effect=RuntimeError("offline")):
            _, result, _ = self.run_crew((PDF / "INV-441.pdf").read_bytes())
        self.assertEqual((result.level, result.decision), ("high", "verify_by_phone"))
        self.assertTrue(result.reasons[0].startswith("Fraud analyst:"))

    def test_po_rules_flag_mismatch_and_foreign_po(self):
        from backend import rules
        vendor = db.find_vendor("PaperWorks Ltd")
        self.assertEqual(rules.score({"id": 0, "bank_last4": "0022", "po_reference": "PO-1003", "amount_cents": 35000}, vendor)[0], "high")
        self.assertEqual(rules.score({"id": 0, "bank_last4": "0022", "po_reference": "PO-1002", "amount_cents": 99900}, vendor)[0], "high")
        self.assertEqual(rules.score({"id": 0, "bank_last4": "0022", "po_reference": "PO-1002", "amount_cents": 35000}, vendor)[0], "low")

    def test_claude_failure_falls_back_to_rules(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-offline"}), \
             patch("backend.agents.crew._claude", side_effect=TimeoutError("slow")), \
             patch("backend.agents.analysis_agent._extract_with_claude", side_effect=RuntimeError("offline")):
            invoice_id, result, _ = self.run_crew((PDF / "INV-441.pdf").read_bytes())
        self.assertEqual(result.decision, "auto_pay")
        trace = {s["id"]: s for s in db.get_invoice(invoice_id)["analysis"]}
        self.assertEqual(trace["fraud_signal"]["engine"], "rules")
        self.assertEqual(trace["risk_scorer"]["engine"], "rules")

    def test_unreadable_invoice_stops_the_crew(self):
        invoice_id, result, _ = self.run_crew(b"%PDF-1.4 garbage")
        self.assertTrue(result.error)
        self.assertEqual(db.get_invoice(invoice_id)["analysis"][0]["status"], "fail")
