#!/usr/bin/env python3
"""Fetch actual delivery drop-off density for a network's stores from Databricks.

Aggregates delivered-order drop-off points (~111 m cells) over the last ~6 months
and writes <network_dir>/orders_<city>.json = [[lat, lon, count], ...]. Used as a
real demand surface (where orders actually happen) instead of uniform residential
area, so radius optimization does not chase sparsely-ordered far suburbs.

Auth: standard Databricks env vars (export from the Taistra .env).
Usage: python3 fetch_orders.py <network_dir>
"""
import json, os, sys
from pathlib import Path
from databricks import sql as dbsql

Q = """
SELECT city_name,
       ROUND(delivery_lat, 3) AS lat,
       ROUND(delivery_lng, 3) AS lng,
       COUNT(*) AS c
FROM hive_metastore.ng_delivery_spark.fact_order_delivery
WHERE order_state = 'delivered'
  AND order_created_date >= date_sub(current_date(), 182)
  AND provider_id IN ({ids})
  AND delivery_lat IS NOT NULL AND delivery_lng IS NOT NULL
GROUP BY city_name, ROUND(delivery_lat, 3), ROUND(delivery_lng, 3)
"""


def connect():
    extra = {}
    if os.environ.get("DATABRICKS_TLS_NO_VERIFY", "").strip().lower() in ("1", "true", "yes"):
        extra["_tls_no_verify"] = True
    return dbsql.connect(server_hostname=os.environ["DATABRICKS_HOST"],
                         http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
                         access_token=os.environ["DATABRICKS_TOKEN"], **extra)


def main():
    net_dir = Path(sys.argv[1]).resolve()
    stores = json.loads((net_dir / "stores.json").read_text(encoding="utf-8"))
    ids = ", ".join(str(s["provider_id"]) for s in stores)
    conn = connect(); cur = conn.cursor()
    cur.execute(Q.format(ids=ids))
    cols = [c[0] for c in cur.description]
    by_city = {}
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        by_city.setdefault(r["city_name"], []).append(
            [float(r["lat"]), float(r["lng"]), int(r["c"])])
    cur.close(); conn.close()
    for city, pts in by_city.items():
        (net_dir / f"orders_{city}.json").write_text(
            json.dumps(pts, ensure_ascii=False), encoding="utf-8")
        tot = sum(p[2] for p in pts)
        print(f"{city:16s} {len(pts):4d} cells, {tot} orders")


if __name__ == "__main__":
    main()
