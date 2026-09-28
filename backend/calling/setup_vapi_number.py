"""Check the Twilio number + verified caller IDs, import the number into Vapi, write VAPI_PHONE_NUMBER_ID to .env.

Run from backend/:  python -m calling.setup_vapi_number
Verify a phone for a trial account:  python -m calling.setup_vapi_number --verify +14155550123
Reads .env: TWILIO_ACCOUNT_SID, TWILIO_PHONE_NUMBER, VAPI_API_KEY and either
TWILIO_API_KEY + TWILIO_API_SECRET or TWILIO_AUTH_TOKEN. Never prints secrets.
"""
import os
import sys

import httpx
from dotenv import find_dotenv, load_dotenv, set_key

env_path = find_dotenv(usecwd=True) or find_dotenv()
if not env_path:
    sys.exit("No .env found. Create one in the project root first.")
load_dotenv(env_path)


def need(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"Missing {name} in {env_path}")
    return val


def mask(num: str) -> str:
    return f"...{num[-4:]}" if len(num) > 4 else num


account = need("TWILIO_ACCOUNT_SID")
number = need("TWILIO_PHONE_NUMBER")
vapi_key = need("VAPI_API_KEY")
api_key, api_secret = os.environ.get("TWILIO_API_KEY", "").strip(), os.environ.get("TWILIO_API_SECRET", "").strip()
auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
if api_key and api_secret:
    twilio_auth, vapi_creds = (api_key, api_secret), {"twilioApiKey": api_key, "twilioApiSecret": api_secret}
elif auth_token:
    twilio_auth, vapi_creds = (account, auth_token), {"twilioAuthToken": auth_token}
else:
    sys.exit("Set TWILIO_API_KEY + TWILIO_API_SECRET (or TWILIO_AUTH_TOKEN) in .env")

tw = f"https://api.twilio.com/2010-04-01/Accounts/{account}"

if len(sys.argv) == 3 and sys.argv[1] == "--verify":
    r = httpx.post(f"{tw}/OutgoingCallerIds.json", auth=twilio_auth, timeout=20,
                   data={"PhoneNumber": sys.argv[2], "FriendlyName": "PayCrew demo"})
    if r.is_error:
        sys.exit(f"Verification request failed ({r.status_code}): {r.json().get('message', r.text)}")
    print(f"Twilio is calling {mask(sys.argv[2])} now. Enter this code on the keypad: {r.json()['validation_code']}")
    sys.exit(0)

# 1. Twilio number exists and can do voice
r = httpx.get(f"{tw}/IncomingPhoneNumbers.json", params={"PhoneNumber": number}, auth=twilio_auth, timeout=20)
if r.is_error:
    sys.exit(f"Twilio auth/lookup failed ({r.status_code}): {r.json().get('message', r.text)}")
nums = r.json().get("incoming_phone_numbers", [])
if not nums:
    sys.exit(f"{number} is not a number on this Twilio account. Check TWILIO_PHONE_NUMBER (E.164, e.g. +14155550123).")
print(f"[ok] Twilio number {number} found, voice={nums[0].get('capabilities', {}).get('voice')}")

# 2. Verified caller IDs (trial accounts can only call these)
r = httpx.get(f"{tw}/OutgoingCallerIds.json", auth=twilio_auth, timeout=20)
verified = [c["phone_number"] for c in r.json().get("outgoing_caller_ids", [])] if r.is_success else []
print(f"[info] Verified phones: {', '.join(mask(n) for n in verified) or 'none yet'}")
print("       Trial accounts can only call these. Verify Zubair's, Shresth's and your own phone.")

# 3. Import into Vapi (reuse if already imported)
vh = {"Authorization": f"Bearer {vapi_key}"}
r = httpx.get("https://api.vapi.ai/phone-number", headers=vh, timeout=20)
if r.is_error:
    sys.exit(f"Vapi auth failed ({r.status_code}): {r.text}")
existing = next((p for p in r.json() if p.get("number") == number), None)
if existing:
    pn_id = existing["id"]
    print(f"[ok] Number already imported into Vapi")
else:
    r = httpx.post("https://api.vapi.ai/phone-number", headers=vh, timeout=30, json={
        "provider": "twilio", "number": number, "twilioAccountSid": account, "name": "PayCrew outbound", **vapi_creds})
    if r.is_error:
        sys.exit(f"Vapi import failed ({r.status_code}): {r.text}")
    pn_id = r.json()["id"]
    print("[ok] Imported number into Vapi")

set_key(env_path, "VAPI_PHONE_NUMBER_ID", pn_id, quote_mode="never")
set_key(env_path, "CALL_PROVIDER", "vapi", quote_mode="never")
print(f"[ok] Wrote VAPI_PHONE_NUMBER_ID and CALL_PROVIDER=vapi to {env_path}")
