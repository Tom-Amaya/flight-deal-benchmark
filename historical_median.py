"""
historical_median.py: the first two benchmark generations.

A benchmark is the reference price a new fare is compared against. A fare
counted as a deal when it was far enough below its benchmark. In production the
minimum saving was 1% until Nov 1, 2026 and 15% after that, with a $30 floor,
a 70% cap and, on the main lane, Google's own low-price flag.

v1 (May 2026), compute_median_by_suffix
    Median of the route's prices scanned in the last 30 days for the same trip
    length. The trip length is the suffix of Window_Type ("42d_7" -> "7"), so
    different booking windows with the same trip length are pooled. Needs n >= 3.

v1.1 (Oct 2026), compute_median_excl_xmas
    The same pool with Christmas departures (Dec 11 - Jan 4) removed, then a
    route-level fallback across trip lengths. Added after Christmas departures
    got into the pools in late summer (a scheduling bug had pushed the real
    booking lead of several rotation slots to 90-120 days) and pushed the
    benchmark up, so ordinary fares looked like deals.

Stdlib only.
"""

import datetime
import logging
import statistics

log = logging.getLogger(__name__)


def _parse_ts(ts_str: str) -> datetime.datetime | None:
    """Parse a Timestamp cell ("%Y-%m-%d %H:%M:%S" or "%Y-%m-%d"); None on failure."""
    s = str(ts_str).strip()
    try:
        return datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        pass
    return None


def compute_median_by_suffix(
    records: list[dict],
    route_iata: str,
    suffix: str,
    cutoff_dt: datetime.datetime,
    neighbor_suffixes: list[str] | None = None,
) -> tuple[float | None, int]:
    """v1. Return (median_price, n_points) for a route and trip-length suffix.

    Matches on the trip-day suffix of Window_Type (the part after the last "_"),
    so 42d_15, 129d_15 and 150d_15 all pool into suffix "15".

    neighbor_suffixes lets a suffix with no siblings borrow a close one: the
    14-day trip is queried as suffix "14" with neighbor ["15"].

    A row is included when:
    1. Route ends with route_iata (case-insensitive), so "JFK-XXX" and "XXX" match.
    2. The Window_Type suffix is in {suffix} | set(neighbor_suffixes).
    3. Timestamp >= cutoff_dt.
    4. Price ($) is numeric (other rows are logged and skipped).

    Returns (None, 0) when no row passes. The caller applies the n >= 3 rule.
    """
    accepted = {suffix.strip()}
    if neighbor_suffixes:
        accepted |= {s.strip() for s in neighbor_suffixes}

    prices: list[float] = []
    for r in records:
        if not str(r.get("Route", "")).upper().endswith(route_iata.upper()):
            continue
        wt = str(r.get("Window_Type", "")).strip()
        if "_" not in wt:
            continue
        row_suffix = wt.rsplit("_", 1)[-1]
        if row_suffix not in accepted:
            continue
        dt = _parse_ts(r.get("Timestamp", ""))
        if dt is None or dt < cutoff_dt:
            continue
        try:
            prices.append(float(r["Price ($)"]))
        except (TypeError, ValueError):
            log.warning(f"Non-numeric Price ($) in row for {r.get('Route')}; skipping")
    if not prices:
        return None, 0
    return statistics.median(prices), len(prices)


# --- v1.1: Christmas departures excluded ---
# The Christmas window was measured from the scraped history: departures from
# Dec 11 to Jan 4 priced well above each route's usual level, while Dec 1-10
# departures did not. backtest.py prints the curve.

XMAS_START = (12, 11)  # (month, day), inclusive
XMAS_END = (1, 4)      # (month, day), inclusive


def _parse_outbound(value) -> datetime.date | None:
    """Parse an Outbound_Date cell (YYYY-MM-DD or M/D/YYYY); None if empty or bad."""
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        return None


def is_xmas_departure(d: datetime.date) -> bool:
    """True for departures from Dec 11 to Jan 4 (inclusive), any year."""
    md = (d.month, d.day)
    return md >= XMAS_START or md <= XMAS_END


def compute_median_excl_xmas(
    records: list[dict],
    route_iata: str,
    suffix: str,
    cutoff_dt: datetime.datetime,
    neighbor_suffixes: list[str] | None = None,
    min_n: int = 3,
) -> tuple[float | None, int, str]:
    """v1.1. Return (median_price, n_points, tier) with Christmas departures excluded.

    Same filters as compute_median_by_suffix, plus: Outbound_Date must parse and
    must not fall between Dec 11 and Jan 4. Rows without a departure date are
    excluded (their season is unknown).

    Ladder, each tier needs n >= min_n:
      T1  route x trip-length suffix (+ neighbor_suffixes), no Christmas
      T2  route x any trip length, no Christmas. Trip length moved prices a few
          percent; season moved them up to ~2x, so loosening length is the safe
          direction.
    Returns (None, n_T2, "") when neither tier reaches min_n; the caller then
    keeps its v1 benchmark.
    """
    accepted = {suffix.strip()}
    if neighbor_suffixes:
        accepted |= {s.strip() for s in neighbor_suffixes}

    tier1: list[float] = []
    tier2: list[float] = []
    for r in records:
        if not str(r.get("Route", "")).upper().endswith(route_iata.upper()):
            continue
        wt = str(r.get("Window_Type", "")).strip()
        if "_" not in wt:
            continue
        dt = _parse_ts(r.get("Timestamp", ""))
        if dt is None or dt < cutoff_dt:
            continue
        ob = _parse_outbound(r.get("Outbound_Date", ""))
        if ob is None or is_xmas_departure(ob):
            continue
        try:
            p = float(r["Price ($)"])
        except (KeyError, TypeError, ValueError):
            continue
        tier2.append(p)
        if wt.rsplit("_", 1)[-1] in accepted:
            tier1.append(p)
    if len(tier1) >= min_n:
        return statistics.median(tier1), len(tier1), "T1"
    if len(tier2) >= min_n:
        return statistics.median(tier2), len(tier2), "T2"
    return None, len(tier2), ""
