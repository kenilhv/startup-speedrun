# Extract one invoice

1. Read the whole document. Find the vendor header, the "Invoice #" line, the "Total due" line,
   the due date, any PO reference, and the payment instructions ("Pay to account ending ....").
2. Convert the total to integer cents and the date to YYYY-MM-DD.
3. Keep only the last 4 digits of the bank account.
4. Return the JSON object from your instructions. Nothing before or after it.

Reference demo set (for self-checks):

| Invoice | Vendor | Total | Bank last 4 |
|---|---|---|---|
| INV-441 | OfficeSupplyCo | $800.00 | 0011 |
| INV-400 | PaperWorks Ltd | $350.00 | 0022 |
| INV-9921 | TechSoftware Inc | $1,200.00 | 0033 |
| INV-910 | CleanVendor Inc | $480.00 | 9921 |
| INV-F42 | FastConsult LLC | $4,500.00 | 7788 |
