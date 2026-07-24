#!/usr/bin/env python3
"""Extract Bolt delivery-zone polygons (from the KML/KMZ exports) for a network's
cities and save <network_dir>/boltzone_<city>.json = [[ [lat,lon], ... ], ...].

These official delivery zones become the territory basis for coverage: coverage
is measured over the Bolt zone, not over OSM residential area or a fixed buffer.

Usage: python3 extract_bolt_zones.py <network_dir>
"""
import json, math, sys, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

RING_NEAR_KM = 5.0  # keep only zone rings within this distance of the city's stores
                    # (splits shared combined files like Bucha+Irpin+Brovary+Vyshhorod)

KML_DIR = Path("/Users/mishabrynchak/My Drive/Cursor folder/Tasks/Stores location check/Bolt polygons")
NS = {"k": "http://www.opengis.net/kml/2.2"}

# city (dim_provider_v2.city_name) -> one or more KML/KMZ files (union of their polygons)
BOLT_FILES = {
    "Kyiv": ["Kyiv_map.kml", "Bucha + Irpin+Brovary+Vyshhorod.kml"],
    "Lviv": ["Lviv launch map.kml"],
    "Chernivtsi": ["Chernivtsi GLOVO.kml"],
    "Ivano-Frankivsk": ["Ivano-Frankivsk GLOVO.kml"],
    "Khmelnytskyi": ["Khmelnytskiy GLOVO.kml"],
    "Ternopil": ["Ternopil New.kml"],
    "Lutsk": ["Lutsk GLOVO.kml"],
    "Vinnytsia": ["Vinnytsa launch map for Admin.kml"],
    "Zhytomyr": ["Zhytomyr GLOVO.kml"],
    "Rivne": ["Rivne GLOVO.kml"],
    "Bila Tserkva": ["Bila Tserkva GLOVO.kml"],
    "Boryspil": ["Boryspil GLOVO.kml"],
    "Chernihiv": ["Chernihiv GLOVO.kmz"],
    "Irpin": ["Bucha + Irpin+Brovary+Vyshhorod.kml"],
    "Brovary": ["Bucha + Irpin+Brovary+Vyshhorod.kml"],
    "Vyshhorod": ["Bucha + Irpin+Brovary+Vyshhorod.kml"],
}


def read_kml(path: Path) -> str:
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            return z.read(name).decode("utf-8", "replace")
    return path.read_text(encoding="utf-8", errors="replace")


def rings_from_kml(text: str):
    root = ET.fromstring(text)
    rings = []
    for pg in root.findall(".//k:Polygon", NS):
        c = pg.find(".//k:outerBoundaryIs/k:LinearRing/k:coordinates", NS)
        if c is None or not c.text:
            continue
        ring = []
        for tok in c.text.split():
            xy = tok.split(",")
            if len(xy) >= 2:
                ring.append([round(float(xy[1]), 6), round(float(xy[0]), 6)])  # [lat,lon]
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def _ring_near_stores(ring, pts, near_km):
    """True if the ring is within near_km of any store (min vertex distance) or
    contains a store. Coordinates are [lat,lon]; distance via equirectangular km."""
    if not pts:
        return True
    lat0 = sum(p[0] for p in pts) / len(pts)
    kx = 111.320 * math.cos(math.radians(lat0)); ky = 110.574
    rx = [lon * kx for lat, lon in ring]; ry = [lat * ky for lat, lon in ring]
    for slat, slon in pts:
        sx, sy = slon * kx, slat * ky
        if min((rx[i] - sx) ** 2 + (ry[i] - sy) ** 2 for i in range(len(ring))) <= near_km ** 2:
            return True
    return False


def main():
    net_dir = Path(sys.argv[1]).resolve()
    stores = json.loads((net_dir / "stores.json").read_text(encoding="utf-8"))
    cities = sorted({s["city"] for s in stores})
    pts_by_city = {}
    for s in stores:
        pts_by_city.setdefault(s["city"], []).append((s["lat"], s["lon"]))
    for city in cities:
        files = BOLT_FILES.get(city)
        if not files:
            print(f"  [WARN] no Bolt zone mapping for '{city}'")
            continue
        rings = []
        for fn in files:
            p = KML_DIR / fn
            if not p.exists():
                print(f"  [WARN] missing {fn}")
                continue
            rings += rings_from_kml(read_kml(p))
        pts = pts_by_city.get(city, [])
        rings = [r for r in rings if _ring_near_stores(r, pts, RING_NEAR_KM)]
        if rings:
            (net_dir / f"boltzone_{city}.json").write_text(
                json.dumps(rings, ensure_ascii=False), encoding="utf-8")
            npts = sum(len(r) for r in rings)
            print(f"{city:16s} {len(rings)} rings, {npts} pts  <- {', '.join(files)}")


if __name__ == "__main__":
    main()
