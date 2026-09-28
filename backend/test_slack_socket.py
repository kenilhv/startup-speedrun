"""Slack Socket Mode button handling, with a fake Slack client. No network."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException

from .slack_socket import SlackSocket
from .test_support import OfflineTestCase


class SlackSocketTests(OfflineTestCase):
    def click(self, decide, action_id="approve_invoice", value="7"):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        sock = SlackSocket(decide)
        sock.loop = loop
        client = SimpleNamespace(send_socket_mode_response=Mock(), web_client=SimpleNamespace(chat_update=Mock()))
        req = SimpleNamespace(envelope_id="env1", type="interactive", payload={
            "actions": [{"action_id": action_id, "value": value}], "user": {"name": "shresth"},
            "channel": {"id": "C1"}, "message": {"ts": "1.2", "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "Approval needed"}},
                {"type": "actions", "elements": []}]}})
        sock._on_request(client, req)
        loop.call_soon_threadsafe(loop.stop)
        return client

    def test_approve_acks_decides_and_replaces_buttons(self):
        calls = []

        async def decide(invoice_id, approved, actor):
            calls.append((invoice_id, approved, actor))
            return {"status": "paying", "amount_cents": 80000, "invoice_number": "INV-F42", "vendor_name_raw": "FastConsult LLC"}
        client = self.click(decide)
        client.send_socket_mode_response.assert_called_once()
        self.assertEqual(calls, [(7, True, "shresth (Slack)")])
        kwargs = client.web_client.chat_update.call_args.kwargs
        self.assertEqual([b["type"] for b in kwargs["blocks"]], ["section", "context"])
        self.assertIn("Approved by shresth", kwargs["text"])
        self.assertIn("Link", kwargs["text"])

    def test_refused_decision_is_shown_not_raised(self):
        async def decide(*a):
            raise HTTPException(409, "Invoice is not awaiting approval")
        client = self.click(decide, "reject_invoice")
        self.assertIn("not awaiting approval", client.web_client.chat_update.call_args.kwargs["text"])

    def test_other_actions_are_ignored(self):
        decide = Mock()
        client = self.click(decide, "something_else")
        decide.assert_not_called()
        client.web_client.chat_update.assert_not_called()
