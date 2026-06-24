# stores-projects

Bolt vs Stores reconciliation reports.

## Bolt vs Stores — Orders Reconciliation

**Live report:** https://mykhailobrynchak-dev.github.io/stores-projects/

Compares partner-reported order receipts (Hop Hey, Kopiyka) against Bolt's
Provider Price per order for the period **01.05–14.06.2026**.

- Matched by the unique 9-digit `order_id`.
- **Hop Hey:** Sum difference = Hop Hey price − `provider_price_before_discount` (Bolt price shown both before and after discount).
- **Kopiyka:** Sum difference = Bolt receipt (`provider_price_after_discount`) − Kopiyka receipt.
- Rows highlighted in red were delivered at the partner but cancelled/failed or missing in Bolt.
- Tables are filterable (date, |difference|, issues only), sortable, and paginated 50 rows at a time.
