"""Slack Socket Mode: receive Approve / Reject button clicks without a public URL.

Runs when SLACK_APP_TOKEN (xapp-...) is set. Clicks call the same approve/reject logic as the board,
then the Slack message is updated with who decided and what happened. /webhooks/slack (HTTP, signed)
still works for apps that use a request URL instead.
"""
import asyncio
import logging
import os

log = logging.getLogger("paycrew.slack")


def _usd(cents):
    return f"${(cents or 0) / 100:,.2f}"


def outcome_text(invoice, approved, actor):
    vendor = (invoice.get("vendor") or {}).get("name") or invoice.get("vendor_name_raw") or "vendor"
    amount = _usd(invoice.get("amount_cents"))
    if not approved:
        return f":no_entry: *Rejected by {actor}*. {amount} to *{vendor}* ({invoice.get('invoice_number')}) will not be paid."
    if invoice.get("status") == "settled":
        return f":white_check_mark: *Approved by {actor}* and paid: {amount} to *{vendor}* ({invoice.get('invoice_number')})."
    return (f":white_check_mark: *Approved by {actor}*. The Brainbase payer agent is paying {amount} to *{vendor}* "
            f"({invoice.get('invoice_number')}); approve the spend in Link when asked.")


class SlackSocket:
    def __init__(self, decide):
        self.decide = decide  # async (invoice_id, approved, actor) -> invoice dict; raises HTTPException
        self.client = None
        self.loop = None

    def start(self):
        app_token, bot_token = os.getenv("SLACK_APP_TOKEN", "").strip(), os.getenv("SLACK_BOT_TOKEN", "").strip()
        if not app_token.startswith("xapp-") or not bot_token:
            return False
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient

        self.loop = asyncio.get_running_loop()
        self.client = SocketModeClient(app_token=app_token, web_client=WebClient(token=bot_token))
        self.client.socket_mode_request_listeners.append(self._on_request)
        self.client.connect()
        log.info("Slack Socket Mode connected")
        return True

    def stop(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    def _on_request(self, client, req):
        from slack_sdk.socket_mode.response import SocketModeResponse

        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))  # ack within 3s
        if req.type != "interactive":
            return
        payload = req.payload or {}
        actions = payload.get("actions") or []
        if not actions or actions[0].get("action_id") not in {"approve_invoice", "reject_invoice"}:
            return
        action = actions[0]
        approved = action["action_id"] == "approve_invoice"
        user = payload.get("user") or {}
        actor = user.get("name") or user.get("username") or "Slack user"
        channel = (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id")
        ts = (payload.get("message") or {}).get("ts") or (payload.get("container") or {}).get("message_ts")
        original = (payload.get("message") or {}).get("blocks") or []
        try:
            invoice_id = int(action["value"])
            future = asyncio.run_coroutine_threadsafe(self.decide(invoice_id, approved, f"{actor} (Slack)"), self.loop)
            invoice = future.result(timeout=60)
            text = outcome_text(invoice, approved, actor)
        except Exception as exc:  # HTTPException from decide, timeouts, bad values
            detail = getattr(exc, "detail", None) or str(exc)
            text = f":warning: Could not {'approve' if approved else 'reject'}: {detail}"
            log.warning("Slack %s failed: %s", action.get("action_id"), detail)
        if channel and ts:
            # keep the original summary, replace the buttons with the outcome
            blocks = [b for b in original if b.get("type") != "actions"]
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": text}]})
            try:
                client.web_client.chat_update(channel=channel, ts=ts, text=text, blocks=blocks)
            except Exception:
                log.exception("Could not update the Slack message")
