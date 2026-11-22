"""Unit checks for historical_median (v1 and v1.1). Run: python tests/test_historical_median.py

Route codes and prices are invented fixtures.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from historical_median import (  # noqa: E402
    compute_median_by_suffix, compute_median_excl_xmas, is_xmas_departure)

CUTOFF = datetime.datetime(2026, 4, 21)  # 30 days before 2026-05-21
D = datetime.date

failures = []


def check(name, got, expected):
    ok = got == expected
    if not ok:
        failures.append(f"{name}: got {got}, expected {expected}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {got}")


def row(route, price, window_type, ts="2026-05-10 13:00:00", dep=""):
    return {"Route": route, "Price ($)": price, "Window_Type": window_type,
            "Timestamp": ts, "Outbound_Date": dep}


print("v1: compute_median_by_suffix")
recs = [row("JFK-AAA", 700, "42d_15"), row("JFK-AAA", 750, "129d_15"), row("JFK-AAA", 800, "150d_15")]
check("suffix _15 pools 3 booking windows", compute_median_by_suffix(recs, "AAA", "15", CUTOFF), (750.0, 3))

recs = [row("JFK-BBB", 600, "42d_15"), row("JFK-BBB", 620, "42d_15"), row("JFK-BBB", 640, "42d_15")]
check("n = 3 boundary", compute_median_by_suffix(recs, "BBB", "15", CUTOFF), (620.0, 3))

recs = [row("JFK-CCC", 740, "180d_14"), row("JFK-CCC", 760, "180d_14")]
check("_14 alone stays below the n >= 3 rule", compute_median_by_suffix(recs, "CCC", "14", CUTOFF), (750.0, 2))

recs = [row("JFK-CCC", 740, "180d_14"), row("JFK-CCC", 760, "42d_15"), row("JFK-CCC", 780, "150d_15")]
check("_14 borrows the _15 neighbour", compute_median_by_suffix(recs, "CCC", "14", CUTOFF, ["15"]), (760.0, 3))

recs = [row("JFK-DDD", 400, "42d_15", "2026-03-01 13:00:00"), row("JFK-DDD", 420, "42d_15"),
        row("JFK-DDD", 440, "42d_15")]
check("rows before the cutoff are excluded", compute_median_by_suffix(recs, "DDD", "15", CUTOFF), (430.0, 2))

recs = [row("JFK-EEE", 650, "42d_15"), row("JFK-EEE", 300, "90d_7")]
check("_7 never pools into _15", compute_median_by_suffix(recs, "EEE", "15", CUTOFF), (650.0, 1))

check("no rows -> (None, 0)", compute_median_by_suffix([], "AAA", "15", CUTOFF), (None, 0))

recs = [row("JFK-FFF", 900, "badformat"), row("JFK-FFF", 700, "42d_15")]
check("malformed Window_Type is skipped", compute_median_by_suffix(recs, "FFF", "15", CUTOFF), (700.0, 1))

print("\nv1.1: Christmas departures excluded")
check("Dec 10 is not Christmas", is_xmas_departure(D(2026, 12, 10)), False)
check("Dec 11 is Christmas", is_xmas_departure(D(2026, 12, 11)), True)
check("Jan 4 is Christmas", is_xmas_departure(D(2027, 1, 4)), True)
check("Jan 5 is not Christmas", is_xmas_departure(D(2027, 1, 5)), False)

# The bug v1.1 fixed: a 7-day pool mixing normal departures (44d_7) with a
# booking window that lands on Christmas (90d_7). v1 is pulled up; v1.1 is not.
pool = [row("JFK-GGG", 400, "44d_7", dep="2026-06-24"), row("JFK-GGG", 410, "44d_7", dep="2026-06-25"),
        row("JFK-GGG", 420, "42d_7", dep="2026-06-21"),
        row("JFK-GGG", 820, "90d_7", dep="2026-12-22"), row("JFK-GGG", 860, "90d_7", dep="2026-12-23"),
        row("JFK-GGG", 900, "90d_7", dep="2026-12-24")]
check("v1 median is inflated by Christmas departures", compute_median_by_suffix(pool, "GGG", "7", CUTOFF),
      (620.0, 6))
check("v1.1 drops them (tier T1)", compute_median_excl_xmas(pool, "GGG", "7", CUTOFF), (410.0, 3, "T1"))

thin = [row("JFK-HHH", 500, "42d_15", dep="2026-06-21"), row("JFK-HHH", 520, "44d_7", dep="2026-06-24"),
        row("JFK-HHH", 540, "75d_10", dep="2026-07-25")]
check("thin suffix falls back to any trip length (T2)", compute_median_excl_xmas(thin, "HHH", "15", CUTOFF),
      (520.0, 3, "T2"))
nodep = [row("JFK-III", 500, "42d_15"), row("JFK-III", 520, "42d_15"), row("JFK-III", 540, "42d_15")]
check("rows without a departure date are excluded", compute_median_excl_xmas(nodep, "III", "15", CUTOFF),
      (None, 0, ""))

print()
if failures:
    print(f"{len(failures)} FAILED")
    for f in failures:
        print("   ", f)
    raise SystemExit(1)
print("ALL PASS")
