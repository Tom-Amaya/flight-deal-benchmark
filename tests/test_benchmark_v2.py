"""Unit checks for benchmark_v2. Run: python tests/test_benchmark_v2.py

Calendar dates, the season cells, and the engine on a tiny hand-built history.
Every price here is invented.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import benchmark_v2 as bv  # noqa: E402

D = datetime.date
FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# ── Calendar ──────────────────────────────────────────────────────────────────
cal = bv.SeasonCalendar(bv.default_calendar([2025, 2026, 2027, 2028]))
c = lambda d, r="latam": cal.cell(d, r)[0]
check("easter 2027 = Mar 28", bv.easter(2027) == D(2027, 3, 28))
check("easter 2026 = Apr 5", bv.easter(2026) == D(2026, 4, 5))
check("thanksgiving 2026 = Nov 26", bv.thanksgiving(2026) == D(2026, 11, 26))
check("presidents 2027 = Feb 15", bv.presidents_day(2027) == D(2027, 2, 15))
check("Dec 16 -> XMAS_EDGE_PRE", c(D(2026, 12, 16)) == "XMAS_EDGE_PRE")
check("Dec 17 -> XMAS_CORE", c(D(2026, 12, 17)) == "XMAS_CORE")
check("Dec 29 -> XMAS_CORE", c(D(2026, 12, 29)) == "XMAS_CORE")
check("Dec 30 -> XMAS_EDGE_POST", c(D(2026, 12, 30)) == "XMAS_EDGE_POST")
check("Jan 4 2027 -> XMAS_EDGE_POST", c(D(2027, 1, 4)) == "XMAS_EDGE_POST")
check("Jan 5 2027 -> NORMAL", c(D(2027, 1, 5)) == "NORMAL")
check("Dec 10 -> NORMAL", c(D(2026, 12, 10)) == "NORMAL")
check("Nov 21 2026 -> THANKSGIVING", c(D(2026, 11, 21)) == "THANKSGIVING")
check("Nov 29 2026 -> THANKSGIVING", c(D(2026, 11, 29)) == "THANKSGIVING")
check("Nov 30 2026 -> NORMAL", c(D(2026, 11, 30)) == "NORMAL")
check("Jan 29 2027 -> CARNIVAL", c(D(2027, 1, 29)) == "CARNIVAL")
check("Feb 11 2027 -> CARNIVAL", c(D(2027, 2, 11)) == "CARNIVAL")
check("Feb 12 2027 -> PRESIDENTS", c(D(2027, 2, 12)) == "PRESIDENTS")
check("Feb 16 2027 -> PRESIDENTS", c(D(2027, 2, 16)) == "PRESIDENTS")
check("Mar 19 2027 -> EASTER", c(D(2027, 3, 19)) == "EASTER")
check("Apr 2 2027 -> EASTER", c(D(2027, 4, 2)) == "EASTER")
check("Jun 27 -> SUMMER", c(D(2027, 6, 27)) == "SUMMER")
check("Aug 10 -> NORMAL", c(D(2027, 8, 10)) == "NORMAL")
check("US Dec 17 -> NORMAL", c(D(2026, 12, 17), "us") == "NORMAL")
check("US Dec 18 -> XMAS_US", c(D(2026, 12, 18), "us") == "XMAS_US")
check("US Nov 16 -> THANKSGIVING_US", c(D(2026, 11, 16), "us") == "THANKSGIVING_US")
check("US Mar 6 -> SPRING_US", c(D(2027, 3, 6), "us") == "SPRING_US")
check("EU Dec 12 -> XMAS_EU", c(D(2026, 12, 12), "eu") == "XMAS_EU")
check("EU Jul 1 -> SUMMER_EU (hard)", cal.cell(D(2027, 7, 1), "eu")[:2] == ("SUMMER_EU", "hard"))
check("ASIA Jan 10 -> XMAS_ASIA", c(D(2027, 1, 10), "asia") == "XMAS_ASIA")
check("covers 2027", cal.covers_year(2027))
check("does not cover 2029", not cal.covers_year(2029))
# 2028: Carnival (Ash Wed Mar 1 -> Feb 18..Mar 2) overlaps Presidents (Feb 21 -> Feb 18..22): Carnival wins
check("2028 Feb 20 -> CARNIVAL (first soft wins)", c(D(2028, 2, 20)) == "CARNIVAL")
try:
    bv.SeasonCalendar([{"season_id": "X", "region": "latam", "kind": "hard", "start": "2026-12-10", "end": "bad"}])
    check("bad calendar row raises", False)
except ValueError:
    check("bad calendar row raises", True)


# ── Engine on synthetic data ──────────────────────────────────────────────────
def rec(scan, dest, dep, trip, price, cat="latam"):
    return {"Timestamp": f"{scan} 13:00:00", "Route": f"JFK-{dest}", "Trip Type": f"{trip}-day Return",
            "Outbound_Date": str(dep), "Price ($)": price, "Window_Type": f"44d_{trip}", "Category": cat}


T = D(2026, 11, 11)
recs = []
# 6 routes with prices ~400 for departures Jan 20-29, 2027 (the last day falls in
# Carnival) and, for 5 of them, Christmas prices ~800 (AAA ~1200) in the last 30 days
for i, dest in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]):
    for k in range(10):
        recs.append(rec(T - datetime.timedelta(days=k + 1), dest, D(2027, 1, 20) + datetime.timedelta(days=k), 7, 400 + 10 * k))
    if dest != "FFF":
        mult = 2.0 if dest != "AAA" else 3.0
        for k in range(6):
            recs.append(rec(T - datetime.timedelta(days=k + 2), dest, D(2026, 12, 20), 10, 400 * mult + k))
# one more route with a single fare (thin history; no check depends on it)
recs.append(rec(T - datetime.timedelta(days=3), "GGG", D(2027, 2, 20), 7, 500))
h = bv.History(recs, cal)
r = bv.benchmark(h, "BBB", "2027-01-24", 7, T, price=300)
check("normal fare uses D3 or cell, B ~ 445", r["B"] is not None and 400 <= r["B"] <= 490 and r["cell"] == "NORMAL")
check("normal 300 vs ~445 is a deal", r["deal"])
r = bv.benchmark(h, "AAA", "2026-12-21", 10, T, price=1000)
f_raw = h._route_factor("AAA", "XMAS_CORE", T)[0]
check("AAA factor shrunk DOWN from its raw route factor toward the pool",
      r["factor"] is not None and f_raw is not None and r["factor"] < f_raw and r["tag"] == "route")
r = bv.benchmark(h, "FFF", "2026-12-21", 10, T, price=500)
check("FFF no Christmas rows -> pooled p25", r["tag"] == "pooled" and r["s_min"] == 0.25)
r = bv.benchmark(h, "BBB", "2027-01-24", 7, T, price=400)
check("price at the median is not a deal", not r["deal"])
r = bv.benchmark(h, "ZZZ", "2027-01-24", 7, T, price=100)
check("unknown route -> no B", r["B"] is None and r["reason"] == "route_unknown")
r = bv.benchmark(h, "BBB", None, 7, T, price=100)
check("bad input never raises", r["B"] is None and r["reason"] == "bad_input")
r = bv.benchmark(h, "BBB", "2027-07-01", 7, T, price=300)
check("soft cell without D3 needs 25%", r["cell"] == "SUMMER" and r["rung"] == "cell" and r["s_min"] == 0.25)


print(f"\n{len(FAILS)} failed" if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
