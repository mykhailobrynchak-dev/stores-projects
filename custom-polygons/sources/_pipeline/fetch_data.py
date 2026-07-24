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

# Per-network store selection. Two modes:
#   * "ids"    — explicit provider_id list (source of truth).
#   * "group"  — group_name + provider_status filter (admin-style selection).
NETWORKS = {
    # TAISTRA: active + onboarding stores of the group (exclude hidden/archived/deleted),
    # plus a few explicit Lutsk stores whose group_name is blank in the source table.
    "taistra": {"group": "TAISTRA", "statuses": ["active", "onboarding"],
                "extra_ids": [592669, 412660, 742667, 742668, 532658,
                              892761, 892762, 262812],
                "econ_full": True},
    # ANRI-PHARM: explicit provider_id list.
    "anri-pharm": {"ids": [
        203437, 862737, 203450, 203421, 203406, 203448, 203449, 203392,
        203398, 472722, 622759, 742739, 203397, 203453, 203447, 203396,
        203423, 203403, 203395, 203439, 203411, 203446, 203408, 562711,
        772725, 203393, 203433, 802731, 203418, 742738, 203429, 203409,
        203428, 203407, 203432, 203441, 203434, 203394, 203440, 203425,
        203438, 203400, 203426, 203419, 203416, 203415, 892722, 203420,
        203413, 203424, 203431, 203457, 203454, 203435, 203463, 203444,
        203402, 203410, 442714, 562705, 682721, 862732, 203459, 862522,
        472709]},
}

STORE_COLS = """
SELECT provider_id, provider_name, group_name, city_name, provider_address,
       CAST(provider_lat AS DOUBLE) AS lat, CAST(provider_lng AS DOUBLE) AS lng,
       provider_status
FROM hive_metastore.ng_delivery_spark.dim_provider_v2
"""


def store_query(cfg):
    base = STORE_COLS + "WHERE provider_lat IS NOT NULL AND provider_lng IS NOT NULL\n"
    if "ids" in cfg:
        base += "  AND provider_id IN (" + ", ".join(str(i) for i in cfg["ids"]) + ")\n"
    else:
        st = ", ".join("'" + s + "'" for s in cfg["statuses"])
        grp = (f"(group_name = '{cfg['group']}' AND country_code = 'ua' "
               f"AND provider_status IN ({st}))")
        if cfg.get("extra_ids"):
            ids = ", ".join(str(i) for i in cfg["extra_ids"])
            base += f"  AND ( {grp} OR provider_id IN ({ids}) )\n"
        else:
            base += f"  AND {grp}\n"
    return base + "ORDER BY city_name, provider_name"

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

# Network-specific "full" CPO = courier earning + bonus + waiting compensation
# (matches the ~€2.24 delivery CPO in the weekly report), over the network's own
# providers and last ~6 months. Used when a network sets "econ_full": True.
ECON_FULL_Q = """
SELECT city_name,
       COUNT(*) AS orders,
       ROUND(AVG(courier_distance_from_provider_to_eater_meters)/1000.0, 3) AS dropoff_km,
       ROUND(AVG(COALESCE(courier_earning_eur,0)+COALESCE(courier_bonus_eur,0)
                 +COALESCE(courier_waiting_at_provider_compensation_eur,0)), 3) AS cpo_eur
FROM hive_metastore.ng_delivery_spark.fact_order_delivery
WHERE order_state = 'delivered'
  AND order_created_date >= date_sub(current_date(), 182)
  AND provider_id IN ({ids})
  AND courier_distance_from_provider_to_eater_meters > 0
  AND courier_distance_from_provider_to_eater_meters < 20000
GROUP BY city_name
"""
MIN_ORDERS_ROBUST = 50  # cities below this fall back to network avg econ


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
    for slug, cfg in NETWORKS.items():
        store_rows = rows(cur, store_query(cfg))
        stores = [{
            "name": r["provider_name"], "city": r["city_name"],
            "address": r["provider_address"], "lat": float(r["lat"]),
            "lon": float(r["lng"]), "provider_id": int(r["provider_id"]),
            "status": r["provider_status"], "group_name": r["group_name"],
        } for r in store_rows]
        if "ids" in cfg:
            found = {int(r["provider_id"]) for r in store_rows}
            missing = [i for i in cfg["ids"] if i not in found]
            if missing:
                print(f"  [WARN] {slug}: {len(missing)} ids without coords/row: {missing}")

        cities = sorted({s["city"] for s in stores})
        if cfg.get("econ_full"):
            id_list = ", ".join(str(s["provider_id"]) for s in stores)
            econ_rows = rows(cur, ECON_FULL_Q.format(ids=id_list))
        else:
            city_list = ", ".join("'" + c.replace("'", "''") + "'" for c in cities)
            econ_rows = rows(cur, ECON_Q.format(cities=city_list))

        raw = {r["city_name"]: {"dropoff": float(r["dropoff_km"]), "cpo": float(r["cpo_eur"]),
                                "orders": int(r["orders"])} for r in econ_rows}
        # network average from robust cities (enough orders) for fallback
        robust = {c: v for c, v in raw.items() if v["orders"] >= MIN_ORDERS_ROBUST}
        base = robust or raw
        tot = sum(v["orders"] for v in base.values()) or 1
        avg_drop = round(sum(v["dropoff"] * v["orders"] for v in base.values()) / tot, 3)
        avg_cpo = round(sum(v["cpo"] * v["orders"] for v in base.values()) / tot, 3)

        cities_econ = {}
        for c in cities:
            v = raw.get(c)
            if v and v["orders"] >= MIN_ORDERS_ROBUST:
                cities_econ[c] = v
            else:  # sparse / missing city -> use network average (avoids noisy anchors)
                cities_econ[c] = {"dropoff": avg_drop, "cpo": avg_cpo,
                                  "orders": v["orders"] if v else 0}
        econ = {"cities": cities_econ, "country_dropoff": avg_drop, "country_cpo": avg_cpo}

        net_dir = OUT / slug
        net_dir.mkdir(parents=True, exist_ok=True)
        (net_dir / "stores.json").write_text(
            json.dumps(stores, ensure_ascii=False, indent=1), encoding="utf-8")
        (net_dir / "econ.json").write_text(
            json.dumps(econ, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{slug:12s} -> {len(stores):3d} stores, {len(cities_econ)} cities "
              f"{sorted(cities_econ)}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
