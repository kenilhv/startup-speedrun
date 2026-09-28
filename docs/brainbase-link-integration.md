# Brainbase + Link: selected payment approach

## Decision and evidence

On September 28, 2026, the user confirmed that the Brainbase team told them their connected Link wallet is sufficient; no separate Stripe account is needed. This supersedes the original direct Stripe Connect/test-transfer requirement. Stripe infrastructure is still underneath Link, but PayCrew should integrate with the Brainbase workflow, not require its own Stripe secret key.

The agent-level Brainbase Payments (Beta) page was inspected, not just organization billing. It showed a connected Link wallet, live agent checkout, and no listed requests or purchases at the time of inspection. The page states that the agent requests approval in Link and then completes merchant checkout; approval alone does not confirm an order/payment. The user subsequently reported creating the workflow; its invocation/result contract has not yet been verified.

Sources: the signed-in Brainbase agent Payments page; [Stripe's explanation of Link wallets for agents](https://stripe.com/blog/giving-agents-the-ability-to-pay). The latter describes scoped payment credentials and checkout after approval, not arbitrary bank-account transfers.

## Current code — do not confuse configuration with completion

- `PAYMENT_PROVIDER=brainbase_link` is the default. It blocks the old outgoing Stripe API path and does not invoke Brainbase.
- No backend Brainbase checkout adapter exists yet. The backend fails closed and explains that integration is pending.
- Legacy Stripe sandbox code is retained, not the selected implementation. A separate Stripe account/key is not a blocker for Brainbase + Link.
- Offline tests fake all provider outcomes. Neither a passing test nor a connected wallet proves a completed purchase.
- The existing webhook `/webhooks/stripe` is legacy transfer handling, not a verified Brainbase/Link receipt endpoint. Do not route Link results into it by assumption.

## Contract needed from the configured workflow

Verify these through configuration/documentation or ask the Brainbase team; do not invent endpoints or spend money to discover behavior:

1. How our backend starts the specific workflow: API endpoint, workflow/agent identifier, authentication and supported idempotency behavior.
2. What input it needs: invoice reference, trusted vendor identity, permitted merchant checkout destination, exact currency and maximum total (including tax/fees).
3. How the backend receives or retrieves an authenticated final result: request/order/payment identifier, completed amount/currency/merchant and terminal status. A general task-success message is not sufficient evidence of payment.
4. How approval, denial, cancellation, expiry, failure, timeout and ambiguous completion are distinguished. If result delivery is retried, identify its event IDs and signature/verification procedure.
5. Whether a genuinely non-spending sandbox exists. Until explicitly verified and authorized, testing remains entirely offline.

The merchant must provide a compatible payment/checkout destination. Connecting the buyer's Link wallet does not create the vendor's checkout page. A bank-details-only invoice is not automatically payable through this checkout flow.

## Required safeguards for the future connector

- Keep payment execution disabled by default. Ask the user before any payment-capable agent run, spend/approval request or checkout; Link approval is an additional control, not permission for the development agent to invoke payments.
- Keep invoice risk checks and vendor verification before requesting payment. Do not trust invoice-supplied links without validating the merchant against trusted vendor data.
- Bind approval/result to the exact invoice, merchant, currency and allowed amount. Persist a unique operation reference before dispatch; never blindly retry an ambiguous payment.
- Record requested, awaiting approval, approved, checkout pending, paid, failed/cancelled, and unknown outcomes distinctly. These are proposed application states, not claimed Brainbase API fields.
- Mark paid only after authenticated final completion evidence. Review duplicates and timeouts without making a second purchase.
- Retain receipts and an audit trail without storing card credentials. Require owner authentication and secure callback handling before public deployment.

No agent execution, payment approval request, card charge, purchase, or payment-settings change was performed to produce this handoff.


## Implemented connector (September 28, 2026)

`backend/brainbase_payer.py` now implements the route. It stays off unless `PAYMENT_PROVIDER=brainbase_link` **and** `BRAINBASE_PAYMENTS_ENABLED=true`, plus `BRAINBASE_TOKEN`, `BRAINBASE_PAYER_AGENT_ID` and `BRAINBASE_PAY_MAX_CENTS` are set. Missing settings fail closed with no payments row.

- **Payer agent:** "PayCrew Payer" (`brainbase-agents/payer`, id `1b4ece5e-a9c4-46ea-8c27-e43f590bb818`). Its Link wallet must be connected on that agent's Payments page by the owner.
- **Dispatch:** a payments row (`provider='brainbase'`, frozen request, idempotency key) is written *before* `POST /v2/tasks` with `Idempotency-Key`. The task message carries the payment reference, vendor, invoice, exact amount and the vendor's trusted checkout URL (`vendors.payment_url`, seeded only from `VENDOR_PAYMENT_URLS`). Over-cap, non-USD, missing link, bank mismatch on auto-pay and wrong state are refused before any request.
- **Invoice status:** a new `paying` status (analyzing/awaiting_approval → paying → settled/escalated).
- **Result:** `PaymentWatcher` polls `GET /v2/tasks/{id}`. On `success` it reads the latest `assistant.message` and settles only if exactly one JSON report matches the reference, `status == "paid"`, the exact amount, `usd`, the checkout host, and a non-empty order id. `fail`/`need_more_info` or a reported failure → `failed`, escalated. Anything unreadable, mismatched or past `BRAINBASE_PAY_TIMEOUT_SECONDS` → `unknown`, escalated, never retried automatically. A transport error on dispatch → `unknown`; an explicit re-approval resends the identical request with the same key.

**Evidence caveat.** Settlement evidence is the payer agent's own final report, read back from a task this backend created with its own token. That is authenticated provenance, not a payment-rail receipt, and weaker than a signed Stripe event. The strict field binding is the substitute until Brainbase offers a result contract (question 3 above is still open).

**Still required before the first live run (owner decisions):** connect the Link wallet to the payer agent; give each demo vendor a real checkout page (a bank-details-only invoice cannot be paid by checkout); set a spend cap; create the PAT; then flip the kill switch. The first clean upload after that spends real money. `/demo/reset` refuses while any payment is pending or unknown.
