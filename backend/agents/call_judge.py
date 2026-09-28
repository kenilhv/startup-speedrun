"""Decide a verification call's outcome from its transcript (used when the phone page reports the end)."""
import asyncio
from typing import Literal

from pydantic import BaseModel

from .crew import _claude, claude_ready


class CallVerdict(BaseModel):
    outcome: Literal["confirmed", "denied", "unclear"]
    summary: str


PROMPT = """This is the transcript of a verification call. The AGENT asked {vendor} whether they really asked
{company} to pay invoice {invoice} ({amount}) to a NEW bank account ending in {last4}.

outcome:
- confirmed: the VENDOR clearly said yes, their team requested the change
- denied: the VENDOR clearly said no, or did not recognize the request
- unclear: anything else (unsure, wrong person, no real answer)
summary: one or two plain-English sentences for a finance dashboard describing what the vendor said.

Transcript:
{transcript}"""


async def classify(transcript: str, v: dict):
    if not claude_ready() or not transcript.strip():
        raise RuntimeError("Claude unavailable or empty transcript")
    verdict = await asyncio.to_thread(_claude, [{"type": "text", "text": PROMPT.format(
        vendor=v.get("vendor_name"), company=v.get("company_name"), invoice=v.get("invoice_number"),
        amount=v.get("amount_display"), last4=v.get("bank_last4_claimed"), transcript=transcript[:6000])}],
        CallVerdict, "low", 1500)
    return verdict.outcome, verdict.summary
