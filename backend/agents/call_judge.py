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

Judge the VENDOR's final, clear answer. If they hesitate, use filler words or change their mind before
settling on a clear yes or no, use that final answer and mention the hesitation in the summary. A
"confirmed" outcome never pays by itself: the business owner still approves or rejects it.

outcome:
- confirmed: the vendor's final answer is clearly yes, their team requested the change
- denied: the vendor's final answer is clearly no, or they do not recognize the request
- unclear: no clear final yes or no (unsure, wrong person, silence, only questions)
summary: one or two plain-English sentences for a finance dashboard describing what the vendor said,
including any hesitation or change of answer.

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
