"""Generate the 5 PayCrew demo invoices (MASTER.md section 6) into backend/demo_invoices/.

Run: .venv/bin/python scripts/make_demo_pdfs.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "demo_invoices"

# vendor, invoice_number, amount_cents, bank_last4 on the invoice, due date, PO, line item
INVOICES = [
    ("OfficeSupplyCo", "INV-441", 80000, "0011", "2026-10-12", "PO-1001", "Printer paper and toner"),
    ("PaperWorks Ltd", "INV-400", 35000, "0022", "2026-10-15", "PO-1002", "Custom letterhead printing"),
    ("TechSoftware Inc", "INV-9921", 120000, "0033", "2026-10-20", "PO-1003", "Annual software licenses"),
    ("CleanVendor Inc", "INV-910", 48000, "9921", "2026-10-05", None, "Office cleaning, September"),
    ("FastConsult LLC", "INV-F42", 450000, "7788", "2026-10-30", "PO-1005", "Finance process consulting"),
]


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def make(vendor, number, cents, last4, due, po, item):
    path = OUT / f"{number}.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter
    y = h - 72

    c.setFont("Helvetica-Bold", 22)
    c.drawString(72, y, vendor)
    c.setFont("Helvetica", 11)
    c.drawRightString(w - 72, y, "INVOICE")
    y -= 40

    lines = [
        f"Invoice #: {number}",
        "Invoice date: 2026-09-28",
        f"Due date: {due}",
        f"PO reference: {po}" if po else "PO reference: none",
        "Bill to: Acme Supplies",
    ]
    for line in lines:
        c.drawString(72, y, line)
        y -= 18

    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Description")
    c.drawRightString(w - 72, y, "Amount")
    y -= 6
    c.line(72, y, w - 72, y)
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawString(72, y, item)
    c.drawRightString(w - 72, y, money(cents))
    y -= 30

    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, f"Total due: {money(cents)} USD")
    y -= 40

    c.setFont("Helvetica", 11)
    c.drawString(72, y, "Payment instructions")
    y -= 18
    c.drawString(72, y, f"Pay to account ending {last4}")
    y -= 18
    c.drawString(72, y, "ACH / wire transfer only")

    c.showPage()
    c.save()
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for row in INVOICES:
        print("wrote", make(*row).name)
