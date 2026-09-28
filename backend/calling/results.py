import os

import httpx


def post_result_sync(payload: dict) -> None:
    """POST a contract call result to the backend's /webhooks/call-result (blocking; use from threads)."""
    url = os.environ.get("CALL_RESULT_URL", "http://127.0.0.1:8000/webhooks/call-result")
    try:
        httpx.post(url, json=payload, timeout=15,
                   headers={"X-PayCrew-Secret": os.environ.get("PAYCREW_WEBHOOK_SECRET", "")}).raise_for_status()
    except Exception as e:
        print(f"[calling] posting call result failed: {e}", flush=True)
