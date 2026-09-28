import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).with_name("paycrew.db")

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try: yield conn; conn.commit()
    finally: conn.close()

def _ensure_columns(conn, table, columns):
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in existing: conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS vendors (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, phone_on_file TEXT, email TEXT, bank_last4_on_file TEXT, stripe_account_id TEXT);
        CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY, vendor_id INTEGER, vendor_name_raw TEXT, invoice_number TEXT, amount_cents INTEGER, currency TEXT DEFAULT 'usd', due_date TEXT, bank_last4_claimed TEXT, source TEXT DEFAULT 'upload', pdf_path TEXT, filename TEXT, status TEXT NOT NULL DEFAULT 'received', risk_level TEXT, risk_reasons TEXT, fields_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL, provider_call_id TEXT, status TEXT DEFAULT 'queued', outcome TEXT, summary TEXT, transcript TEXT, attempt INTEGER NOT NULL DEFAULT 1, started_at TEXT, ended_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY, invoice_id INTEGER UNIQUE NOT NULL, stripe_transfer_id TEXT, amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, invoice_id INTEGER, type TEXT DEFAULT 'activity', message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        _ensure_columns(c, "vendors", {"email":"TEXT"})
        _ensure_columns(c, "invoices", {"vendor_name_raw":"TEXT", "currency":"TEXT DEFAULT 'usd'", "due_date":"TEXT", "bank_last4_claimed":"TEXT", "source":"TEXT DEFAULT 'upload'"})
        _ensure_columns(c, "calls", {"status":"TEXT DEFAULT 'queued'", "transcript":"TEXT", "started_at":"TEXT", "ended_at":"TEXT"})
        _ensure_columns(c, "payments", {"stripe_transfer_id":"TEXT"})
        _ensure_columns(c, "events", {"type":"TEXT DEFAULT 'activity'"})

def _decode(data):
    for key in ("risk_reasons", "fields_json"):
        if data.get(key): data[key] = json.loads(data[key])
    return data

INVOICE_SQL = """
SELECT i.*, v.id vendor_id_join, v.name vendor_name, v.phone_on_file vendor_phone_on_file, v.bank_last4_on_file vendor_bank_last4_on_file,
 c.status call_status, c.outcome call_outcome, c.summary call_summary, c.attempt call_attempt,
 p.id payment_id, p.stripe_transfer_id payment_stripe_transfer_id, p.amount_cents payment_amount_cents, p.status payment_status
FROM invoices i LEFT JOIN vendors v ON v.id=i.vendor_id
LEFT JOIN calls c ON c.id=(SELECT id FROM calls WHERE invoice_id=i.id ORDER BY id DESC LIMIT 1)
LEFT JOIN payments p ON p.invoice_id=i.id
"""

def invoice_object(row):
    if not row: return None
    data = _decode(dict(row))
    vendor = {"id":data.pop("vendor_id_join"), "name":data.pop("vendor_name"), "phone_on_file":data.pop("vendor_phone_on_file"), "bank_last4_on_file":data.pop("vendor_bank_last4_on_file")}
    data["vendor"] = vendor if vendor["id"] else None
    data["bank_last4_on_file"] = vendor["bank_last4_on_file"] if vendor["id"] else None
    call = {k:data.pop(f"call_{k}") for k in ("status","outcome","summary","attempt")}
    data["call"] = call if call["status"] else None
    payment_id = data.pop("payment_id")
    data["payment"] = {"id":payment_id, "stripe_transfer_id":data.pop("payment_stripe_transfer_id"), "amount_cents":data.pop("payment_amount_cents"), "status":data.pop("payment_status")} if payment_id else None
    return data

def get_invoice(invoice_id):
    with connect() as c: return invoice_object(c.execute(INVOICE_SQL + " WHERE i.id=?", (invoice_id,)).fetchone())
def list_invoices():
    with connect() as c: return [invoice_object(r) for r in c.execute(INVOICE_SQL + " ORDER BY i.id DESC")]
def list_vendors():
    with connect() as c: return [dict(r) for r in c.execute("SELECT * FROM vendors ORDER BY name")]
def list_events():
    with connect() as c: return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")]

def update_invoice(invoice_id, **fields):
    for key in ("risk_reasons", "fields_json"):
        if key in fields and not isinstance(fields[key], str): fields[key] = json.dumps(fields[key])
    values = list(fields.values()) + [invoice_id]
    sql = ", ".join(f"{k}=?" for k in fields) + ", updated_at=CURRENT_TIMESTAMP"
    with connect() as c: c.execute(f"UPDATE invoices SET {sql} WHERE id=?", values)
    return get_invoice(invoice_id)

def find_vendor(name):
    import re
    norm = lambda v: re.sub(r"\b(inc|llc|ltd)\b\.?", "", v.lower()).strip()
    with connect() as c:
        for row in c.execute("SELECT * FROM vendors"):
            if norm(row["name"]) == norm(name or ""): return dict(row)
    return None

def add_event(invoice_id, message, event_type="activity"):
    with connect() as c: c.execute("INSERT INTO events(invoice_id,type,message) VALUES (?,?,?)", (invoice_id, event_type, message))
def clear_demo():
    with connect() as c:
        for table in ("invoices", "calls", "payments", "events"): c.execute(f"DELETE FROM {table}")
