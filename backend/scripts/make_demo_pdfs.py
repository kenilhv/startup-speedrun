"""Generate the 5 PayCrew demo invoices (MASTER.md section 6) as realistic vendor invoices.

Each vendor gets its own letterhead, colors, address, line items, tax and payment block. Values
match the demo table exactly (vendor, invoice number, total, bank last 4, PO). CleanVendor's invoice
carries the classic impersonation cues (changed bank details, urgency); FastConsult's announces a
genuine bank change. All names, addresses and numbers are fictional.

Run from backend/: .venv/bin/python scripts/make_demo_pdfs.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "demo_invoices"
W, H = letter
M = 0.75 * inch

BILL_TO = ["Acme Supplies Inc.", "Accounts Payable", "500 Market Street, Suite 1200", "San Francisco, CA 94105"]

VENDORS = [
    dict(
        number="INV-441", vendor="OfficeSupplyCo", legal="OfficeSupplyCo LLC", color="#1d4ed8", mark="OS",
        tagline="Office products & print supplies",
        address=["2200 Industrial Pkwy", "Hayward, CA 94545", "(510) 555-0142 · billing@officesupplyco.example"],
        date="September 14, 2026", due="2026-10-12", terms="Net 28", po="PO-1001",
        items=[("Copy paper, 8.5x11, 20lb, case of 10 reams", 12, 4550), ("Toner cartridge, black, high yield", 3, 5800),
               ("Desk organizer set", 2, 2000)],
        tax_rate=0.0, shipping=4000, bank="First Coast Bank", last4="0011",
        notes="Thank you for your business. Please reference the invoice number with your payment.",
    ),
    dict(
        number="INV-400", vendor="PaperWorks Ltd", legal="PaperWorks Ltd", color="#7c2d12", mark="PW",
        tagline="Custom printing & stationery",
        address=["88 Mill Lane", "Oakland, CA 94607", "(510) 555-0199 · accounts@paperworks.example"],
        date="September 17, 2026", due="2026-10-15", terms="Net 28", po="PO-1002",
        items=[("Custom letterhead, 24lb linen, 1,000 sheets", 1, 18500), ("#10 envelopes, printed, 1,000", 1, 12000),
               ("Design proof revisions", 1, 4500)],
        tax_rate=0.0, shipping=0, bank="Bay Mutual Bank", last4="0022",
        notes="Payment due within 28 days. Late balances accrue 1.5% monthly.",
    ),
    dict(
        number="INV-9921", vendor="TechSoftware Inc", legal="TechSoftware Inc.", color="#0f766e", mark="TS",
        tagline="Business software & licensing",
        address=["1 Innovation Way, Floor 9", "San Jose, CA 95110", "(408) 555-0170 · ar@techsoftware.example"],
        date="September 20, 2026", due="2026-10-20", terms="Net 30", po="PO-1003",
        items=[("Productivity Suite, annual license (per seat)", 10, 9000), ("Admin console add-on, annual", 1, 30000)],
        tax_rate=0.0, shipping=0, bank="Silicon Valley Trust", last4="0033",
        notes="Licenses renew automatically. Contact your account manager to change seat counts.",
    ),
    dict(
        number="INV-910", vendor="CleanVendor Inc", legal="CleanVendor Inc.", color="#15803d", mark="CV",
        tagline="Commercial cleaning services",
        address=["415 Harbor Blvd", "Richmond, CA 94804", "(510) 555-0117 · billing@cleanvendor.example"],
        date="September 26, 2026", due="2026-10-05", terms="Due on receipt", po=None,
        items=[("Office cleaning, September (4 weekly visits)", 4, 10500), ("Carpet spot treatment", 1, 6000)],
        tax_rate=0.0, shipping=0, bank="Coastal Digital Bank", last4="9921",
        notice="IMPORTANT: Our banking details have changed. Please update your records and send this "
               "payment to the new account below. Payments to our old account will be returned.",
        notes="Kindly process today to avoid a service interruption. Reply to this email with any questions.",
    ),
    dict(
        number="INV-F42", vendor="FastConsult LLC", legal="FastConsult LLC", color="#6d28d9", mark="FC",
        tagline="Finance process consulting",
        address=["950 Pine Street, Suite 300", "San Francisco, CA 94108", "(415) 555-0163 · finance@fastconsult.example"],
        date="September 24, 2026", due="2026-10-30", terms="Net 30", po="PO-1005",
        items=[("AP process assessment, phase 1 (hours)", 20, 18000), ("Controls documentation workshop", 1, 90000)],
        tax_rate=0.0, shipping=0, bank="Pacific Commerce Bank", last4="7788",
        notice="Please note: we moved our business banking to Pacific Commerce Bank in August. "
               "Our new account details are below; call your FastConsult contact if you'd like to confirm.",
        notes="Thank you for choosing FastConsult.",
    ),
]


PROFILE = {v["vendor"]: v for v in VENDORS}


def like(vendor, **changes):
    """Another invoice from the same company: same letterhead, new details."""
    base = {k: val for k, val in PROFILE[vendor].items() if k not in ("notice",)}
    base.update(changes)
    return base


# More invoices from the same companies: some normal, some odd. (file stem, invoice, what the crew should do)
EXTRA = [
    ("01-good-officesupply", like("OfficeSupplyCo", number="INV-443", date="September 22, 2026", due="2026-10-20", po="PO-1006",
        items=[("Breakroom coffee, 12 x 2lb bags", 5, 3100), ("Paper towels, case of 12", 4, 2000), ("Disinfecting wipes, case", 2, 1500)],
        shipping=0), "Low risk → auto-pay"),
    ("02-good-paperworks", like("PaperWorks Ltd", number="INV-401", date="September 23, 2026", due="2026-10-21", po="PO-1007",
        items=[("Business cards, 500 per box", 5, 2400)]), "Low risk → auto-pay"),
    ("03-good-techsoftware", like("TechSoftware Inc", number="INV-9930", date="September 25, 2026", due="2026-10-25", po="PO-1008",
        items=[("Security add-on, annual (per seat)", 10, 24000)]), "Low risk → auto-pay"),
    ("04-good-cleanvendor", like("CleanVendor Inc", number="INV-911", date="September 27, 2026", due="2026-10-27", terms="Net 30", po=None,
        last4="0042", bank="Harbor Savings Bank",
        items=[("Window washing, exterior, 2 floors", 1, 32000)], notes="Thank you for your business."), "Low risk → auto-pay (no PO is only a note)"),
    ("05-good-fastconsult", like("FastConsult LLC", number="INV-F43", date="September 27, 2026", due="2026-10-27", po="PO-1009",
        last4="0055", bank="Golden Gate Bank",
        items=[("Month-end close support (hours)", 10, 18000)]), "Low risk → auto-pay"),
    ("06-weird-duplicate-officesupply", PROFILE["OfficeSupplyCo"], "Same INV-441 sent again → Duplicate → verify by phone"),
    ("07-weird-inflated-officesupply", like("OfficeSupplyCo", number="INV-446", date="September 27, 2026", due="2026-10-05", terms="Due on receipt",
        items=[("Copy paper, 8.5x11, 20lb, case of 10 reams", 120, 4550), ("Toner cartridge, black, high yield", 30, 5800), ("Expedite fee", 1, 29000)],
        shipping=0, notes="Rush order. Please pay promptly."), "Amount doesn't match PO-1001 → verify by phone"),
    ("08-weird-newbank-officesupply", like("OfficeSupplyCo", number="INV-447", date="September 28, 2026", due="2026-09-30", terms="Due on receipt",
        bank="Meridian Online Bank", last4="4471", shipping=4000,
        notice="URGENT: Due to an audit our old account is frozen. Send this and all future payments to our new "
               "account below. Do not call our office; email billing@officesupply-co.example instead.",
        notes="Payment must be received within 24 hours to avoid account suspension."), "Bank changed + urgency → critical → verify by phone"),
    ("09-weird-wrong-po-paperworks", like("PaperWorks Ltd", number="INV-403", date="September 28, 2026", due="2026-10-26", po="PO-1003"),
        "PO-1003 belongs to TechSoftware → verify by phone"),
    ("10-weird-giftcards-cleanvendor", like("CleanVendor Inc", number="INV-913", date="September 28, 2026", due="2026-09-29", terms="Due on receipt",
        last4="0042", bank="Harbor Savings Bank", po=None,
        items=[("Emergency deep clean, after hours", 1, 380000), ("Hazmat disposal surcharge", 1, 95000)],
        notice="Our finance manager is travelling. To avoid late fees, you may also settle this invoice with "
               "retail gift cards; email the card numbers to our manager directly.",
        notes="Please treat as confidential and process today."), "Claude flags gift-card request → verify by phone"),
    ("11-weird-lookalike-vendor", dict(like("OfficeSupplyCo", number="INV-9001", date="September 28, 2026", due="2026-10-01",
        bank="Meridian Online Bank", last4="4471", po=None, terms="Due on receipt"),
        vendor="Office Supply Co.", legal="Office Supply Co. Ltd", mark="OS",
        address=["77 Commerce Rd, Unit 4", "Reno, NV 89502", "(775) 555-0108 · billing@officesupply-co.example"]),
        "Not in vendor directory → human review"),
]


def money(cents):
    return f"${cents / 100:,.2f}"


def make(v, path=None):
    c = canvas.Canvas(str(path or OUT / f"{v['number']}.pdf"), pagesize=letter)
    c.setTitle(f"{v['legal']} invoice {v['number']}")
    brand = colors.HexColor(v["color"])
    light = colors.Color(brand.red, brand.green, brand.blue, alpha=0.08)
    ink, muted, line = colors.HexColor("#111827"), colors.HexColor("#6b7280"), colors.HexColor("#e5e7eb")

    # header band + logo mark
    c.setFillColor(brand); c.rect(0, H - 0.22 * inch, W, 0.22 * inch, stroke=0, fill=1)
    c.roundRect(M, H - 1.35 * inch, 0.7 * inch, 0.7 * inch, 10, stroke=0, fill=1)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(M + 0.35 * inch, H - 1.1 * inch, v["mark"])
    c.setFillColor(ink); c.setFont("Helvetica-Bold", 20)
    c.drawString(M + 0.9 * inch, H - 0.93 * inch, v["vendor"])
    c.setFillColor(muted); c.setFont("Helvetica", 9.5)
    c.drawString(M + 0.9 * inch, H - 1.12 * inch, v["tagline"])
    y = H - 1.55 * inch
    for part in v["address"]:
        c.drawString(M, y, part); y -= 13

    # title + meta box
    c.setFillColor(brand); c.setFont("Helvetica-Bold", 26)
    c.drawRightString(W - M, H - 0.98 * inch, "INVOICE")
    meta = [("Invoice #:", v["number"]), ("Invoice date:", v["date"]), ("Due date:", v["due"]),
            ("Terms:", v["terms"]), ("PO reference:", v["po"] or "none")]
    my = H - 1.3 * inch
    for label, value in meta:
        c.setFillColor(muted); c.setFont("Helvetica", 9.5); c.drawRightString(W - M - 1.45 * inch, my, label)
        c.setFillColor(ink); c.setFont("Helvetica-Bold", 9.5); c.drawRightString(W - M, my, value)
        my -= 14

    # bill to
    y = H - 2.55 * inch
    c.setFillColor(muted); c.setFont("Helvetica-Bold", 8.5); c.drawString(M, y, "BILL TO")
    c.setFillColor(ink); c.setFont("Helvetica", 10)
    for i, part in enumerate(BILL_TO):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 10); c.drawString(M, y - 14 - i * 13, part)

    # notice banner (bank change)
    y = H - 3.55 * inch
    if v.get("notice"):
        c.setFillColor(colors.HexColor("#fef3c7")); c.setStrokeColor(colors.HexColor("#f59e0b"))
        c.roundRect(M, y - 26, W - 2 * M, 36, 6, stroke=1, fill=1)
        c.setFillColor(colors.HexColor("#92400e")); c.setFont("Helvetica-Bold", 8.8)
        text = c.beginText(M + 10, y - 2); text.setFont("Helvetica-Bold", 8.8)
        words, lineb = v["notice"].split(), ""
        for w in words:
            if c.stringWidth(lineb + " " + w, "Helvetica-Bold", 8.8) > W - 2 * M - 20:
                text.textLine(lineb.strip()); lineb = ""
            lineb += " " + w
        text.textLine(lineb.strip()); c.drawText(text)
        y -= 50

    # line items
    cols = [M, W - M - 3.1 * inch, W - M - 1.9 * inch, W - M]
    c.setFillColor(light); c.rect(M, y - 6, W - 2 * M, 20, stroke=0, fill=1)
    c.setFillColor(brand); c.setFont("Helvetica-Bold", 9)
    c.drawString(cols[0] + 8, y, "DESCRIPTION"); c.drawRightString(cols[1] + 0.5 * inch, y, "QTY")
    c.drawRightString(cols[2] + 0.35 * inch, y, "UNIT PRICE"); c.drawRightString(cols[3] - 8, y, "AMOUNT")
    y -= 24
    subtotal = 0
    c.setFont("Helvetica", 9.5)
    for desc, qty, unit in v["items"]:
        amount = qty * unit; subtotal += amount
        c.setFillColor(ink); c.drawString(cols[0] + 8, y, desc)
        c.drawRightString(cols[1] + 0.5 * inch, y, str(qty))
        c.drawRightString(cols[2] + 0.35 * inch, y, money(unit))
        c.drawRightString(cols[3] - 8, y, money(amount))
        c.setStrokeColor(line); c.line(M, y - 7, W - M, y - 7)
        y -= 22

    # totals
    tax = round(subtotal * v["tax_rate"])
    total = subtotal + tax + v["shipping"]
    y -= 6
    rows = [("Subtotal", money(subtotal))]
    if v["shipping"]:
        rows.append(("Shipping & handling", money(v["shipping"])))
    rows.append(("Sales tax", money(tax) if tax else "$0.00"))
    for label, value in rows:
        c.setFillColor(muted); c.setFont("Helvetica", 9.5); c.drawRightString(W - M - 1.4 * inch, y, label)
        c.setFillColor(ink); c.drawRightString(W - M - 8, y, value); y -= 15
    c.setFillColor(brand); c.roundRect(W - M - 3.2 * inch, y - 12, 3.2 * inch, 24, 5, stroke=0, fill=1)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 11.5)
    c.drawString(W - M - 3.1 * inch, y - 4, f"Total due: {money(total)} USD")
    y -= 44

    # payment instructions
    c.setFillColor(muted); c.setFont("Helvetica-Bold", 8.5); c.drawString(M, y, "PAYMENT INSTRUCTIONS")
    c.setFillColor(ink); c.setFont("Helvetica", 9.5)
    lines = [f"Remit by ACH or wire to {v['legal']}", f"Bank: {v['bank']}",
             f"Pay to account ending {v['last4']}", "Routing number on file with your AP team",
             f"Reference: {v['number']}"]
    for i, part in enumerate(lines):
        c.setFont("Helvetica-Bold" if "ending" in part else "Helvetica", 9.5)
        c.drawString(M, y - 15 - i * 13, part)
    c.setFillColor(muted); c.setFont("Helvetica-Oblique", 9)
    c.drawString(M, y - 15 - len(lines) * 13 - 10, v["notes"])

    # footer
    c.setStrokeColor(line); c.line(M, 0.85 * inch, W - M, 0.85 * inch)
    c.setFillColor(muted); c.setFont("Helvetica", 8)
    c.drawString(M, 0.65 * inch, f"{v['legal']} · {v['address'][0]}, {v['address'][1]}")
    c.drawRightString(W - M, 0.65 * inch, "Page 1 of 1")
    c.showPage(); c.save()
    return total


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    expected = {"INV-441": 80000, "INV-400": 35000, "INV-9921": 120000, "INV-910": 48000, "INV-F42": 450000}
    for v in VENDORS:
        total = make(v)
        assert total == expected[v["number"]], (v["number"], total)
        print(f"wrote {v['number']}.pdf  total {money(total)}")
    extra = OUT / "extra"
    extra.mkdir(exist_ok=True)
    readme = ["# Extra demo invoices", "", "Same five companies, some normal and some odd. Upload any mix.", "",
              "| File | Invoice | Total | Expected |", "|---|---|---|---|"]
    for stem, v, expect in EXTRA:
        total = make(v, extra / f"{stem}.pdf")
        readme.append(f"| {stem}.pdf | {v['number']} | {money(total)} | {expect} |")
        print(f"wrote extra/{stem}.pdf  {v['number']}  {money(total)}")
    (extra / "README.md").write_text("\n".join(readme) + "\n")
