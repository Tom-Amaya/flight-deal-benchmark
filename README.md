# Flight-deal benchmark: finding and fixing a biased "deal" metric

VuelaCasa was a flight-deal alert service I ran from March to November 2026, for New York (JFK) to 38 cities in Latin America and the Caribbean. A daily job pulled fares from Google Flights results (through SerpApi) and stored them. An alert went out when a fare was far enough below a **benchmark**, the "usual" price for that route.

This repository is about that benchmark. The first version said "deal" far too often. A walk-forward backtest measured how often, the diagnosis found two causes, and the benchmark was replaced in two steps.

## Result

5,293 Latin America fares scanned from May 21 to Oct 19, 2026. Each fare was judged only against data from earlier days. v1 and v1.1 are scored with one deal rule: at least 15% and $30 below the benchmark, at most 70%. v2 has its own rule, which asks for 25% instead of 15% where its evidence is thinner. The last row scores v2's benchmark with the same rule as v1, to separate the benchmark from the stricter rule.

| Benchmark | Coverage | Flagged as deals | False deals | Overstated saving (mean / p90) | True deals missed |
|---|---|---|---|---|---|
| v1: 30-day median, same trip length | 99.0% | 1,077 | **48.5%** | 23.0 / 48.8 pts | 63.4% |
| v1.1: v1 without Christmas departures | 99.9% | 595 | **35.5%** | 13.8 / 32.0 pts | 74.0% |
| v2: date-matched + season factors | 93.7% | 424 | **9.9%** | 5.9 / 18.3 pts | 60.7% |
| v2 benchmark, v1's deal rule | 93.7% | 491 | 12.8% | 6.2 / 19.1 pts | 57.6% |

How each column is measured (code in [`backtest.py`](backtest.py), output in [`results/backtest_2026-10-19.json`](results/backtest_2026-10-19.json)):

- **Typical price.** Each fare is scored against the median of the other fares seen on the same route for departures within ±3 days, widened to ±7 days when ±3 has fewer than 4 fares; at least 4 are needed.
  - It takes any trip length and fares scanned before or after the judged one, including fares from a separate holiday-probe job.
  - It leaves out the same itinerary scanned within 10 days.
  - 6.9% of fares have no typical price and are left out of the next three columns.
- **False deals.** The share of flagged fares (those with a typical price: 1,001, 552, 405 and 470) that were less than 5% below the typical price.
- **Overstated saving.** The claimed saving minus the saving against the typical price, in percentage points, over the same flagged fares.
- **True deals missed.** The share of fares at least 20% below their typical price that the method did not flag.
- **Coverage.** The share of fares that got a benchmark. In production, v1 covered its last 1% with the midpoint of Google's typical price range. That fallback isn't reproduced here.

In production, the minimum saving was 1% until Nov 1, 2026, and 15% after that. Production also had a $30 floor, a 70% cap and, on the main lane, Google's own "low price" flag.

## What was wrong with v1

v1 took the median of each route's prices from the last 30 days for the same trip length, whatever the departure date. That had two problems.

**1. Christmas got into the pool.** The scraper rotated through booking windows by weekday, and some of those windows landed on Christmas departures. Christmas departures cost up to about twice the normal price:

| Departure dates | Price vs. the route's normal median |
|---|---|
| Dec 1-10 | ×1.08 |
| Dec 11-16 | ×1.44 |
| Dec 17-29 | ×2.18 |
| Dec 30-Jan 4 | ×1.37 |
| Jan 5-20 | ×1.04 |

It happened three ways:
- **By design.** The 180-day slot (14-day trips) reached Christmas departures in scans from Jun 17 to Jul 8.
- **A scheduling bug.** Until Oct 16, the real booking lead differed from the labels. From Aug 3, all the other slots ran 120 days ahead, so from mid-August they landed on Christmas departures.
- **Normal leads in the fall.** The 90-day slot reached Christmas at its intended lead from mid-September, and the 75-day slot in October.

For 7-day trips, the busiest pool, none of the 30-day pool was Christmas departures on Aug 15. By Sep 1, 57.5% was, and it was still 20-27% in October. The benchmark ran high, so normal fares looked like deals. Among the fares v1 flagged in September scans, 65.8% were false, with the saving overstated by 33.7 points on average. Departure dates overlap with booking lead and trip length, so read the multipliers as a shape, not exact values.

