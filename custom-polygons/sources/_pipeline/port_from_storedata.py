#!/usr/bin/env python3
"""Generate stores.json + econ.json for a network from an existing store_data.js
(window.STORE_DATA = {...};). Lets a legacy network (e.g. FORA, built by the
colleague's script) be re-run through build_network on the Bolt-zone basis.

Usage: python3 port_from_storedata.py <network_dir>
"""
import json, sys
from pathlib import Path


def main():
    net_dir = Path(sys.argv[1]).resolve()
    txt = (net_dir / "store_data.js").read_text(encoding="utf-8")
    obj = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    cities = obj["cities"]
    stores = []
    econ = {}
    for city, cd in cities.items():
        for s in cd["stores"]:
            stores.append({"name": s["name"], "city": city, "address": s.get("address", ""),
                           "lat": s["lat"], "lon": s["lon"]})
        e = cd.get("econ", {})
        econ[city] = {"dropoff": e.get("dropoff", obj["model"].get("country_dropoff", 2.6)),
                      "cpo": e.get("cpo", obj["model"].get("country_cpo", 2.0)),
                      "orders": e.get("orders", 0)}
    out = {"cities": econ,
           "country_cpo": obj["model"].get("country_cpo", 2.0),
           "country_dropoff": obj["model"].get("country_dropoff", 2.6)}
    (net_dir / "stores.json").write_text(json.dumps(stores, ensure_ascii=False, indent=1), encoding="utf-8")
    (net_dir / "econ.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{net_dir.name}: {len(stores)} stores, {len(econ)} cities -> stores.json + econ.json")


if __name__ == "__main__":
    main()
