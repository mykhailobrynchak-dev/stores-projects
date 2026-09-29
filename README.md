# stores-projects

Bolt vs Stores reconciliation reports.

## Bolt vs Stores — Orders Reconciliation

**Live report:** https://mykhailobrynchak-dev.github.io/stores-projects/

Compares partner-reported order receipts against Bolt's Provider Price
(`provider_price_after_discount`) per order.

Tabs:

- **Hop Hey** — two periods as sub-tabs: **01.05–14.06** and **01.07–12.07.2026**.
  Matched by the unique 9-digit `order_id`; Sum difference = Hop Hey price − `provider_price_before_discount`
  (Bolt price shown before and after discount).
- **Kopiyka** — three periods as sub-tabs: **01.05–14.06**, **01.06–12.07**, and **01.07–31.08.2026**.
  Matched by `order_id`; Sum difference = Bolt receipt (after discount) − Kopiyka receipt.
- **TAISTRA**
  - **01.05–09.07.2026** — no shared order IDs; receipts matched by location + timestamp (±120 min) + amount.
  - **July 2026** and **August 2026** — partner files contain **location totals only**.
    Compared with Bolt `provider_price_after_discount` for **all delivered** orders at the matching store
    (cash + cashless; ОʼНДЕ excluded). Partner file is labelled cashless-only, but those totals align with all delivered, not cashless-only.
- **SPAR** — **01.07–31.08.2026**, EUROSPAR. Partner file has **no order IDs** (19 1C documents).
  Matched at **calendar-day** grain: Bolt Σ = `provider_price_after_discount` of delivered orders.
  Sum difference = Bolt day Σ − partner day Σ. 1:1 amount match is not possible (documents merge/split vs orders);
  17.08 is the control day (2 partner documents = 3 delivered orders, totals match exactly).

All tables are filterable (date, |difference|, issues only), sortable, and paginated 50 rows at a time.
Rows highlighted in red were not matched / cancelled / failed in Bolt while present at the partner.
