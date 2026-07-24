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
- **Kopiyka** — two periods as sub-tabs: **01.05–14.06** and **01.06–12.07.2026**.
  Matched by `order_id`; Sum difference = Bolt receipt (after discount) − Kopiyka receipt.
- **TAISTRA** (01.05–09.07.2026) — the partner registry has no shared order IDs, only
  location + timestamp + amount, so receipts are matched to Bolt orders by
  **location + timestamp (±120 min) + amount**. Each row shows the match type
  (Exact sum / Time-matched / Not matched). Includes a per-location summary of matched totals.
  The registry includes only bank-transfer receipts and excludes the ОʼНДЕ store.

All tables are filterable (date, |difference|, issues only), sortable, and paginated 50 rows at a time.
Rows highlighted in red were not matched / cancelled / failed in Bolt while present at the partner.

## Custom Delivery Polygons — Radius Analysis

**Live:** https://mykhailobrynchak-dev.github.io/stores-projects/custom-polygons/

Coverage analysis and delivery-radius optimization for partner networks
(**FORA**, **ANRI-PHARM**, **TAISTRA**). Each network has two interactive maps:

- **Radius Map (Delivery Radius Optimizer)** — compare three radius strategies
  (city-uniform / per-store / country) with live coverage, cannibalization and CPO.
- **Manual Radius Editor** — drag each store's radius and export the result to CSV.

Key points of the methodology:

- **Coverage is measured over the official Bolt delivery zones** (KML polygons), not
  residential area — so numbers reflect the real serviceable territory.
- **Economics from Databricks:** store coordinates from `dim_provider_v2`, per-city
  drop-off distance and CPO (courier earning + bonus + waiting) from `fact_order_delivery`.
- **Recommended strategy** is highlighted on each map. For the sparse TAISTRA network the
  recommendation is a **resilient** radius: sized so ≥85% of demand is reachable by 2+
  stores, giving a backup if a store goes offline — at ~the same CPO.

Folder [`custom-polygons/`](custom-polygons/):

- `*-radius-map.html`, `*-manual-editor.html` — the shareable maps per network.
- `final-radii/` — the **final applied radii per store** (with Provider ID) for FORA,
  ANRI-PHARM and TAISTRA.
- `sources/` — the reproducible pipeline (`_pipeline/`: fetch data & Bolt zones, build,
  render) plus per-network inputs/outputs (`stores.json`, `econ.json`, Bolt zones, etc.).
