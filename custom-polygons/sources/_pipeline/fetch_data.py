#!/usr/bin/env python3
"""Pull store lists + per-city courier economics from Databricks for each network.

Writes, per network, into ../<network>/:
  * stores.json  — [{name, city, address, lat, lon, provider_id, status}]
  * econ.json    — {cities:{city:{dropoff,cpo,orders}}, country_cpo, country_dropoff}

Auth: reads standard env vars (export them from your Taistra .env before running):
  DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID,
  DATABRICKS_TLS_NO_VERIFY (optional, "1" for local corp-proxy).

Economics are city-level (network-agnostic): avg courier drop-off distance and
avg courier earning (CPO proxy) over delivered orders in the last 12 months.
"""
import json, os, sys
from pathlib import Path
from databricks import sql as dbsql

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent  # custom-polygons/sources

NETWORKS = {
    "anri-pharm": "ANRI-PHARM",
    "taistra": "TAISTRA",
}

STORE_Q = """
SELECT provider_id, provider_name, city_name, provider_address,
       CAST(provider_lat AS DOUBLE) AS lat, CAST(provider_lng AS DOUBLE) AS lng,
       provider_status
FROM hive_metastore.ng_delivery_spark.dim_provider_v2
WHERE group_name = '{group}' AND country_code = 'ua'
  AND provider_status <> 'deleted'
  AND provider_lat IS NOT NULL AND provider_lng IS NOT NULL
ORDER BY city_name, provider_name
"""

ECON_Q = """
SELECT city_name,
       COUNT(*) AS orders,
       ROUND(AVG(courier_distance_from_provider_to_eater_meters)/1000.0, 3) AS dropoff_km,
       ROUND(AVG(courier_earning_eur), 3) AS cpo_eur
FROM hive_metastore.ng_delivery_spark.fact_order_delivery
WHERE order_state = 'delivered' AND city_country_code = 'ua'
  AND order_created_date >= date_sub(current_date(), 365)
  AND city_name IN ({cities})
  AND courier_distance_from_provider_to_eater_meters > 0
  AND courier_distance_from_provider_to_eater_meters < 20000
GROUP BY city_name
"""


def connect():
    host = os.environ["DATABRICKS_HOST"]
    token = os.environ["DATABRICKS_TOKEN"]
    wid = os.environ["DATABRICKS_WAREHOUSE_ID"]
    extra = {}
    if os.environ.get("DATABRICKS_TLS_NO_VERIFY", "").strip().lower() in ("1", "true", "yes"):
        extra["_tls_no_verify"] = True
    return dbsql.connect(server_hostname=host,
                         http_path=f"/sql/1.0/warehouses/{wid}",
                         access_token=token, **extra)


def rows(cur, q):
    cur.execute(q)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main():
    conn = connect()
    cur = conn.cursor()
    for slug, group in NETWORKS.items():
        store_rows = rows(cur, STORE_Q.format(group=group))
        stores = [{
            "name": r["provider_name"], "city": r["city_name"],
            "address": r["provider_address"], "lat": float(r["lat"]),
            "lon": float(r["lng"]), "provider_id": int(r["provider_id"]),
            "status": r["provider_status"],
        } for r in store_rows]

        cities = sorted({s["city"] for s in stores})
        city_list = ", ".join("'" + c.replace("'", "''") + "'" for c in cities)
        econ_rows = rows(cur, ECON_Q.format(cities=city_list))
        cities_econ = {}
        tot_orders = 0
        w_drop = 0.0
        w_cpo = 0.0
        for r in econ_rows:
            o = int(r["orders"])
            cities_econ[r["city_name"]] = {
                "dropoff": float(r["dropoff_km"]),
                "cpo": float(r["cpo_eur"]),
                "orders": o,
            }
            tot_orders += o
            w_drop += float(r["dropoff_km"]) * o
            w_cpo += float(r["cpo_eur"]) * o
        econ = {
            "cities": cities_econ,
            "country_dropoff": round(w_drop / tot_orders, 3) if tot_orders else 2.6,
            "country_cpo": round(w_cpo / tot_orders, 3) if tot_orders else 2.0,
        }

        net_dir = OUT / slug
        net_dir.mkdir(parents=True, exist_ok=True)
        (net_dir / "stores.json").write_text(
            json.dumps(stores, ensure_ascii=False, indent=1), encoding="utf-8")
        (net_dir / "econ.json").write_text(
            json.dumps(econ, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{group:12s} -> {len(stores):3d} stores, {len(cities_econ)} cities "
              f"{sorted(cities_econ)}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