**2. A 30-day median ignores the departure date.** In June scans, removing Christmas departures changed no verdict: v1 and v1.1 were both 43.4% false. So a large part of the error had another cause. Overall, v1.1 lowered the mean overstatement from 23.0 to 13.8 points. Almost all of that gain came from September scans; in July, August and October v1.1 was slightly worse than v1. v2 goes after the rest by comparing each fare with fares for nearby departure dates.

## The fixes

**v1.1 (switched on Oct 26, 2026).** Drop departures from Dec 11 to Jan 4 from the pool. Fall back to any trip length when the pool is too thin. Christmas fares are still judged against the normal price, so they rarely alert. Before going live, v1.1 ran next to the live benchmark for four daily runs, only logging its result, and never turned a non-deal into a deal.

**v2 (built Nov 2026).** It uses three steps:
1. **Same dates.** Compare against past fares for departures within ±3 days, when there are enough of them. The result is kept between 0.75× and 1.30× of step 2's price.
2. **Normal price × season factor.** Otherwise, use the route's normal price times a factor learned for that season. The factor is pulled toward the other routes' factors, and only ever downward.
3. **No benchmark.** In peak seasons with no reliable reference, or when a route has too little history, stay silent.

Thinner evidence needs a bigger discount (25% instead of 15%). v2 was in the same log-only mode when the service closed, so it never sent a live alert.

## Limits

- **The yardstick favors v2.** The typical price is matched on dates, and so is v2's first step. Part of v2's lead comes from that, so read it as an upper bound.
- **v2 was chosen on this data.** One rule (the 25% minimum for soft seasons without a date match) was added after seeing these results. Part of v2's lower false-deal rate comes from its stricter rule: under v1's rule it is 12.8%, not 9.9%. The only out-of-sample check was the log-only run, which lasted about five days (Nov 12-17) and was never scored. In October scans, v2 was 25% false (12 of 48 flagged fares with a typical price).
- **Only the benchmark is modeled.** Production had more checks that this backtest doesn't reproduce: Google's price-level flag, a guard against implausible prices, and a return-flight check. These numbers measure the benchmark alone, not the alerts people received.
- **One season of data.** Each season factor was learned the first time that season was seen. Carnival was worse in v2 than in v1: 50% false on 18 flagged fares, against 42%.
- **Silence over false alarms.** Every method misses 58-74% of true deals, and v2 gives no benchmark for 6.3% of fares.

## Run it

Requires Python 3.10 or newer, standard library only.

```bash
for t in tests/test_*.py; do python "$t"; done           # no network, no keys
python make_synthetic_data.py synthetic_history.csv     # invented prices, fake routes
python backtest.py synthetic_history.csv                # PROBES.csv optional
python backtest.py synthetic_history.csv --start 2026-09-15   # fall scans, when the synthetic 90-day slot hits Christmas
```

The real fare history is not published, because it is derived from Google Flights results. The synthetic file follows the nominal rotation labels and has the same columns, so the code runs end to end. It does not model the scheduling bug, so its Christmas effect is much smaller than the real one. Its numbers show the mechanism, not the real result.

## Files

| File | What it is |
|---|---|
| [`historical_median.py`](historical_median.py) | v1 and v1.1, the benchmarks that ran in production |
| [`benchmark_v2.py`](benchmark_v2.py) | v2: season calendar, date-matched step, season factors, deal rule |
| [`backtest.py`](backtest.py) | Walk-forward backtest of all three, with breakdowns by season and scan month |
| [`make_synthetic_data.py`](make_synthetic_data.py) | Synthetic fare history for running everything without private data |
| [`tests/`](tests) | Unit checks for v1, v1.1 and v2, and an end-to-end run on synthetic data |

## How this was built

I ran the business, set the alert rules (no alert without our own benchmark, and at least 15% below it), and approved each change. The code, the backtests and the diagnosis were done with an AI coding assistant under my direction. The v2 design came from a study and a review panel run with AI models. The 25% rule for soft seasons without a date match was the coding assistant's proposal. I chose the design on an earlier backtest: a separate script with the same walk-forward rule, the same yardstick and the same data, within 0.4 points of these numbers.

---

© 2026 Tom Amaya. Shared for portfolio review; not licensed for reuse. Fare figures are historical aggregates, not price quotes.
