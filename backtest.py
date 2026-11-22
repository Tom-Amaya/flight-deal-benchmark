"""
backtest.py: walk-forward backtest of the three benchmark generations.

    python backtest.py HISTORY.csv [PROBES.csv] [--start YYYY-MM-DD] [--json OUT.json]

HISTORY.csv has one row per scraped fare, with the columns
    Timestamp, Route, Trip Type, Outbound_Date, Price ($), Window_Type, Category
PROBES.csv (optional) holds extra fares from the holiday-probe lane. They only
feed the oracle, never a benchmark. Columns:
    run_date, route, outbound_date, return_date, price, internal_only

Judged fares: Latin America rotation fares with a departure date, scanned on or
after --start (default 2026-05-21). Every benchmark uses only rows scanned on
EARLIER days (walk-forward).

  v1    median of the same trip length, last 30 days (historical_median.py)
  v1.1  v1 with Christmas departures removed; falls back to v1 when thin
  v2    D3 -> base x season factor (benchmark_v2.py), with its own deal rule
  v2_15 the same v2 benchmark, judged with the uniform rule below

v1 and v1.1 are judged with a uniform deal rule: saving >= 15%, >= $30 below the
benchmark, <= 70%. v2 uses its own rule (25% instead of 15% where its evidence
is thinner); "v2_15" scores v2's benchmark with the uniform rule, to separate
the benchmark from the stricter rule. In
production v1 fell back to the midpoint of Google's typical price range when it
had fewer than 3 points; that fallback is not reproduced here, so those fares
count as having no benchmark.

Oracle, the "typical" price a judged fare is scored against: the median of all
other observed fares on the same route departing within +-3 days (widened to
+-7), scanned at any time, excluding the same itinerary scanned within 10 days;
it needs n >= 4. Because the oracle is itself date-matched, v2's D3 rung is
close to it by construction. Read v2's advantage as an upper bound.

Metrics, per method:
  coverage  share of judged fares that get a benchmark
  flagged   fares the method calls a deal
  false     flagged fares (with an oracle) whose oracle saving is under 5%
  over      claimed saving minus oracle saving, in points (mean and p90)
  missed    fares with an oracle saving of 20%+ that were not flagged

Prints aggregates only. Stdlib only.
"""

import argparse
import csv
import datetime
import json
import statistics

import benchmark_v2 as bv
import historical_median as hm

S_MIN, S_MAX, ABS_MIN = 0.15, 0.70, 30
NEIGHBORS = {"14": ["15"]}
RUN_TIME = datetime.time(13, 0)   # the daily job ran at 13:00 UTC
CUTOFF_DAYS = 30


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _suffix(window_type):
    wt = str(window_type or "").strip()
    return wt.rsplit("_", 1)[-1] if "_" in wt else wt


def judged_fares(history, start):
    """Latin America rotation fares the engine can place in a season, scanned >= start."""
    out = []
    for r in history:
        lane = str(r.get("Lane", "") or "").strip().lower()
        if lane not in ("", "rot", "rotation"):
            continue
        if bv.region_for(r.get("Category")) != "latam":
            continue
        route = str(r.get("Route", "")).strip().upper()
        dest = route.split("-")[-1] if route else ""
        scan = bv.parse_date(r.get("Timestamp"))
        dep = bv.parse_date(r.get("Outbound_Date"))
        trip = bv._trip_days(r.get("Window_Type"), r.get("Trip Type"))
        try:
            price = float(r.get("Price ($)"))
        except (TypeError, ValueError):
            continue
        if not dest or scan is None or dep is None or trip is None or price <= 0 or scan < start:
            continue
        out.append({"dest": dest, "scan": scan, "dep": dep, "trip": trip, "price": price,
                    "suffix": _suffix(r.get("Window_Type"))})
    return out


def index_by_dest(history):
    """dest -> list of (scan_date, raw_row), for the walk-forward filter."""
    idx = {}
    for r in history:
        route = str(r.get("Route", "")).strip().upper()
        dest = route.split("-")[-1] if route else ""
        scan = bv.parse_date(r.get("Timestamp"))
        if dest and scan is not None:
            idx.setdefault(dest, []).append((scan, r))
    return idx


def bench_v1(rows, dest, suffix, cutoff):
    med, n = hm.compute_median_by_suffix(rows, dest, suffix, cutoff, NEIGHBORS.get(suffix))
    return round(med) if (med is not None and n >= 3) else None


def bench_v11(rows, dest, suffix, cutoff):
    v1 = bench_v1(rows, dest, suffix, cutoff)
    med, _n, _tier = hm.compute_median_excl_xmas(rows, dest, suffix, cutoff, NEIGHBORS.get(suffix))
    return round(med) if med is not None else v1


def uniform_deal(b, price):
    if not b:
        return None, False
    s = 1 - price / b
    return s, (S_MIN <= s <= S_MAX and (b - price) >= ABS_MIN)


