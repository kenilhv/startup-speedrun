# PayCrew backend

Run from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
uvicorn backend.main:app --reload --port 8000
```

The API seeds the five vendors specified in `MASTER.md`. Set `CLEAN_VENDOR_PHONE` and `FAST_CONSULT_PHONE` in `backend/.env` before placing real calls. Upload PDFs with `POST /invoices/upload` using multipart field `files`; connect the board to `ws://localhost:8000/ws`. Set `MOCK_CALLS=true` for an immediate confirmed verification. The live caller posts `invoice_id`, `outcome`, `summary`, optional `provider_call_id`, and optional `transcript` to `/webhooks/call-result`.

Run the verified local demo checks with `py -m unittest backend.test_api -v`.

After setting a Stripe **test** secret key, provision connected accounts and test balance once with `py -m backend.seed --stripe`.
