#!/usr/bin/env python3
"""
FORA - delivery radius optimization.

Reads the Account Creation template, extracts every store's coordinates, then
for each city computes:
  * Option A  - one unified radius per city (and one unified radius for the
                whole country), chosen to balance coverage / cannibalization / CPO.
  * Option B  - a per-store radius driven by local store spacing (fills gaps to
                neighbours, shrinks where stores are dense, capped so isolated
                stores don't blanket empty land).

Outputs `store_data.js` (consumed by index.html) and `recommended_radii.csv`.

Economic grounding (Bolt courier-ops data already in the workspace):
  * Per-city CPO (Cost Per Order, EUR) and avg courier drop-off distance.
  * A linear CPO-vs-distance model derived from those observations estimates
    how CPO moves as the delivery radius changes.

Demand surface:
  * OSM residential land-use (residential_<city>.json) when available, so
    coverage/cannibalization are measured over real residential areas.
  * Falls back to a store-network kernel density when no OSM data is cached.
"""
import csv, json, math, os
import numpy as np

SRC = "FORA, OKKO Market Account Creation - Account Creation [TEMPLATE].csv"

# ---------------------------------------------------------------------------
# 1. Per-city courier economics (dashboard-delivery courier ops / city_summary.csv)
# ---------------------------------------------------------------------------
CITY_ECON = {
    "Kyiv":         {"dropoff": 2.527, "cpo": 2.40, "orders": 465033},
    "Vinnytsia":    {"dropoff": 2.369, "cpo": 2.09, "orders": 65343},
    "Irpin":        {"dropoff": 2.915, "cpo": 2.76, "orders": 11188},
    "Zhytomyr":     {"dropoff": 2.817, "cpo": 2.12, "orders": 10423},
    "Rivne":        {"dropoff": 3.280, "cpo": 2.29, "orders": 10228},
    "Brovary":      {"dropoff": 2.135, "cpo": 2.19, "orders": 7055},
    "Bila Tserkva": {"dropoff": 3.398, "cpo": 2.35, "orders": 5868},
    "Boryspil":     {"dropoff": 2.119, "cpo": 1.95, "orders": 5271},
    "Chernihiv":    {"dropoff": 2.889, "cpo": 2.45, "orders": 4767},
    "Vyshhorod":    {"dropoff": 3.164, "cpo": 4.32, "orders": 112},
}
COUNTRY_CPO = 2.33
COUNTRY_DROPOFF = 2.66

def fit_cpo_model():
    pts = [(v["dropoff"], v["cpo"]) for v in CITY_ECON.values() if v["orders"] >= 4000]
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    b = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, y in pts)
    a = my - b * mx
    return a, b

CPO_A, CPO_B = fit_cpo_model()
DROPOFF_FACTOR = 0.60  # mean drop-off dist ~= factor * radius

def cpo_for_radius(city, radius_km):
    econ = CITY_ECON.get(city)
    base_cpo = econ["cpo"] if econ else COUNTRY_CPO
    base_drop = econ["dropoff"] if econ else COUNTRY_DROPOFF
    return base_cpo + CPO_B * (DROPOFF_FACTOR * radius_km - base_drop)

# ---------------------------------------------------------------------------
# 2. Extract stores.
# ---------------------------------------------------------------------------
COL_NAME, COL_COUNTRY, COL_CITY, COL_ADDR, COL_LAT, COL_LON = 24, 27, 28, 31, 32, 33

