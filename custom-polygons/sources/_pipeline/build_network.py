#!/usr/bin/env python3
"""Network-agnostic delivery radius optimization (adapted from FORA build_analysis.py).

Reads <network_dir>/stores.json + <network_dir>/econ.json and writes
<network_dir>/store_data.js, resid_data.js, recommended_radii.csv.

For each city computes:
  * Option A — one unified radius per city (balance coverage/cannibalization/CPO).
  * Option B — a per-store radius driven by local store spacing / residential demand.
Plus a country-unified radius (Option C).

Economics come from econ.json (per-city avg courier drop-off distance + CPO proxy,
delivered orders). A linear CPO-vs-distance model is fit from the city observations;
if the cities have too little drop-off spread (or <2 usable cities) it falls back to
a default slope so CPO still rises sensibly with radius.

Usage: python3 build_network.py <network_dir>
"""
import csv, json, math, os, sys
from pathlib import Path
import numpy as np

NET_DIR = Path(sys.argv[1]).resolve()
ECON = json.loads((NET_DIR / "econ.json").read_text(encoding="utf-8"))
CITY_ECON = ECON["cities"]
COUNTRY_CPO = ECON.get("country_cpo", 2.0)
COUNTRY_DROPOFF = ECON.get("country_dropoff", 2.6)

# Optional per-network overrides (e.g. allow radii up to 6 km for a network).
PARAMS = json.loads((NET_DIR / "params.json").read_text(encoding="utf-8")) \
    if (NET_DIR / "params.json").exists() else {}

DEFAULT_CPO_SLOPE = 0.2654  # EUR per km of drop-off (FORA-derived fallback)

# ---------------------------------------------------------------------------
# CPO model.
# ---------------------------------------------------------------------------
def fit_cpo_model():
    pts = [(v["dropoff"], v["cpo"]) for v in CITY_ECON.values() if v.get("orders", 0) >= 4000]
    n = len(pts)
    if n >= 2:
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        denom = sum((x - mx) ** 2 for x, y in pts)
        if denom >= 1e-3:  # enough drop-off spread for a stable slope
            b = sum((x - mx) * (y - my) for x, y in pts) / denom
            return my - b * mx, b
        return my - DEFAULT_CPO_SLOPE * mx, DEFAULT_CPO_SLOPE
    if n == 1:
        x, y = pts[0]
        return y - DEFAULT_CPO_SLOPE * x, DEFAULT_CPO_SLOPE
    return COUNTRY_CPO - DEFAULT_CPO_SLOPE * COUNTRY_DROPOFF, DEFAULT_CPO_SLOPE

CPO_A, CPO_B = fit_cpo_model()
DROPOFF_FACTOR = 0.60  # mean drop-off dist ~= factor * radius

def cpo_for_radius(city, radius_km):
    econ = CITY_ECON.get(city)
    base_cpo = econ["cpo"] if econ else COUNTRY_CPO
    base_drop = econ["dropoff"] if econ else COUNTRY_DROPOFF
    return base_cpo + CPO_B * (DROPOFF_FACTOR * radius_km - base_drop)

