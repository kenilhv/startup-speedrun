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
    return "low", ["All checks passed"]
