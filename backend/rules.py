from . import db

def score(fields, vendor):
    if not vendor:
        return "high", ["Unknown vendor"]
    if fields.get("bank_last4") and fields["bank_last4"] != vendor.get("bank_last4_on_file"):
        return "critical", [f"Bank details changed: invoice says ••••{fields['bank_last4']}, file says ••••{vendor['bank_last4_on_file']}"]
    if fields.get("invoice_number"):
        with db.connect() as c:
            found = c.execute("SELECT 1 FROM invoices WHERE vendor_id=? AND invoice_number=? AND id!=?", (vendor["id"], fields["invoice_number"], fields.get("id", -1))).fetchone()
        if found: return "high", ["Duplicate invoice"]
    po = fields.get("po_reference")
    if po:
        with db.connect() as c:
            order = c.execute("SELECT * FROM purchase_orders WHERE po_number=?", (po,)).fetchone()
        if order and order["vendor_id"] != vendor["id"]:
            return "high", [f"{po} belongs to a different vendor"]
        amount = fields.get("amount_cents")
        if order and amount and order["amount_cents"] != amount:
            return "high", [f"Amount does not match {po}: invoice ${amount / 100:,.2f}, PO ${order['amount_cents'] / 100:,.2f}"]
    return "low", ["All checks passed"]