# ---------------------------------------------------------------------------
# Stores.
# ---------------------------------------------------------------------------
def load_stores():
    data = json.loads((NET_DIR / "stores.json").read_text(encoding="utf-8"))
    stores = []
    for r in data:
        try:
            lat = float(r["lat"]); lon = float(r["lon"])
        except (ValueError, KeyError, TypeError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        stores.append({"name": r.get("name", "").strip(), "city": r.get("city", "").strip(),
                       "address": r.get("address", "").strip(), "lat": lat, "lon": lon})
    return stores

# ---------------------------------------------------------------------------
# Geometry + demand grid.
# ---------------------------------------------------------------------------
CELL_KM = 0.20
BUFFER_KM = PARAMS.get("buffer_km", 4.0)
RMIN, RSTEP = 1.0, 0.1
RMAX = PARAMS.get("rmax", 5.0)
ALPHA_CANNIB = 0.35
BETA_CPO = 0.40
KDE_SIGMA = 1.2
RESID_DILATE_KM = 0.3
SERVICE_MAX_KM = PARAMS.get("service_max_km", 3.0)
PS_RMIN, PS_RMAX = 1.3, PARAMS.get("ps_rmax", 3.5)
PS_ALPHA = 0.18
PS_BETA = 0.25
SMOOTH_SIGMA_KM = 1.0
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
    fn = NET_DIR / f"residential_{city}.json"
    if not fn.exists():
        return None
    try:
        polys = json.loads(fn.read_text())
    except Exception:
        return None
    if not polys:
        return None
    from matplotlib.path import Path as MplPath
    lat0, lon0, kx, ky = proj
    paths = []
    for ring in polys:
        pts = [((lon - lon0) * kx, (lat - lat0) * ky) for lat, lon in ring]
        if len(pts) >= 3:
            paths.append(MplPath(pts))
    return paths or None

def _dilate(w, shape, dist_km):
    rad = max(1, int(round(dist_km / CELL_KM)))
    grid = w.reshape(shape) > 0
    out = grid.copy()
    for dyi in range(-rad, rad + 1):
        for dxi in range(-rad, rad + 1):
            if dyi * dyi + dxi * dxi > rad * rad:
                continue
            out |= np.roll(np.roll(grid, dyi, axis=0), dxi, axis=1)
    return out.reshape(-1).astype(np.float32)

def _gaussian_blur(w2d, sigma_cells):
    """Light separable-ish Gaussian smoothing over the 2D grid (few np.roll ops)."""
    rad = max(1, int(round(sigma_cells * 2)))
    out = np.zeros_like(w2d)
    tot = 0.0
    for dyi in range(-rad, rad + 1):
        for dxi in range(-rad, rad + 1):
            g = np.exp(-(dyi * dyi + dxi * dxi) / (2.0 * sigma_cells * sigma_cells))
            out += g * np.roll(np.roll(w2d, dyi, axis=0), dxi, axis=1)
            tot += g
    return out / tot


def build_grid_demand(city, lst, xs, ys, proj):
    x0, x1 = xs.min() - BUFFER_KM, xs.max() + BUFFER_KM
    y0, y1 = ys.min() - BUFFER_KM, ys.max() + BUFFER_KM
    gx = np.arange(x0 + CELL_KM / 2, x1, CELL_KM)
    gy = np.arange(y0 + CELL_KM / 2, y1, CELL_KM)
    GX, GY = np.meshgrid(gx, gy)
    cx = GX.ravel().astype(np.float32); cy = GY.ravel().astype(np.float32)
    dx = cx[:, None] - xs[None, :].astype(np.float32)
    dy = cy[:, None] - ys[None, :].astype(np.float32)
    D = np.sqrt(dx * dx + dy * dy).astype(np.float32)

    # Real order-density demand surface (where deliveries actually happen).
    # Cities with too few historical orders (e.g. onboarding) fall back to residential.
    if PARAMS.get("demand") == "orders":
        of = NET_DIR / f"orders_{city}.json"
        if of.exists():
            pts = json.loads(of.read_text())
            if sum(c for _, _, c in pts) >= PARAMS.get("min_orders", 30):
                lat0, lon0, kx, ky = proj
                w2d = np.zeros((len(gy), len(gx)), dtype=np.float64)
                for lat, lon, c in pts:
                    px = (lon - lon0) * kx; py = (lat - lat0) * ky
                    ix = int(round((px - gx[0]) / CELL_KM))
                    iy = int(round((py - gy[0]) / CELL_KM))
                    if 0 <= ix < len(gx) and 0 <= iy < len(gy):
                        w2d[iy, ix] += c
                w2d = _gaussian_blur(w2d, sigma_cells=2.0)  # ~0.4 km smoothing
                return D, w2d.ravel().astype(np.float32), "orders"

    paths = load_residential(city, proj)
    if paths:
        pts = np.column_stack([cx, cy])
        mask = np.zeros(len(cx), dtype=bool)
        for p in paths:
            mask |= p.contains_points(pts)
        w = mask.astype(np.float32)
        if RESID_DILATE_KM > 0:
            w = _dilate(w, GX.shape, RESID_DILATE_KM)
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
            "reach": round(dem / total, 4) if total else 0.0,
            "cannib": round(shared / dem, 4) if dem else 0.0,
            "cpo": round(cpo_for_radius(city, radii[i]), 3),
        })
    return out

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

def optimize_perstore(city, D, w, n, xs, ys, optA_radius):
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

    P = np.column_stack([xs, ys]).astype(np.float64)
    diff = P[:, None, :] - P[None, :, :]
    dist = np.sqrt((diff ** 2).sum(axis=2))
    sw = np.exp(-(dist ** 2) / (2.0 * SMOOTH_SIGMA_KM ** 2))
    r = np.asarray(radii, dtype=np.float64)
    for _ in range(SMOOTH_ITERS):
        r = (sw * r[None, :]).sum(axis=1) / sw.sum(axis=1)
    r = np.clip(r, PS_RMIN, PS_RMAX)
    return [round(float(x), 1) for x in r]

