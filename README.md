# startup-speedrun — PayCrew

PayCrew routes invoices through extraction, deterministic risk checks, vendor verification, and payment approval. This branch contains the FastAPI backend; frontend and invoice/calling work are separate team contributions.

## Payment decision — updated September 28, 2026

The selected payment approach is **Brainbase workflow + the user's connected Link wallet**. The user confirmed this approach with the Brainbase team. **A separate Stripe account or Stripe API key is not a requirement for this approach.** Earlier instructions requiring a direct Stripe integration are superseded for the selected design.

Brainbase's agent-level Payments (Beta) page was inspected: the Link wallet was connected and labeled **Live payments / Agent checkout**. This is distinct from Brainbase's usage-credit billing. No payment requests, agent runs, purchases, or wallet changes were performed during inspection.

The backend-to-Brainbase payment connector is implemented in `backend/brainbase_payer.py` and ships **switched off** (`BRAINBASE_PAYMENTS_ENABLED=false`). With it off, payment attempts fail closed and nothing is dispatched. With it on, approved invoices go to the "PayCrew Payer" Brainbase agent, which checks out with the Link wallet; the backend marks an invoice paid only when the agent's final report matches the exact payment. See the handoff doc for the settings and the evidence caveat.

The previous test-transfer adapter remains only as inactive legacy code. It is not the team's current payment implementation. See [Brainbase + Link integration handoff](docs/brainbase-link-integration.md) for the remaining contract and safety requirements.

## Current status

Backend work in progress, not production-ready. REST routes, SQLite storage, WebSocket events, a serialized call queue, legacy Stripe test-transfer code, and Slack notification/interaction code are present. Live provider integrations have not been verified. Tests exercise local behavior with fake provider responses and text fixtures, not actual PDF extraction or real payments/calls.

Known integration gaps:

- `backend/calling/caller.py` is a mock adapter. Kenil's real caller must replace it.
- There is no implicit simulated-payment fallback. Missing/disabled provider configuration does not mark an invoice paid.
- Brainbase workflow invocation and authenticated final payment-result reconciliation remain to be integrated. A Link approval alone must never mark an invoice paid.
- The service has no owner authentication yet. Call webhook authentication currently also depends on configuring its secret, and the in-memory call queue has no restart recovery. Slack delivery/message updates still need integration verification. Do not expose this service publicly in this state.

## Local setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

Edit `backend/.env` before starting. Keep `PAYMENT_PROVIDER=brainbase_link`; leave the legacy Stripe fields empty. Leave Slack credentials empty for local work. `MOCK_CALLS=true` simulates call confirmation only, not payment. Replace `PAYCREW_WEBHOOK_SECRET` with a random secret before any callback work. Never use real vendor numbers for development.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

API documentation: <http://127.0.0.1:8000/docs>. Uploads use multipart field `files`; the WebSocket endpoint is `/ws`. Run a single server worker because the call queue is currently in memory.

## Tests

Run the offline suite from the repository root. It skips `.env` loading, uses temporary databases/uploads, mocks providers, and blocks network connections (apart from Python's internal loopback socket-pair creation on Windows):

```powershell
.\.venv\Scripts\python.exe -m backend.run_offline_tests
```

## Team integration

- Shresth: supply the frontend and `extract_invoice(pdf_bytes)` implementation.
- Kenil: supply `start_verification_call(...)` and post results to `/webhooks/call-result` using `X-PayCrew-Secret`.
- Zubair: backend integration with the Brainbase + Link workflow, safe result reconciliation, Slack configuration, and deployment. No separate Stripe account setup is required for the selected route.

Keep credentials in an untracked `.env` file. Databases, uploaded invoices, virtual environments, and Python caches are excluded from Git.
