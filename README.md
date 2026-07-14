# stores-projects

Bolt vs Stores reconciliation reports.

## Bolt vs Stores — Orders Reconciliation

**Live report:** https://mykhailobrynchak-dev.github.io/stores-projects/

Compares partner-reported order receipts against Bolt's Provider Price
(`provider_price_after_discount`) per order.

Tabs:

- **Hop Hey** (01.05–14.06.2026) — matched by the unique 9-digit `order_id`.
  Sum difference = Hop Hey price − `provider_price_before_discount` (Bolt price shown before and after discount).
- **Kopiyka** — two periods as sub-tabs: **01.05–14.06** and **01.06–12.07.2026**.
  Matched by `order_id`; Sum difference = Bolt receipt (after discount) − Kopiyka receipt.
- **TAISTRA** (01.05–09.07.2026) — the partner registry has no shared order IDs, only
  location + timestamp + amount, so receipts are matched to Bolt orders by
  **location + timestamp (±120 min) + amount**. Each row shows the match type
  (Exact sum / Time-matched / Not matched). Includes a per-location summary of matched totals.
  The registry includes only bank-transfer receipts and excludes the ОʼНДЕ store.

All tables are filterable (date, |difference|, issues only), sortable, and paginated 50 rows at a time.
Rows highlighted in red were not matched / cancelled / failed in Bolt while present at the partner.