def resil_coverage(D, w, radii, p):
    """Expected share of demand served when each store is independently
    unavailable with probability p. A cell reachable by k stores is served
    with probability 1 - p**k (a backup store rescues it when the primary is down)."""
    member = D <= np.asarray(radii, dtype=np.float32)[None, :]
    k = member.sum(axis=1)
    total = float(w.sum())
    served = np.where(k > 0, 1.0 - p ** k, 0.0)
    return float((w * served).sum()) / total if total else 0.0


def optimize_resilient(city, D, w, n, backup_target, cov_target=0.97,
                       rmin=1.0, rmax=None, step=0.1):
    """Single resilient radius per city: the smallest uniform radius such that
    >= backup_target of order demand is reachable by TWO OR MORE stores (so any
    single store outage still leaves a backup for those customers).

    A uniform (symmetric) radius is used on purpose: minimizing individual radii
    degenerates (stores shrink to a floor and offload orders to a far neighbour,
    which RAISES delivery distance under nearest-store dispatch). A common radius
    keeps every store serving its own nearby orders (low CPO) while guaranteeing
    mutual backup. For a lone store (no redundancy possible) it just covers the
    order mass.
    """
    if rmax is None:
        rmax = PS_RMAX
    total = float(w.sum())

    def cov1(R):
        return float(w[(D[:, 0] <= R) if n == 1 else ((D <= R).sum(1) >= 1)].sum()) / total \
            if total else 0.0

    def backup(R):
        return float(w[(D <= R).sum(1) >= 2].sum()) / total if total else 0.0

    if n == 1:
        for R in np.arange(rmin, rmax + 1e-9, step):
            if cov1(round(float(R), 1)) >= cov_target or R >= rmax - 1e-9:
                return [round(float(R), 1)]
        return [round(rmax, 1)]

    for R in np.arange(rmin, rmax + 1e-9, step):
        if backup(R) >= backup_target - 1e-9:
            return [round(float(R), 1)] * n
    return [round(rmax, 1)] * n


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
        _p = PARAMS.get("outage_prob", 0.0)
        if _p and _p > 0 and src == "orders":
            radiiB = optimize_resilient(city, D, w, n,
                                        PARAMS.get("backup_target", 0.85),
                                        rmin=1.0, rmax=PS_RMAX)
        elif _p and _p > 0:
            # no real order history (e.g. onboarding city) -> provisional default
            radiiB = [round(min(bestA["r"], 3.0), 1)] * n
        else:
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
        print(f"{city:16s} n={n:3d} [{src:11s}] | A r={bestA['r']:.1f}km cov={covA*100:4.1f}% "
              f"can={canA*100:4.1f}% | B avg={avg_rB:.2f} [{min(radiiB):.1f}-{max(radiiB):.1f}] "
              f"cov={covB*100:4.1f}% can={canB*100:4.1f}%", flush=True)

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
    if PARAMS.get("recommend"):
        result["recommendation"] = PARAMS["recommend"]
    (NET_DIR / "store_data.js").write_text(
        "window.STORE_DATA = " + json.dumps(result, ensure_ascii=False) + ";\n",
        encoding="utf-8")

    with open(NET_DIR / "recommended_radii.csv", "w", newline="", encoding="utf-8") as f:
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

    resid = {}
    for city in out_cities:
        fn = NET_DIR / f"residential_{city}.json"
        polys = json.loads(fn.read_text()) if fn.exists() else []
        simp = []
        for ring in polys:
            lats = [p[0] for p in ring]; lons = [p[1] for p in ring]
            h = (max(lats) - min(lats)) * 111.0
            wd = (max(lons) - min(lons)) * 70.0
            if h * wd < 0.03:
                continue
            step = 2 if len(ring) > 14 else 1
            simp.append([[round(p[0], 4), round(p[1], 4)] for p in ring[::step]])
        resid[city] = simp
    (NET_DIR / "resid_data.js").write_text(
        "window.RESID_DATA = " + json.dumps(resid, ensure_ascii=False) + ";\n",
        encoding="utf-8")

    print(f"\nCPO model: CPO = {CPO_A:.3f} + {CPO_B:.3f} * dropoff_km")
    print(f"Unified country radius: {best_country['r']:.1f} km "
          f"(cov {best_country['cov']*100:.1f}%, cannib {best_country['can']*100:.1f}%)")
    print("Wrote store_data.js + recommended_radii.csv + resid_data.js")


if __name__ == "__main__":
    main()