def load_stores():
    with open(SRC, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    stores = []
    for r in rows:
        if len(r) <= COL_LON:
            continue
        try:
            lat = float(r[COL_LAT]); lon = float(r[COL_LON])
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        stores.append({"name": r[COL_NAME].strip(), "city": r[COL_CITY].strip(),
                       "address": r[COL_ADDR].strip(), "lat": lat, "lon": lon})
    return stores

# ---------------------------------------------------------------------------
# 3. Geometry + demand grid.
# ---------------------------------------------------------------------------
CELL_KM = 0.20
BUFFER_KM = 4.0
RMIN, RMAX, RSTEP = 1.0, 5.0, 0.1
ALPHA_CANNIB = 0.35   # default weight on cannibalization penalty
BETA_CPO = 0.40       # default weight on CPO premium penalty
KDE_SIGMA = 1.2       # km, bandwidth of the demand kernel (fallback when no OSM data)
RESID_DILATE_KM = 0.3 # treat cells within this distance of residential land as demand
SERVICE_MAX_KM = 3.0  # residential beyond this from every store is "not addressable"
                      # (excludes far villages so coverage reflects serviceable demand)
# Per-store (Option B): radius optimised to cover nearby residential demand.
PS_RMIN, PS_RMAX = 1.3, 3.5   # band; can grow to fill residential gaps, capped
PS_ALPHA = 0.18               # lighter cannibalization penalty -> grow to fill blind spots
PS_BETA = 0.25                # mild CPO awareness -> stop once no new residential is gained
SMOOTH_SIGMA_KM = 1.0         # align very close / co-located stores after optimisation
SMOOTH_ITERS = 1

def make_proj(lst):
    lat0 = sum(s["lat"] for s in lst) / len(lst)
    lon0 = sum(s["lon"] for s in lst) / len(lst)
    kx = 111.320 * math.cos(math.radians(lat0)); ky = 110.574
    return lat0, lon0, kx, ky

def make_xy(lst):
    lat0, lon0, kx, ky = make_proj(lst)
    xs = np.array([(s["lon"] - lon0) * kx for s in lst])
    ys = np.array([(s["lat"] - lat0) * ky for s in lst])
    return xs, ys

def load_residential(city, proj):
    """Load cached OSM residential polygons, projected to local km. Returns list
    of matplotlib Paths, or None if no usable data."""
    fn = f"residential_{city}.json"
    if not os.path.exists(fn):
        return None
    try:
        polys = json.load(open(fn))
    except Exception:
        return None
    if not polys:
        return None
    from matplotlib.path import Path
    lat0, lon0, kx, ky = proj
    paths = []
    for ring in polys:
        pts = [((lon - lon0) * kx, (lat - lat0) * ky) for lat, lon in ring]
        if len(pts) >= 3:
            paths.append(Path(pts))
    return paths or None

def _dilate(w, shape, dist_km):
    """Grid dilation: mark cells within dist_km of any demand cell."""
    rad = max(1, int(round(dist_km / CELL_KM)))
    grid = w.reshape(shape) > 0
    out = grid.copy()
    for dyi in range(-rad, rad + 1):
        for dxi in range(-rad, rad + 1):
            if dyi * dyi + dxi * dxi > rad * rad:
                continue
            out |= np.roll(np.roll(grid, dyi, axis=0), dxi, axis=1)
    return out.reshape(-1).astype(np.float32)

def build_grid_demand(city, lst, xs, ys, proj):
    """Return D (cells x stores) km and demand weight w (cells,)."""
    x0, x1 = xs.min() - BUFFER_KM, xs.max() + BUFFER_KM
    y0, y1 = ys.min() - BUFFER_KM, ys.max() + BUFFER_KM
    gx = np.arange(x0 + CELL_KM / 2, x1, CELL_KM)
    gy = np.arange(y0 + CELL_KM / 2, y1, CELL_KM)
    GX, GY = np.meshgrid(gx, gy)
    cx = GX.ravel().astype(np.float32); cy = GY.ravel().astype(np.float32)
    dx = cx[:, None] - xs[None, :].astype(np.float32)
    dy = cy[:, None] - ys[None, :].astype(np.float32)
    D = np.sqrt(dx * dx + dy * dy).astype(np.float32)

    paths = load_residential(city, proj)
    if paths:
        pts = np.column_stack([cx, cy])
        mask = np.zeros(len(cx), dtype=bool)
        for p in paths:
            mask |= p.contains_points(pts)
        w = mask.astype(np.float32)
        if RESID_DILATE_KM > 0:
            w = _dilate(w, GX.shape, RESID_DILATE_KM)
        # keep only residential within serviceable distance of the network
        din = D.min(axis=1)
        w = w * (din <= SERVICE_MAX_KM).astype(np.float32)
        src = "residential"
    else:
        D2 = (dx * dx + dy * dy)
        w = np.exp(-D2 / (2.0 * KDE_SIGMA * KDE_SIGMA)).sum(axis=1).astype(np.float32)
        src = "kde"
    return D, w, src

def stats_from_radii(D, w, radii):
    cnt = (D <= np.asarray(radii, dtype=np.float32)[None, :]).sum(axis=1)
    total = float(w.sum())
    covered = float(w[cnt >= 1].sum())
    cannib = float(w[cnt >= 2].sum())
    cov = covered / total if total else 0.0
    can = (cannib / covered) if covered else 0.0
    return cov, can

def cpo_index(city, r):
    lo = cpo_for_radius(city, RMIN); hi = cpo_for_radius(city, RMAX)
    return (cpo_for_radius(city, r) - lo) / (hi - lo) if hi > lo else 0.0

def perstore_stats(city, D, w, radii):
    """Per-store metrics: demand share captured, share also shared (cannibalised), CPO."""
    arr = np.asarray(radii, dtype=np.float32)
    member = (D <= arr[None, :])
    cnt = member.sum(axis=1).astype(np.int32)
    total = float(w.sum())
    out = []
    for i in range(len(radii)):
        mi = member[:, i]
        dem = float(w[mi].sum())
        shared = float(w[mi & (cnt >= 2)].sum())
        out.append({
            "reach": round(dem / total, 4),
            "cannib": round(shared / dem, 4) if dem else 0.0,
            "cpo": round(cpo_for_radius(city, radii[i]), 3),
        })
    return out

# ---------------------------------------------------------------------------
# 4a. Option A - uniform radius per city.
# ---------------------------------------------------------------------------
def optimize_uniform(city, D, w, n):
    best = None; curve = []
    for r in np.arange(RMIN, RMAX + 1e-9, RSTEP):
        r = round(float(r), 2)
        cov, can = stats_from_radii(D, w, [r] * n)
        score = cov - ALPHA_CANNIB * can - BETA_CPO * cpo_index(city, r)
        curve.append({"r": r, "cov": round(cov, 4), "can": round(can, 4),
                      "cpo": round(cpo_for_radius(city, r), 3), "score": round(score, 4)})
        if best is None or score > best["score"]:
            best = {"r": r, "cov": cov, "can": can, "score": score}
    return best, curve

# ---------------------------------------------------------------------------
# 4b. Option B - per-store radius driven by local store spacing.
# ---------------------------------------------------------------------------
def optimize_perstore(city, D, w, n, xs, ys, optA_radius):
    """Per-store radius optimised against the (residential) demand surface:

      * each store grows to cover nearby uncovered residential -> fills blind
        spots between stores;
      * it does NOT grow into empty land (no residential demand there), so
        isolated stores (e.g. Bortnychi) stay tight around their district;
      * where residential is already covered by neighbours, the radius shrinks
        to cut cannibalization & CPO -> dense clusters get smaller radii;
      * a light spatial smoothing then aligns co-located / adjacent stores so
        there are no odd mismatched pairs.

    Coordinate ascent on score = coverage - a*cannibalization - b*CPO_index,
    each store bounded to [PS_RMIN, PS_RMAX].
    """
    if n == 1:
        return [round(float(min(optA_radius, PS_RMAX)), 1)]

    total = float(w.sum())
    cand = np.round(np.arange(PS_RMIN, PS_RMAX + 1e-9, RSTEP), 2)
    radii = np.full(n, min(2.0, PS_RMAX), dtype=np.float32)
    member = (D <= radii[None, :])
    cnt = member.sum(axis=1).astype(np.int32)

    def score_from(newc, avg_r):
        cdem = float(w[newc >= 1].sum())
        cov = cdem / total if total else 0.0
        can = (float(w[newc >= 2].sum()) / cdem) if cdem else 0.0
        return cov - PS_ALPHA * can - PS_BETA * cpo_index(city, avg_r)

    for _ in range(8):
        changed = False
        for i in range(n):
            col = D[:, i]
            base_other = cnt - member[:, i].astype(np.int32)
            sum_other = float(radii.sum() - radii[i])
            best_r = float(radii[i]); best_s = -1e9; best_cnt = None
            for cr in cand:
                newc = base_other + (col <= cr).astype(np.int32)
                s = score_from(newc, (sum_other + cr) / n)
                if s > best_s + 1e-9:
                    best_s, best_r, best_cnt = s, float(cr), newc
            if abs(best_r - float(radii[i])) > 1e-9:
                changed = True
            radii[i] = best_r
            member[:, i] = col <= best_r
            cnt = best_cnt
        if not changed:
            break

    # Light spatial smoothing so co-located / adjacent stores match.
    P = np.column_stack([xs, ys]).astype(np.float64)
    diff = P[:, None, :] - P[None, :, :]
    dist = np.sqrt((diff ** 2).sum(axis=2))
    sw = np.exp(-(dist ** 2) / (2.0 * SMOOTH_SIGMA_KM ** 2))
    r = np.asarray(radii, dtype=np.float64)
    for _ in range(SMOOTH_ITERS):
        r = (sw * r[None, :]).sum(axis=1) / sw.sum(axis=1)
    r = np.clip(r, PS_RMIN, PS_RMAX)
    return [round(float(x), 1) for x in r]

# ---------------------------------------------------------------------------
# 5. Main.
# ---------------------------------------------------------------------------
def main():
    stores = load_stores()
    cities = {}
    for s in stores:
        cities.setdefault(s["city"], []).append(s)

    out_cities = {}
    city_D = {}
    for city, lst in sorted(cities.items(), key=lambda kv: -len(kv[1])):
        n = len(lst)
        proj = make_proj(lst)
        xs, ys = make_xy(lst)
        D, w, src = build_grid_demand(city, lst, xs, ys, proj)
        city_D[city] = (D, w)

        bestA, curve = optimize_uniform(city, D, w, n)
        radiiB = optimize_perstore(city, D, w, n, xs, ys, bestA["r"])
        covA, canA = stats_from_radii(D, w, [bestA["r"]] * n)
        covB, canB = stats_from_radii(D, w, radiiB)
        avg_rB = sum(radiiB) / n
        statsB = perstore_stats(city, D, w, radiiB)

        out_cities[city] = {
            "stores": [{"name": s["name"], "address": s["address"],
                        "lat": s["lat"], "lon": s["lon"], "radiusB": radiiB[i],
                        "reachB": statsB[i]["reach"], "cannibB": statsB[i]["cannib"],
                        "cpoB": statsB[i]["cpo"]}
                       for i, s in enumerate(lst)],
            "optionA": {"radius": round(bestA["r"], 2), "coverage": round(covA, 4),
                        "cannibalization": round(canA, 4),
                        "cpo": round(cpo_for_radius(city, bestA["r"]), 3)},
            "optionB": {"avgRadius": round(avg_rB, 2), "coverage": round(covB, 4),
                        "cannibalization": round(canB, 4),
                        "cpo": round(cpo_for_radius(city, avg_rB), 3),
                        "minRadius": round(min(radiiB), 2), "maxRadius": round(max(radiiB), 2)},
            "econ": CITY_ECON.get(city, {"cpo": COUNTRY_CPO, "dropoff": COUNTRY_DROPOFF}),
            "demandSource": src,
            "curve": curve,
        }
        print(f"{city:14s} n={n:3d} [{src:11s}] | A r={bestA['r']:.1f}km cov={covA*100:4.1f}% "
              f"can={canA*100:4.1f}% | B avg={avg_rB:.2f} [{min(radiiB):.1f}-{max(radiiB):.1f}] "
              f"cov={covB*100:4.1f}% can={canB*100:4.1f}%", flush=True)

    # Unified country radius (store-count weighted score across cities).
    best_country = None; country_curve = []
    for r in np.arange(RMIN, RMAX + 1e-9, RSTEP):
        r = round(float(r), 2)
        tot_w = 0; agg_cov = 0; agg_can = 0; agg_score = 0
        for city, lst in cities.items():
            Dc, wc = city_D[city]
            cov, can = stats_from_radii(Dc, wc, [r] * len(lst))
            wt = len(lst)
            agg_cov += cov * wt; agg_can += can * wt
            agg_score += (cov - ALPHA_CANNIB * can - BETA_CPO * cpo_index(city, r)) * wt
            tot_w += wt
        agg_cov /= tot_w; agg_can /= tot_w; agg_score /= tot_w
        country_curve.append({"r": r, "cov": round(agg_cov, 4),
                              "can": round(agg_can, 4), "score": round(agg_score, 4)})
        if best_country is None or agg_score > best_country["score"]:
            best_country = {"r": r, "cov": agg_cov, "can": agg_can, "score": agg_score}

    result = {
        "model": {"cpo_a": round(CPO_A, 4), "cpo_b": round(CPO_B, 4),
                  "dropoff_factor": DROPOFF_FACTOR, "country_cpo": COUNTRY_CPO,
                  "country_dropoff": COUNTRY_DROPOFF, "alpha_cannib": ALPHA_CANNIB,
                  "beta_cpo": BETA_CPO, "rmin": RMIN, "rmax": RMAX,
                  "kde_sigma": KDE_SIGMA, "ps_rmin": PS_RMIN, "ps_rmax": PS_RMAX,
                  "service_max": SERVICE_MAX_KM, "city_econ": CITY_ECON},
        "countryUnified": {"radius": round(best_country["r"], 2),
                           "coverage": round(best_country["cov"], 4),
                           "cannibalization": round(best_country["can"], 4),
                           "curve": country_curve},
        "cities": out_cities,
    }
    with open("store_data.js", "w", encoding="utf-8") as f:
        f.write("window.STORE_DATA = ")
        json.dump(result, f, ensure_ascii=False)
        f.write(";\n")

    # Actionable export.
    with open("recommended_radii.csv", "w", newline="", encoding="utf-8") as f:
        wri = csv.writer(f)
        wri.writerow(["City", "Store name", "Address", "Latitude", "Longitude",
                      "Radius_OptionA_cityUniform_km", "Radius_OptionB_perStore_km",
                      "Radius_OptionC_countryUnified_km",
                      "OptionB_demand_reach_%", "OptionB_cannibalization_%", "OptionB_CPO_EUR"])
        for city, cd in out_cities.items():
            ra = cd["optionA"]["radius"]
            rc = result["countryUnified"]["radius"]
            for s in cd["stores"]:
                wri.writerow([city, s["name"], s["address"], s["lat"], s["lon"],
                              ra, s["radiusB"], rc,
                              round(s["reachB"] * 100, 1), round(s["cannibB"] * 100, 1), s["cpoB"]])
    # Residential overlay (simplified) for the map.
    resid = {}
    for city in out_cities:
        fn = f"residential_{city}.json"
        polys = json.load(open(fn)) if os.path.exists(fn) else []
        simp = []
        for ring in polys:
            lats = [p[0] for p in ring]; lons = [p[1] for p in ring]
            h = (max(lats) - min(lats)) * 111.0
            wd = (max(lons) - min(lons)) * 70.0
            if h * wd < 0.03:           # drop tiny rings (< ~0.03 km^2)
                continue
            step = 2 if len(ring) > 14 else 1
            simp.append([[round(p[0], 4), round(p[1], 4)] for p in ring[::step]])
        resid[city] = simp
    with open("resid_data.js", "w", encoding="utf-8") as f:
        f.write("window.RESID_DATA = ")
        json.dump(resid, f, ensure_ascii=False)
        f.write(";\n")

    print(f"\nCPO model: CPO = {CPO_A:.3f} + {CPO_B:.3f} * dropoff_km")
    print(f"Unified country radius: {best_country['r']:.1f} km "
          f"(cov {best_country['cov']*100:.1f}%, cannib {best_country['can']*100:.1f}%)")
    print("Wrote store_data.js + recommended_radii.csv + resid_data.js")

if __name__ == "__main__":
    main()
