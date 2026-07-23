#!/usr/bin/env python3
"""Fetch OSM residential land-use polygons for each city's bounding box.

Saves one cache file per city (residential_<city>.json) containing a list of
polygons (each a list of [lat, lon]). Used as the real demand surface so radii
cover residential areas and stop at empty land. Robust to Overpass flakiness:
retries with backoff and skips cities already cached.
"""
import csv, json, math, time, os, urllib.request, urllib.parse

SRC = "FORA, OKKO Market Account Creation - Account Creation [TEMPLATE].csv"
COL_CITY, COL_LAT, COL_LON = 28, 32, 33
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
BUFFER_KM = 4.0

def load_city_bboxes():
    with open(SRC, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    pts = {}
    for r in rows:
        if len(r) <= COL_LON:
            continue
        try:
            lat = float(r[COL_LAT]); lon = float(r[COL_LON])
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        pts.setdefault(r[COL_CITY].strip(), []).append((lat, lon))
    boxes = {}
    for city, ps in pts.items():
        lats = [p[0] for p in ps]; lons = [p[1] for p in ps]
        dlat = BUFFER_KM / 111.0
        dlon = BUFFER_KM / (111.0 * math.cos(math.radians(sum(lats) / len(lats))))
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
                    headers={"User-Agent": "fora-radius/1.0",
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as resp:
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
    boxes = load_city_bboxes()
    for city, bbox in boxes.items():
        fn = f"residential_{city}.json"
        if os.path.exists(fn) and os.path.getsize(fn) > 2:
            print(f"{city}: cached"); continue
        print(f"{city}: querying...", flush=True)
        try:
            js = query(bbox)
            polys = polygons_from(js)
            json.dump(polys, open(fn, "w"))
            print(f"  -> {len(polys)} polygons", flush=True)
        except Exception as ex:
            print(f"  !! FAILED: {ex}", flush=True)

if __name__ == "__main__":
    main()
