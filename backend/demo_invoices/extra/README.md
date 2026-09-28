# Extra demo invoices

Same five companies, some normal and some odd. Upload any mix.

| File | Invoice | Total | Expected |
|---|---|---|---|
| 01-good-officesupply.pdf | INV-443 | $265.00 | Low risk → auto-pay |
| 02-good-paperworks.pdf | INV-401 | $120.00 | Low risk → auto-pay |
| 03-good-techsoftware.pdf | INV-9930 | $2,400.00 | Low risk → auto-pay |
| 04-good-cleanvendor.pdf | INV-911 | $320.00 | Low risk → auto-pay (no PO is only a note) |
| 05-good-fastconsult.pdf | INV-F43 | $1,800.00 | Low risk → auto-pay |
| 06-weird-duplicate-officesupply.pdf | INV-441 | $800.00 | Same INV-441 sent again → Duplicate → verify by phone |
| 07-weird-inflated-officesupply.pdf | INV-446 | $7,490.00 | Amount doesn't match PO-1001 → verify by phone |
| 08-weird-newbank-officesupply.pdf | INV-447 | $800.00 | Bank changed + urgency → critical → verify by phone |
| 09-weird-wrong-po-paperworks.pdf | INV-403 | $350.00 | PO-1003 belongs to TechSoftware → verify by phone |
| 10-weird-giftcards-cleanvendor.pdf | INV-913 | $4,750.00 | Claude flags gift-card request → verify by phone |
| 11-weird-lookalike-vendor.pdf | INV-9001 | $800.00 | Not in vendor directory → human review |
