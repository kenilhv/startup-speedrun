import os

def start_verification_call(invoice_id, vendor_name, phone_on_file, invoice_number, amount_display, bank_last4_claimed, company_name="Acme Supplies"):
    """Kenil's real caller should replace this. Return None to await its webhook."""
    if os.getenv("MOCK_CALLS") == "true":
        return f"mock-{invoice_id}"
    return None
