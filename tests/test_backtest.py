"""End-to-end check of backtest.py on synthetic data. Run: python tests/test_backtest.py

Uses make_synthetic_data.py (invented prices, fake routes), so it runs without
the private fare history. It checks that the pipeline runs and that the
Christmas-pooling bias shows up in the same direction as on real data: in the
synthetic data's fall scans, when its 90-day slot lands on Christmas
departures, v1 overstates savings more than v1.1. The effect is much smaller
here than in the real data, and the synthetic numbers are not the real result.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest  # noqa: E402
import make_synthetic_data as syn  # noqa: E402

FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


rows = [{k: str(v) for k, v in r.items()} for r in syn.generate(seed=7)]
check("synthetic history has rows", len(rows) > 1000)

full = backtest.run(rows, start=datetime.date(2026, 5, 21))
m = full["methods"]
check("all three methods judge the same fares", m["v1"]["judged"] == m["v1.1"]["judged"] == m["v2"]["judged"] > 0)
check("v1 and v1.1 cover almost every fare", m["v1"]["coverage_pct"] >= 95 and m["v1.1"]["coverage_pct"] >= 95)
check("v2 covers most fares", m["v2"]["coverage_pct"] >= 85)
curve = full["season_curve"]
check("season curve peaks on Dec 17-29 departures",
      curve["Dec 17-29"]["median_ratio"] > curve["Dec 11-16"]["median_ratio"] > curve["Dec 1-10"]["median_ratio"])

fall = backtest.run(rows, start=datetime.date(2026, 9, 15))["methods"]
check("fall scans: v1 overstates savings more than v1.1",
      fall["v1"]["mean_over_pts"] > fall["v1.1"]["mean_over_pts"])
check("fall scans: v1 flags more fares than v1.1", fall["v1"]["flagged"] > fall["v1.1"]["flagged"])

print(f"\n{len(FAILS)} failed" if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
