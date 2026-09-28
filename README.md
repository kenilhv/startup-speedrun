# startup-speedrun — PayCrew

PayCrew routes invoices through extraction, deterministic risk checks, vendor verification, and payment approval. This repository currently contains the FastAPI backend; the frontend and live invoice/calling agents are separate team contributions.

## Current status

Backend work in progress. REST routes, SQLite storage, WebSocket events, a serialized call queue, Stripe transfer code, and Slack notification/interaction code are present. Provider integrations have not been verified with real credentials. The existing tests exercise local behavior with mocks and text fixtures, not actual PDF extraction or live payments/calls.

Known integration gaps:

- `backend/agents/analysis_agent.py` is a placeholder text parser, not a working PDF/Claude extractor. Shresth's extractor must replace it.
- `backend/calling/caller.py` is a mock adapter. Kenil's real caller must replace it.
- Without a Stripe key the current payment implementation records a mock payment. A `settled` status in that configuration is simulated.
- Stripe account provisioning and Slack delivery/message updates need integration verification. The service has no owner authentication yet and is not ready for public production deployment.

## Local setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

Edit `backend/.env` before starting. For local mock testing, leave `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, and `SLACK_CHANNEL_ID` empty; the example's placeholder values are not usable credentials. Set `MOCK_CALLS=true` and replace `PAYCREW_WEBHOOK_SECRET` with a random secret. Only teammate phone numbers should be used for eventual real calls.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

API documentation: <http://127.0.0.1:8000/docs>. Uploads use multipart field `files`; the WebSocket endpoint is `/ws`. Run a single server worker because the call queue is currently in memory.

## Tests

Use an environment without live provider credentials:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.test_api -v
```

## Team integration

- Shresth: supply the frontend and `extract_invoice(pdf_bytes)` implementation.
- Kenil: supply `start_verification_call(...)` and post results to `/webhooks/call-result` using `X-PayCrew-Secret`.
- Zubair: backend integration, Stripe test accounts, Slack configuration, and deployment.

Keep credentials in an untracked `.env` file. Databases, uploaded invoices, virtual environments, and Python caches are excluded from Git.
