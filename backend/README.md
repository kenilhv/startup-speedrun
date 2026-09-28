# PayCrew backend

Run from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
uvicorn backend.main:app --reload --port 8000
```

The API seeds the five vendors specified in `MASTER.md`. Set `CLEAN_VENDOR_PHONE` and `FAST_CONSULT_PHONE` in `backend/.env` before placing real calls. Upload PDFs with `POST /invoices/upload` using multipart field `files`; connect the board to `ws://localhost:8000/ws`. Set `MOCK_CALLS=true` for an immediate confirmed verification. The live caller posts `invoice_id`, `outcome`, `summary`, optional `provider_call_id`, and optional `transcript` to `/webhooks/call-result`.

Run offline checks from the repository root with `py -m backend.run_offline_tests`. This disables `.env` loading and outgoing network connections, uses temporary data, and mocks all providers. Passing tests are not evidence of live integration or production readiness.

## Payment status

Use **Brainbase + the connected Link wallet**, as confirmed by the user and Brainbase team. A separate Stripe account/API key is not required for this route. The backend connector remains unfinished; see [integration handoff](../docs/brainbase-link-integration.md).

Keep `PAYMENT_PROVIDER=brainbase_link` (also the default if unset). Outgoing legacy Stripe calls are disabled in this mode, and the app reports integration pending instead of pretending to pay. Do not run the legacy provisioning/funding CLI or change provider settings without explicit authorization. Only an intentional `stripe_test` selection plus a test key can enable that retained adapter; live Stripe keys are refused.

The user's connected Link wallet is live. Do not execute agent tasks, create Link spend/approval requests, or perform checkout as a test. No payment usage is authorized by these setup instructions.