def build_oracle_pool(hist, probes):
    pool = {}
    for dest, rows in hist.by_route.items():
        if hist.region_of[dest] != "latam":
            continue
        for (s, d, tr, p, _c, _n) in rows:
            pool.setdefault(dest, []).append((s, d, tr, p))
    for r in probes or ():
        if str(r.get("internal_only", "")).strip().lower() == "true":
            continue
        dest = str(r.get("route", "")).split("-")[-1]
        s, d = bv.parse_date(r.get("run_date")), bv.parse_date(r.get("outbound_date"))
        ret = bv.parse_date(r.get("return_date"))
        try:
            p = float(r.get("price"))
        except (TypeError, ValueError):
            continue
        if dest in pool and s and d and ret and p > 0:
            pool[dest].append((s, d, (ret - d).days, p))
    return pool


def oracle(pool, dest, s_i, d_i, tr_i, p_i):
    for w in (3, 7):
        ps = [p for (s, d, tr, p) in pool.get(dest, ())
              if abs((d - d_i).days) <= w
              and not (d == d_i and tr == tr_i and abs((s - s_i).days) <= 10)]
        if len(ps) >= 4:
            return statistics.median(ps)
    return None


def _p90(xs):
    """90th percentile, lower nearest-rank (no interpolation)."""
    xs = sorted(xs)
    return xs[int(0.9 * (len(xs) - 1))] if xs else None


def summarize(rows):
    """rows: list of (has_benchmark, claimed_saving, flagged, oracle_saving)."""
    n = len(rows)
    cov = sum(1 for r in rows if r[0])
    flagged = [r for r in rows if r[2]]
    scored = [r for r in flagged if r[3] is not None]
    false = [r for r in scored if r[3] < 0.05]
    over = [r[1] - r[3] for r in scored]
    true20 = [r for r in rows if r[3] is not None and r[3] >= 0.20]
    missed = [r for r in true20 if not r[2]]
    return {
        "judged": n,
        "coverage_pct": round(100 * cov / n, 1) if n else None,
        "flagged": len(flagged),
        "scored": len(scored),
        "false_pct": round(100 * len(false) / len(scored), 1) if scored else None,
        "mean_over_pts": round(100 * statistics.mean(over), 1) if over else None,
        "p90_over_pts": round(100 * _p90(over), 1) if over else None,
        "true_deals_20pct": len(true20),
        "missed_pct": round(100 * len(missed) / len(true20), 1) if true20 else None,
    }


def season_curve(hist):
    """Median of price / route NORMAL median, by departure period (Latin America).
    Aggregate only. Departure period is confounded with booking lead and trip
    length, so read it as a shape, not a precise multiplier."""
    buckets = [("Dec 1-10", (12, 1), (12, 10)), ("Dec 11-16", (12, 11), (12, 16)),
               ("Dec 17-29", (12, 17), (12, 29)), ("Dec 30-Jan 4", (12, 30), (1, 4)),
               ("Jan 5-20", (1, 5), (1, 20))]

    def bucket(d):
        md = (d.month, d.day)
        for name, a, b in buckets:
            if (a <= b and a <= md <= b) or (a > b and (md >= a or md <= b)):
                return name
        return None

    ratios = {name: [] for name, _a, _b in buckets}
    for dest, rows in hist.by_route.items():
        if hist.region_of[dest] != "latam":
            continue
        normal = [p for (_s, _d, _tr, p, _c, nrm) in rows if nrm]
        if len(normal) < 8:
            continue
        m = statistics.median(normal)
        for (_s, d, _tr, p, _c, _n) in rows:
            name = bucket(d)
            if name:
                ratios[name].append(p / m)
    return {name: {"median_ratio": round(statistics.median(v), 2) if v else None, "n": len(v)}
            for name, v in ratios.items()}


def christmas_share_of_pool(history, start, trip_suffix="7"):
    """Share of v1's 30-day pool for one trip length (all Latin America routes)
    whose departure falls in the Christmas window, on the 1st and 15th of each
    month from `start`. Shows when the seasonal contamination began. Aggregate only."""
    fares = []
    for r in history:
        if bv.region_for(r.get("Category")) != "latam" or _suffix(r.get("Window_Type")) != trip_suffix:
            continue
        scan, dep = bv.parse_date(r.get("Timestamp")), bv.parse_date(r.get("Outbound_Date"))
        if scan and dep:
            fares.append((scan, dep))
    if not fares:
        return {}
    last = max(s for s, _d in fares)
    out = {}
    day = start.replace(day=1)
    while day <= last:
        for t in (day, day.replace(day=15)):
            if start <= t <= last:
                pool = [d for (s, d) in fares if t - datetime.timedelta(days=CUTOFF_DAYS) <= s < t]
                xmas = sum(1 for d in pool if hm.is_xmas_departure(d))
                out[t.isoformat()] = {"share_pct": round(100 * xmas / len(pool), 1) if pool else None,
                                      "pool": len(pool)}
        day = (day + datetime.timedelta(days=32)).replace(day=1)
    return out


