#!/usr/bin/env python3
"""Fetch OSM residential land-use polygons per city for a network.

Reads <network_dir>/stores.json, computes a bbox per city, and caches
<network_dir>/residential_<city>.json (list of [lat,lon] rings). Robust to
Overpass flakiness: retries with backoff, skips already-cached cities.

Usage: python3 fetch_residential_net.py <network_dir>
Adapted from the FORA fetch_residential.py.
"""
import json, math, time, os, ssl, sys, urllib.request, urllib.parse
from pathlib import Path

# Local corp-proxy MITM breaks cert verification (same reason as
# DATABRICKS_TLS_NO_VERIFY). Use an unverified context for Overpass.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
BUFFER_KM = 4.0


def load_city_bboxes(stores, buffer_km=BUFFER_KM):
    pts = {}
    for s in stores:
        pts.setdefault(s["city"], []).append((s["lat"], s["lon"]))
    boxes = {}
    for city, ps in pts.items():
        lats = [p[0] for p in ps]; lons = [p[1] for p in ps]
        dlat = buffer_km / 111.0
        dlon = buffer_km / (111.0 * math.cos(math.radians(sum(lats) / len(lats))))
        boxes[city] = (min(lats) - dlat, min(lons) - dlon,
                       max(lats) + dlat, max(lons) + dlon)
    return boxes


def query(bbox):
    s, w, n, e = bbox
    q = (f'[out:json][timeout:90];'
         f'(way["landuse"="residential"]({s},{w},{n},{e});'
         f'relation["landuse"="residential"]({s},{w},{n},{e}););'
         f'out geom;')
    data = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for attempt in range(6):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    ep, data=data,
                    headers={"User-Agent": "custom-polygons/1.0",
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=180, context=_SSL_CTX) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                if raw.lstrip().startswith("{"):
                    return json.loads(raw)
                last = raw[:140]
            except Exception as ex:
                last = str(ex)
            time.sleep(5)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"all endpoints failed: {last}")


def polygons_from(js):
    polys = []
    for el in js.get("elements", []):
        if el.get("type") == "way" and el.get("geometry"):
            ring = [[round(p["lat"], 5), round(p["lon"], 5)] for p in el["geometry"]]
            if len(ring) >= 4:
                polys.append(ring)
        elif el.get("type") == "relation":
            for m in el.get("members", []):
                if m.get("role") in ("outer", "") and m.get("geometry"):
                    ring = [[round(p["lat"], 5), round(p["lon"], 5)] for p in m["geometry"]]
                    if len(ring) >= 4:
                        polys.append(ring)
    return polys


def main():
    net_dir = Path(sys.argv[1]).resolve()
    stores = json.loads((net_dir / "stores.json").read_text(encoding="utf-8"))
    params = json.loads((net_dir / "params.json").read_text(encoding="utf-8")) \
        if (net_dir / "params.json").exists() else {}
    buffer_km = params.get("resid_buffer_km", BUFFER_KM)
    boxes = load_city_bboxes(stores, buffer_km)
    print(f"(residential bbox buffer = {buffer_km} km)")
    for city, bbox in boxes.items():
        fn = net_dir / f"residential_{city}.json"
        if fn.exists() and fn.stat().st_size > 2:
            print(f"{city}: cached"); continue
        print(f"{city}: querying...", flush=True)
        try:
            js = query(bbox)
            polys = polygons_from(js)
            fn.write_text(json.dumps(polys), encoding="utf-8")
            print(f"  -> {len(polys)} polygons", flush=True)
        except Exception as ex:
            print(f"  !! FAILED: {ex}", flush=True)


if __name__ == "__main__":
    main()
