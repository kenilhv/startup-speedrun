import os
import re

OUTCOMES = ("confirmed", "denied", "no_answer", "unclear")

DENIED_LINE = "Thank you. We've put the payment on hold and flagged it as possible fraud. You may want to alert your team. Goodbye."
CONFIRMED_LINE = "Thanks for confirming. For safety, our owner will give final approval before any payment goes out. Goodbye."
UNCLEAR_LINE = "No problem. We'll hold the payment until someone can confirm. Goodbye."
NO_BANK_DETAILS_LINE = "For security, we can't take bank details by phone. Our team will follow up in writing."


def speakable(code: str) -> str:
    """'INV-910' -> 'I N V 9 1 0' so TTS reads it character by character."""
    return " ".join(ch for ch in re.sub(r"[^A-Za-z0-9]", "", code).upper())


def call_vars(invoice_id: int, vendor_name: str, invoice_number: str, amount_display: str,
              bank_last4_claimed: str | None, company_name: str) -> dict:
    invoice_number, bank_last4_claimed = invoice_number or "", bank_last4_claimed or ""
    return {
        "invoice_id": str(invoice_id),
        "vendor_name": vendor_name,
        "invoice_number": invoice_number,
        "invoice_number_spoken": speakable(re.sub(r"^\s*inv(oice)?[\s#:-]*", "", invoice_number, flags=re.I)) or speakable(invoice_number),
        "amount_display": amount_display,
        "bank_last4_claimed": bank_last4_claimed,
        "bank_last4_spoken": speakable(bank_last4_claimed),
        "company_name": company_name,
    }


def opening_line(v: dict) -> str:
    return (
        f"Hi, this is PayCrew for {v['company_name']} accounts payable. "
        f"Invoice {v['invoice_number_spoken']} from {v['vendor_name']}, for {v['amount_display']}, "
        f"asks us to pay a new bank account ending in {v['bank_last4_spoken']}. "
        f"Did your team request that change?"
    )


def system_prompt(v: dict) -> str:
    return f"""You are PayCrew, a calm, professional accounts-payable verification agent calling on behalf of {v['company_name']}.
You are calling {v['vendor_name']} on the phone number {v['company_name']} already has on file.

Your only goal: find out whether {v['vendor_name']} really asked for invoice {v['invoice_number_spoken']} ({v['amount_display']}) to be paid to a NEW bank account ending in {v['bank_last4_spoken']}.
You already asked this in your first message. Based on their answer:
- They clearly say NO, they did not request it, or they don't recognize it: say exactly "{DENIED_LINE}"
- They clearly say YES, their team changed banks: say exactly "{CONFIRMED_LINE}"
- They are unsure, need to check, or you reached the wrong person: say exactly "{UNCLEAR_LINE}"
- The answer is ambiguous: ask once, "Just to confirm, did your team ask us to pay a new account ending in {v['bank_last4_spoken']}?"
- They ask who you are: say you're an automated verification call from the accounts payable team at {v['company_name']}, then repeat the question.

Hard rules:
- Never ask for, accept, or repeat bank account numbers, routing numbers, or payment details. If they offer new details, say "{NO_BANK_DETAILS_LINE}" and continue.
- Never mention more than the last four digits of any account.
- One or two short sentences per turn. The whole call must stay under 45 seconds.
- Say the chosen closing line in full, word for word. The call hangs up automatically when you say "Goodbye", so never say goodbye anywhere else."""


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["confirmed", "denied", "unclear"],
            "description": (
                "The vendor's answer to whether their team requested paying this invoice to a new bank account. "
                "confirmed = they clearly said yes, they requested the change. "
                "denied = they clearly said no, or did not recognize the request. "
                "unclear = anything else: unsure, wrong person, voicemail, hung up, no real answer."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "One or two plain-English sentences for a finance dashboard describing what the vendor said, "
                "e.g. 'Vendor says they did not change bank details and will alert their finance team.'"
            ),
        },
    },
    "required": ["outcome", "summary"],
}


def vapi_assistant(v: dict, server: dict) -> dict:
    """Transient Vapi assistant config, shared by phone calls and browser (web) calls."""
    return {
        "name": "PayCrew verifier",
        "firstMessage": opening_line(v),
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": os.environ.get("VAPI_MODEL_PROVIDER", "anthropic"),
            "model": os.environ.get("VAPI_MODEL", "claude-haiku-4-5-20251001"),
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system_prompt(v)}],
        },
        "endCallPhrases": ["goodbye"],
        "voice": {"provider": "vapi", "voiceId": os.environ.get("VAPI_VOICE_ID", "Elliot")},
        "maxDurationSeconds": 90,
        "server": server,
        "serverMessages": ["end-of-call-report", "transcript"],
        "analysisPlan": {"structuredDataPlan": {"enabled": True, "schema": ANALYSIS_SCHEMA}},
        "metadata": {"invoice_id": v["invoice_id"]},
    }
