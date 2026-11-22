"""
make_synthetic_data.py: a fake fare history with the same shape as the real one.

    python make_synthetic_data.py [OUT.csv] [--seed N]

The real scraped history is not published (it holds third-party fare data).
This script writes a synthetic stand-in so backtest.py and the tests run for
anyone. Every price is invented. The routes are fake codes (JFK-R01 ...).

What it reproduces from the real setup:
- one fare per route per day, from a weekday rotation of booking windows that
  follows the real nominal labels (Mon +44d/7-day trip, Tue +90d/7,
  Wed +180d/14, Thu +42d/15, Sat +75d/10, Sun +42d/7). The real Friday slot
  targeted the next holiday; here it is a fixed +60d/10;
- a Christmas price spike on departures from Dec 11 to Jan 4, smaller bumps for
  Thanksgiving and the Latin America summer, a small trip-length effect, noise,
  and occasional sales.

What it does not reproduce: real route levels, real season sizes, airline
behaviour, anything about the Google price data, or the scheduling bug that
made real booking leads differ from their labels until Oct 2026 (which is why
the real Christmas effect was much larger). Numbers from this file show the
mechanism, not the real result.
"""

import argparse
import csv
import datetime
import random

ROTATION = {0: (44, 7), 1: (90, 7), 2: (180, 14), 3: (42, 15), 4: (60, 10), 5: (75, 10), 6: (42, 7)}
TRIP_FACTOR = {7: 1.00, 10: 1.03, 14: 1.06, 15: 1.06}
HEADERS = ["Timestamp", "Route", "Trip Type", "Outbound_Date", "Price ($)", "Window_Type", "Category"]


def thanksgiving(year):
    d = datetime.date(year, 11, 1)
    return d + datetime.timedelta(days=(3 - d.weekday()) % 7 + 21)


def season_multiplier(dep):
    md = (dep.month, dep.day)
    if (12, 17) <= md <= (12, 29):
        return 2.0
    if (12, 11) <= md <= (12, 16):
        return 1.4
    if md >= (12, 30) or md <= (1, 4):
        return 1.35
    tg = thanksgiving(dep.year)
    if tg - datetime.timedelta(days=5) <= dep <= tg + datetime.timedelta(days=3):
        return 1.1
    if (6, 27) <= md <= (8, 9):
        return 1.1
    return 1.0


def generate(seed=7, n_routes=20, start=datetime.date(2026, 3, 1), end=datetime.date(2026, 10, 19)):
    rng = random.Random(seed)
    routes = [(f"R{i:02d}", rng.uniform(250, 900)) for i in range(1, n_routes + 1)]
    rows = []
    day = start
    while day <= end:
        lead, trip = ROTATION[day.weekday()]
        dep = day + datetime.timedelta(days=lead)
        for code, base in routes:
            price = base * season_multiplier(dep) * TRIP_FACTOR[trip] * rng.lognormvariate(0, 0.12)
            if rng.random() < 0.04:
                price *= 0.70
            rows.append({
                "Timestamp": f"{day.isoformat()} 13:00:00",
                "Route": f"JFK-{code}",
                "Trip Type": f"{trip}-day Return",
                "Outbound_Date": dep.isoformat(),
                "Price ($)": int(round(price)),
                "Window_Type": f"{lead}d_{trip}",
                "Category": "latam",
            })
        day += datetime.timedelta(days=1)
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="Write a synthetic fare history CSV.")
    ap.add_argument("out", nargs="?", default="synthetic_history.csv")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rows = generate(seed=a.seed)
    write_csv(rows, a.out)
    print(f"wrote {len(rows)} synthetic rows to {a.out}")


if __name__ == "__main__":
    main()