def run(history, probes=None, start=datetime.date(2026, 5, 21)):
    cal = bv.SeasonCalendar(bv.default_calendar(range(2025, 2029)))
    hist = bv.History(history, cal)
    pool = build_oracle_pool(hist, probes)
    by_dest = index_by_dest(history)
    fares = judged_fares(history, start)

    results = {"v1": [], "v1.1": [], "v2": [], "v2_15": []}
    by_cell = {}
    by_month = {}
    for f in fares:
        dest, t, price = f["dest"], f["scan"], f["price"]
        past = [r for (s, r) in by_dest.get(dest, ()) if s < t]
        cutoff = datetime.datetime.combine(t, RUN_TIME) - datetime.timedelta(days=CUTOFF_DAYS)
        o = oracle(pool, dest, t, f["dep"], f["trip"], price)
        s_star = (1 - price / o) if o else None

        for name, fn in (("v1", bench_v1), ("v1.1", bench_v11)):
            b = fn(past, dest, f["suffix"], cutoff)
            s, deal = uniform_deal(b, price)
            results[name].append((b is not None, s, deal, s_star))

        res = bv.benchmark(hist, dest, f["dep"], f["trip"], t, price=price)
        results["v2"].append((res["B"] is not None, res["savings"], res["deal"], s_star))
        s15, deal15 = uniform_deal(res["B"], price)
        results["v2_15"].append((res["B"] is not None, s15, deal15, s_star))
        cell = by_cell.setdefault(res["cell"] or "NONE", {n: [] for n in results})
        month = by_month.setdefault(t.strftime("%Y-%m"), {n: [] for n in results})
        for name in results:
            cell[name].append(results[name][-1])
            month[name].append(results[name][-1])

    out = {
        "start": start.isoformat(),
        "history_rows": len(history),
        "methods": {name: summarize(rows) for name, rows in results.items()},
        "by_departure_cell": {c: {name: summarize(rows) for name, rows in d.items()}
                              for c, d in sorted(by_cell.items())},
        "by_scan_month": {m: {name: summarize(rows) for name, rows in d.items()}
                          for m, d in sorted(by_month.items())},
        "christmas_share_of_7day_pool": christmas_share_of_pool(history, start),
        "season_curve": season_curve(hist),
    }
    return out


def print_report(out):
    print(f"Walk-forward backtest, judged fares scanned >= {out['start']} "
          f"({out['history_rows']} history rows)\n")
    hdr = f"{'method':<6} {'judged':>6} {'cov%':>6} {'flagged':>7} {'false%':>7} " \
          f"{'mean over':>9} {'p90 over':>8} {'missed%':>8}"
    print(hdr)
    for name, m in out["methods"].items():
        print(f"{name:<6} {m['judged']:>6} {m['coverage_pct']!s:>6} {m['flagged']:>7} "
              f"{m['false_pct']!s:>7} {m['mean_over_pts']!s:>9} {m['p90_over_pts']!s:>8} "
              f"{m['missed_pct']!s:>8}")
    print("\nFalse-deal rate by departure cell (false % of flagged fares with an oracle):")
    for cell, d in out["by_departure_cell"].items():
        parts = []
        for n in ("v1", "v1.1", "v2"):
            fp = d[n]["false_pct"]
            parts.append(f"{n} {'-' if fp is None else str(fp) + '%'} of {d[n]['scored']}")
        print(f"  {cell:<15} " + " | ".join(parts))
    print("\nBy scan month (false % of flagged fares with an oracle; mean overstatement):")
    for month, d in out["by_scan_month"].items():
        parts = []
        for n in ("v1", "v1.1", "v2"):
            fp, mo = d[n]["false_pct"], d[n]["mean_over_pts"]
            parts.append(f"{n} {'-' if fp is None else str(fp) + '%'} of {d[n]['scored']}, "
                         f"over {'-' if mo is None else mo}")
        print(f"  {month}  " + " | ".join(parts))
    print("\nChristmas departures in v1's 30-day pool of 7-day trips:")
    for day, v in out["christmas_share_of_7day_pool"].items():
        print(f"  {day}: {v['share_pct']}% of {v['pool']}")
    print("\nSeason curve (price / route normal median, by departure period):")
    for name, v in out["season_curve"].items():
        print(f"  {name:<13} x{v['median_ratio']} (n={v['n']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("history")
    ap.add_argument("probes", nargs="?")
    ap.add_argument("--start", default="2026-05-21")
    ap.add_argument("--json")
    a = ap.parse_args()
    out = run(load_csv(a.history), load_csv(a.probes) if a.probes else None,
              datetime.date.fromisoformat(a.start))
    print_report(out)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
