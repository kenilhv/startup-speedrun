"""Slack Block Kit notifications and verified interactive-button handling."""
import asyncio
import os
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

def _client(): return WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

def _post(blocks, text):
    channel = os.getenv("SLACK_CHANNEL_ID")
    if not channel or not os.getenv("SLACK_BOT_TOKEN"): return None
    return _client().chat_postMessage(channel=channel, text=text, blocks=blocks)

async def notify_blocked(invoice, summary):
    vendor = (invoice.get("vendor") or {}).get("name", invoice.get("vendor_name_raw", "Unknown vendor"))
    amount = f"${(invoice.get('amount_cents') or 0) / 100:,.2f}"
    call = (invoice.get("call") or {}).get("summary")
    blocks = [{"type":"section","text":{"type":"mrkdwn","text":f":rotating_light: *Fraud stopped* · {invoice.get('invoice_number')}\n{amount} payment to *{vendor}* blocked." + ("" if summary.startswith("Fraud stopped") else f"\n{summary}") + (f"\n>{call}" if call else "")}}]
    return await asyncio.to_thread(_post, blocks, "PayCrew blocked a payment")

async def notify_approval(invoice, summary):
    vendor = (invoice.get("vendor") or {}).get("name", invoice.get("vendor_name_raw", "Unknown vendor"))
    amount = f"${(invoice.get('amount_cents') or 0) / 100:,.2f}"
    call = (invoice.get("call") or {}).get("summary")
    reasons = "; ".join(invoice.get("risk_reasons") or [])
    blocks = [{"type":"section","text":{"type":"mrkdwn","text":f":telephone_receiver: *Approval needed* · {invoice.get('invoice_number')}\n*{vendor}* — {amount}\n{reasons}\nVendor confirmed the bank change by phone." + (f"\n>{call}" if call else "")}}, {"type":"actions","elements":[{"type":"button","text":{"type":"plain_text","text":"Approve"},"style":"primary","action_id":"approve_invoice","value":str(invoice["id"])},{"type":"button","text":{"type":"plain_text","text":"Reject"},"style":"danger","action_id":"reject_invoice","value":str(invoice["id"])}]}]
    return await asyncio.to_thread(_post, blocks, "PayCrew payment approval needed")

async def notify_escalated(invoice, reason):
    blocks = [{"type":"section","text":{"type":"mrkdwn","text":f":warning: *Invoice escalated* · {invoice.get('invoice_number') or 'new invoice'}\n{reason}" + ("" if "needs a human" in reason.lower() else "\nNeeds a human.")}}]
    return await asyncio.to_thread(_post, blocks, "PayCrew invoice escalated")

async def notify_paid(invoice, message):
    vendor = (invoice.get("vendor") or {}).get("name", invoice.get("vendor_name_raw", "vendor"))
    amount = f"${(invoice.get('amount_cents') or 0) / 100:,.2f}"
    blocks = [{"type":"section","text":{"type":"mrkdwn","text":f":moneybag: *Paid* · {invoice.get('invoice_number')}\n{amount} to *{vendor}*.\n{message}"}}]
    return await asyncio.to_thread(_post, blocks, "PayCrew paid an invoice")

def valid_signature(body, timestamp, signature):
    secret = os.getenv("SLACK_SIGNING_SECRET")
    return bool(secret) and SignatureVerifier(secret).is_valid_request(body, {"X-Slack-Request-Timestamp": timestamp or "", "X-Slack-Signature": signature or ""})
