"""Check extract_invoice against the MASTER.md section 6 table for all 5 demo PDFs.

Run from backend/: .venv/bin/python scripts/test_extract.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.analysis_agent import KEYS, extract_invoice  # noqa: E402

EXPECTED = {
    "INV-441": ("OfficeSupplyCo", 80000, "0011"),
    "INV-400": ("PaperWorks Ltd", 35000, "0022"),
    "INV-9921": ("TechSoftware Inc", 120000, "0033"),
    "INV-910": ("CleanVendor Inc", 48000, "9921"),
    "INV-F42": ("FastConsult LLC", 450000, "7788"),
}

failures = 0
for number, (vendor, cents, last4) in EXPECTED.items():
    got = extract_invoice((ROOT / "demo_invoices" / f"{number}.pdf").read_bytes())
    want = {"vendor_name": vendor, "invoice_number": number, "amount_cents": cents,
            "currency": "usd", "bank_last4": last4}
    bad = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    if "error" in got:
        bad["error"] = got["error"]
    if set(got) != set(KEYS) and "error" not in got:
        bad["keys"] = sorted(got)
    status = "OK  " if not bad else "FAIL"
    failures += bool(bad)
    print(f"{status} {number}: {got}" + (f"\n     mismatches: {bad}" if bad else ""))

print(f"\n{len(EXPECTED) - failures}/{len(EXPECTED)} passed")
sys.exit(1 if failures else 0)
