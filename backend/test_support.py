"""Offline fixtures: temporary data, fake provider responses, no outbound sockets."""
import copy
import ipaddress
import os
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import db

_REAL_CONNECT = socket.socket.connect
_REAL_SOCKETPAIR = socket.socketpair
_INTERNAL_SOCKETPAIR = threading.local()


def _guarded_connect(connection, address):
    # Python's Windows socketpair fallback uses TCP loopback for asyncio's
    # internal wakeup pipe. Only allow it during that stdlib operation.
    if getattr(_INTERNAL_SOCKETPAIR, "active", False) and isinstance(address, tuple):
        if ipaddress.ip_address(address[0]).is_loopback:
            return _REAL_CONNECT(connection, address)
    raise AssertionError("Network disabled in offline tests")


def _internal_socketpair(*args, **kwargs):
    previous = getattr(_INTERNAL_SOCKETPAIR, "active", False)
    _INTERNAL_SOCKETPAIR.active = True
    try:
        return _REAL_SOCKETPAIR(*args, **kwargs)
    finally:
        _INTERNAL_SOCKETPAIR.active = previous


@contextmanager
def offline_network():
    with patch("socket.socket.connect", new=_guarded_connect), \
         patch("socket.socket.connect_ex", side_effect=AssertionError("Network disabled in offline tests")), \
         patch("socket.socketpair", new=_internal_socketpair):
        yield


class OfflineTestCase(unittest.TestCase):
    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        super().setUp()
        self.enterContext(offline_network())
        self.patch("dotenv.load_dotenv", return_value=False)
        environment = patch.dict(os.environ, {
            "MOCK_CALLS": "true", "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "",
            "SLACK_BOT_TOKEN": "", "SLACK_CHANNEL_ID": "", "SLACK_SIGNING_SECRET": "",
            "CLEAN_VENDOR_PHONE": "", "FAST_CONSULT_PHONE": "",
            "PAYCREW_WEBHOOK_SECRET": "offline-test-secret",
            "PAYMENT_PROVIDER": "brainbase_link", "BRAINBASE_PAYMENTS_ENABLED": "",
            "BRAINBASE_TOKEN": "", "BRAINBASE_PAYER_AGENT_ID": "", "BRAINBASE_PAY_MAX_CENTS": "",
            "VENDOR_PAYMENT_URLS": "", "MOCK_CALL_DELAY_SECONDS": "0", "MOCK_CALL_OUTCOMES": "", "BRAINBASE_DEMO_CHARGE_CENTS": "", "ANTHROPIC_API_KEY": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="paycrew-offline-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch("backend.db.DB_PATH", self.root / "paycrew.db")
        from .seed import seed
        seed()
        with db.connect() as c:
            c.execute("UPDATE vendors SET stripe_account_id='acct_offline_' || id")
        self.transfers = {}
        self.create_transfer = Mock(side_effect=self.fake_transfer)
        self.gateway = SimpleNamespace(v1=SimpleNamespace(
            transfers=SimpleNamespace(create=self.create_transfer)))
        self.patch("backend.payments.test_client", return_value=self.gateway)
        self.patch("backend.slack_notify._post", return_value=None)

    def fake_transfer(self, params, options):
        key = options["idempotency_key"]
        if key not in self.transfers:
            self.transfers[key] = {
                **copy.deepcopy(params), "object": "transfer",
                "id": f"tr_offline_{len(self.transfers) + 1}", "livemode": False,
                "reversed": False, "amount_reversed": 0,
            }
        return copy.deepcopy(self.transfers[key])

    def invoice(self, **changes):
        vendor = db.find_vendor("OfficeSupplyCo")
        with db.connect() as c:
            invoice_id = c.execute("""INSERT INTO invoices
                (vendor_id,vendor_name_raw,invoice_number,amount_cents,currency,
                 bank_last4_claimed,status,risk_level)
                VALUES (?,?,'OFFLINE-1',100,'usd','0011','analyzing','low')""",
                (vendor["id"], vendor["name"])).lastrowid
        if changes:
            db.update_invoice(invoice_id, **changes)
        return invoice_id

    def payment(self, invoice_id):
        with db.connect() as c:
            row = c.execute("SELECT * FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone()
            return dict(row) if row else None
