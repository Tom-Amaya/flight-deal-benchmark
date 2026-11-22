"""
benchmark_v2.py: the third benchmark generation (Nov 2026).

One benchmark() for every alert lane. Pure Python, stdlib only. benchmark()
never raises: any failure returns B = None with a reason, and no saving is
claimed without a reference (fail-closed).

    ladder:  D3  ->  base x season factor  ->  no benchmark

No benchmark when the base is too thin, or in a hard season cell with no
route or pooled factor.

- cell(dep, region): the season cell the DEPARTURE date falls in, from a
  season calendar (default_calendar() builds one).
  Hard cells: Christmas (Latin America in 3 bands; US / EU / Asia one band
  each) and EU summer. Soft cells: Thanksgiving, Carnival, Easter, Latin
  America summer, Presidents' Day, US spring. Anything else is NORMAL.
- base(route, t): median of the route's rows scanned before t whose departure
  is NORMAL and whose return does not land in a hard cell, all trip lengths
  pooled; scan window 30 -> 60 -> 90 days until n >= 8; then the 14-day
  median instead when it is lower and has n >= 5.
- factor(route, cell, t): 1.0 for NORMAL. Season cell: median(route prices
  departing in that cell, 400 days) / median(route NORMAL prices, 400 days).
    hard: route factor with n >= 3, shrunk toward the region pool (K = 4,
          pool median if n >= 6, else pool p25) and only DOWNWARD
          (min(f_route, shrunk)); no route factor -> pool p25 (Christmas cells
          only, needs >= 5 routes, tag "pooled", 25% minimum) -> else none.
    soft: 1.0 unless the route has n >= 6 (Latin America summer n >= 10);
          then the same one-sided shrink, capped at 1.5.
- D3: median of the same route, departures within +-3 days, scanned before t,
  excluding the same itinerary (departure + trip length) scanned in the last
  10 days; n >= 4 -> B = D3 bounded to [0.75, 1.30] x (base x factor);
  with no base x factor, D3 alone needs n >= 6.
- Deal rule: saving >= 15% (25% when the factor is pooled or a soft cell has
  no D3; 20% for Asia), >= $30 below B, <= 70%. Saving uses the unrounded ratio.

Other checks in the production scraper (Google's price-level flag, a guard
against prices far outside Google's typical range, and a return-leg check) are
not part of this module.

Only rotation rows feed the engine: fares from a separate holiday-probe lane
never feed the base, the factors or D3.
"""

import datetime
import statistics

CALENDAR_VERSION = "cal-20261111"

# ── Parameters ────────────────────────────────────────────────────────────────
BASE_LADDER = (30, 60, 90)
BASE_N_MIN = 8
BASE14_DAYS = 14
BASE14_N_MIN = 5
FACTOR_LOOKBACK = 400
N_ROUTE_MIN = 3          # hard-cell route factor
N_ROUTE_FULL = 6         # shrink target = pool median from here (else pool p25)
N_SOFT = 6               # soft-cell route factor needs this many
N_SOFT_SUMMER = 10       # LatAm summer
SOFT_CAP = 1.5
K_SHRINK = 4
POOL_MIN_ROUTES = 5
D3_DAYS = 3
D3_N = 4
D3_N_ALONE = 6
D3_SAME_ITIN_DAYS = 10
D3_LO, D3_HI = 0.75, 1.30
S_MIN = 0.15
S_MIN_POOLED = 0.25
S_MIN_SOFT_NO_D3 = 0.25
S_MIN_ASIA = 0.20
S_MAX = 0.70
ABS_MIN = 30

CATEGORY_REGION = {"latam": "latam", "us_domestic": "us", "eu": "eu", "asia": "asia"}

# Cells where the pooled p25 fallback is allowed (Christmas only).
POOLED_FALLBACK_CELLS = {"XMAS_EDGE_PRE", "XMAS_CORE", "XMAS_EDGE_POST",
                         "XMAS_US", "XMAS_EU", "XMAS_ASIA"}

CALENDAR_HEADERS = ["season_id", "region", "kind", "start", "end", "label_es", "version"]


# ── Dates ─────────────────────────────────────────────────────────────────────

def parse_date(s):
    """'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS' or 'M/D/YYYY' -> date; else None."""
    if isinstance(s, datetime.datetime):
        return s.date()
    if isinstance(s, datetime.date):
        return s
    s = str(s or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        return None


def easter(year):
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


def thanksgiving(year):
    """US Thanksgiving: 4th Thursday of November."""
    d = datetime.date(year, 11, 1)
    first_thu = d + datetime.timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + datetime.timedelta(weeks=3)


def presidents_day(year):
    """US Presidents' Day: 3rd Monday of February."""
    d = datetime.date(year, 2, 1)
    first_mon = d + datetime.timedelta(days=(0 - d.weekday()) % 7)
    return first_mon + datetime.timedelta(weeks=2)


# ── Season calendar ───────────────────────────────────────────────────────────

def _D(y, m, d):
    return datetime.date(y, m, d)


def _td(n):
    return datetime.timedelta(days=n)


def default_calendar(years):
    """Calendar rows (dicts with CALENDAR_HEADERS) for each year in `years`.
    A Christmas cell of year y runs into January of y+1. Order matters inside
    a region: hard cells first; among soft cells the first match wins."""
    rows = []

    def add(sid, region, kind, start, end, label):
        rows.append({"season_id": sid, "region": region, "kind": kind,
                     "start": start.isoformat(), "end": end.isoformat(),
                     "label_es": label, "version": CALENDAR_VERSION})

    for y in years:
        e = easter(y)
        ash = e - _td(46)
        tg = thanksgiving(y)
        pres = presidents_day(y)
        # Latin America (the 38 alerted routes)
        add("XMAS_EDGE_PRE", "latam", "hard", _D(y, 12, 11), _D(y, 12, 16), "Navidad")
        add("XMAS_CORE", "latam", "hard", _D(y, 12, 17), _D(y, 12, 29), "Navidad")
        add("XMAS_EDGE_POST", "latam", "hard", _D(y, 12, 30), _D(y + 1, 1, 4), "Navidad")
        add("THANKSGIVING", "latam", "soft", tg - _td(5), tg + _td(3), "Thanksgiving")
        add("CARNIVAL", "latam", "soft", ash - _td(12), ash + _td(1), "Carnaval")
        add("PRESIDENTS", "latam", "soft", pres - _td(3), pres + _td(1), "Presidents' Day")
        add("EASTER", "latam", "soft", e - _td(9), e + _td(5), "Semana Santa")
        add("SUMMER", "latam", "soft", _D(y, 6, 27), _D(y, 8, 9), "verano")
        # US (tracked, not alerted)
        add("XMAS_US", "us", "hard", _D(y, 12, 18), _D(y + 1, 1, 3), "Navidad")
        add("THANKSGIVING_US", "us", "soft", tg - _td(10), tg + _td(6), "Thanksgiving")
        add("PRESIDENTS", "us", "soft", pres - _td(3), pres + _td(1), "Presidents' Day")
        add("SPRING_US", "us", "soft", _D(y, 3, 6), _D(y, 4, 18), "spring break")
        # EU (tracked, not alerted)
        add("XMAS_EU", "eu", "hard", _D(y, 12, 12), _D(y + 1, 1, 3), "Navidad")
        add("SUMMER_EU", "eu", "hard", _D(y, 7, 1), _D(y, 8, 15), "verano")
        # Asia (tracked, not alerted)
        add("XMAS_ASIA", "asia", "hard", _D(y, 12, 5), _D(y + 1, 1, 10), "Navidad")
    return rows


class SeasonCalendar:
    """Reads calendar rows (from a stored calendar or default_calendar).
    Raises ValueError on a malformed row: the caller treats that as no calendar
    (fail-closed)."""

    def __init__(self, rows):
        self.cells = {}          # region -> list of (sid, kind, start, end, label)
        self.version = ""
        for r in rows:
            sid = str(r.get("season_id", "")).strip()
            region = str(r.get("region", "")).strip()
            kind = str(r.get("kind", "")).strip()
            start, end = parse_date(r.get("start")), parse_date(r.get("end"))
            if not sid or region not in CATEGORY_REGION.values() or kind not in ("hard", "soft") \
                    or start is None or end is None or end < start:
                raise ValueError(f"bad season calendar row: {r}")
            self.cells.setdefault(region, []).append(
                (sid, kind, start, end, str(r.get("label_es", "")).strip()))
            self.version = str(r.get("version", "")).strip() or self.version
        if not self.cells:
            raise ValueError("empty season calendar")
        for region in self.cells:   # hard before soft, stable inside each kind
            self.cells[region].sort(key=lambda c: 0 if c[1] == "hard" else 1)
        self._cache = {}

    def cell(self, d, region):
        """-> (season_id, kind, label_es); ('NORMAL', 'normal', '') outside any cell."""
        if d is None:
            return ("NORMAL", "normal", "")
        key = (d, region)
        hit = self._cache.get(key)
        if hit is None:
            hit = ("NORMAL", "normal", "")
            for sid, kind, start, end, label in self.cells.get(region, ()):
                if start <= d <= end:
                    hit = (sid, kind, label)
                    break
            self._cache[key] = hit
        return hit

    def covers_year(self, year):
        """True if every region has its Christmas cell for `year` (the yearly
        check: on Oct 1 next year's calendar must already be there)."""
        for region in ("latam", "us", "eu", "asia"):
            if not any(k == "hard" and s.year == year and s.month == 12
                       for _sid, k, s, _e, _l in self.cells.get(region, ())):
                return False
        return True


# ── History index ─────────────────────────────────────────────────────────────

def _trip_days(window_type, trip_type=""):
    wt = str(window_type or "")
    if "_" in wt:
        try:
            return int(wt.rsplit("_", 1)[-1])
        except ValueError:
            pass
    tt = str(trip_type or "")
    try:
        return int(tt.split("-", 1)[0])
    except ValueError:
        return None


def _median(xs):
    return statistics.median(xs) if xs else None


def _p25(xs):
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = 0.25 * (len(xs) - 1)
    lo = int(pos)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (pos - lo)


class History:
    """Rotation rows of the scraped history, indexed by route (destination IATA).
    Rows without a departure date or a numeric price are skipped (they can't
    be given a cell). Rows of other lanes (Lane not empty and not 'rot') are
    skipped too: only rotation feeds the engine."""

    def __init__(self, records, calendar):
        self.cal = calendar
        self.by_route = {}
        self.region_of = {}
        self.skipped = 0
        for r in records:
            lane = str(r.get("Lane", "") or "").strip().lower()
            if lane not in ("", "rot", "rotation"):
                self.skipped += 1
                continue
            route = str(r.get("Route", "")).strip().upper()
            dest = route.split("-")[-1] if route else ""
            region = CATEGORY_REGION.get(str(r.get("Category", "")).strip().lower())
            scan = parse_date(r.get("Timestamp"))
            dep = parse_date(r.get("Outbound_Date"))
            trip = _trip_days(r.get("Window_Type"), r.get("Trip Type"))
            try:
                price = float(r.get("Price ($)"))
            except (TypeError, ValueError):
                price = None
            if not dest or region is None or scan is None or dep is None or trip is None \
                    or not price or price <= 0:
                self.skipped += 1
                continue
            ret = dep + _td(trip)
            dep_cell = calendar.cell(dep, region)
            ret_cell = calendar.cell(ret, region)
            normal = dep_cell[0] == "NORMAL" and ret_cell[1] != "hard"
            self.region_of[dest] = region
            self.by_route.setdefault(dest, []).append(
                (scan, dep, trip, price, dep_cell[0], normal))
        self._factor_cache = {}

    # base -----------------------------------------------------------------
    def base(self, dest, t):
        rows = self.by_route.get(dest, ())
        for w in BASE_LADDER:
            lo = t - _td(w)
            ps = [p for (s, _d, _tr, p, _c, nrm) in rows if nrm and lo <= s < t]
            if len(ps) >= BASE_N_MIN:
                b = _median(ps)
                lo14 = t - _td(BASE14_DAYS)
                ps14 = [p for (s, _d, _tr, p, _c, nrm) in rows if nrm and lo14 <= s < t]
                b14 = _median(ps14) if len(ps14) >= BASE14_N_MIN else None
                if b14 is not None and b14 < b:
                    return b14, len(ps14), BASE14_DAYS
                return b, len(ps), w
        return None, 0, None

    # factor ---------------------------------------------------------------
    def _route_factor(self, dest, cell_id, t):
        key = ("rf", dest, cell_id, t)
        if key in self._factor_cache:
            return self._factor_cache[key]
        lo = t - _td(FACTOR_LOOKBACK)
        rows = self.by_route.get(dest, ())
        ps_s = [p for (s, _d, _tr, p, c, _n) in rows if c == cell_id and lo <= s < t]
        ps_n = [p for (s, _d, _tr, p, _c, nrm) in rows if nrm and lo <= s < t]
        if len(ps_s) < N_ROUTE_MIN or len(ps_n) < BASE_N_MIN:
            out = (None, len(ps_s))
        else:
            out = (_median(ps_s) / _median(ps_n), len(ps_s))
        self._factor_cache[key] = out
        return out

    def _pool(self, region, cell_id, t):
        key = ("pool", region, cell_id, t)
        if key in self._factor_cache:
            return self._factor_cache[key]
        fs = []
        for dest, reg in self.region_of.items():
            if reg != region:
                continue
            f, n = self._route_factor(dest, cell_id, t)
            if f is not None and n >= N_ROUTE_MIN:
                fs.append(f)
        out = (_median(fs), _p25(fs), len(fs)) if len(fs) >= POOL_MIN_ROUTES else (None, None, len(fs))
        self._factor_cache[key] = out
        return out

    def factor(self, dest, cell_id, kind, t):
        """-> (factor or None, tag, n_season, pool_med, pool_low)."""
        if cell_id == "NORMAL":
            return 1.0, "normal", None, None, None
        region = self.region_of.get(dest)
        f_r, n = self._route_factor(dest, cell_id, t)
        pool_med, pool_low, _np = self._pool(region, cell_id, t)

        def shrink(f):
            target = pool_med if n >= N_ROUTE_FULL else pool_low
            if target is None:
                return f
            return min(f, (n * f + K_SHRINK * target) / (n + K_SHRINK))

        if kind == "hard":
            if f_r is not None:
                return shrink(f_r), "route", n, pool_med, pool_low
            if cell_id in POOLED_FALLBACK_CELLS and pool_low is not None:
                return pool_low, "pooled", n, pool_med, pool_low
            return None, "none", n, pool_med, pool_low
        # soft
        need = N_SOFT_SUMMER if cell_id == "SUMMER" else N_SOFT
        if f_r is not None and n >= need:
            return min(shrink(f_r), SOFT_CAP), "route", n, pool_med, pool_low
        return 1.0, "soft_default", n, pool_med, pool_low

    # D3 -------------------------------------------------------------------
    def d3(self, dest, dep, trip, t):
        rows = self.by_route.get(dest, ())
        recent = t - _td(D3_SAME_ITIN_DAYS)
        ps = [p for (s, d, tr, p, _c, _n) in rows
              if s < t and abs((d - dep).days) <= D3_DAYS
              and not (d == dep and tr == trip and s >= recent)]
        return (_median(ps), len(ps)) if ps else (None, 0)


def region_for(category):
    return CATEGORY_REGION.get(str(category or "").strip().lower())


def benchmark(hist, dest, dep, trip, t, price=None):
    """The v2 benchmark for one fare. Never raises.
    dest: IATA ('MEX'), dep: date or str, trip: trip days, t: scan date (today).
    Returns a dict; B is None when there is no reference (no % may be claimed)."""
    out = {"B": None, "cell": "", "kind": "", "label": "", "rung": "none", "tag": "",
           "base": None, "base_n": 0, "base_window": None, "factor": None, "n_season": None,
           "d3": None, "d3_n": 0, "s_min": None, "savings": None, "deal": False,
           "reason": "", "region": ""}
    try:
        dep = parse_date(dep)
        t = parse_date(t)
        dest = str(dest or "").strip().upper().split("-")[-1]
        region = hist.region_of.get(dest)
        out["region"] = region or ""
        if dep is None or t is None or not trip:
            out["reason"] = "bad_input"
            return out
        if region is None:
            out["reason"] = "route_unknown"
            return out
        cell_id, kind, label = hist.cal.cell(dep, region)
        out.update(cell=cell_id, kind=kind, label=label)

        b, bn, bw = hist.base(dest, t)
        out.update(base=b, base_n=bn, base_window=bw)
        f, tag, n_s, _pm, _pl = hist.factor(dest, cell_id, kind, t)
        out.update(factor=f, tag=tag, n_season=n_s)
        b_cell = b * f if (b is not None and f is not None) else None

        d3, d3n = hist.d3(dest, dep, int(trip), t)
        out.update(d3=d3, d3_n=d3n)

        if d3 is not None and d3n >= D3_N and b_cell is not None:
            B = min(max(d3, D3_LO * b_cell), D3_HI * b_cell)
            rung = "D3"
        elif d3 is not None and d3n >= D3_N_ALONE and b_cell is None:
            B = d3
            rung = "D3_alone"
        elif b_cell is not None:
            B = b_cell
            rung = "cell"
        else:
            out["reason"] = "base_thin" if b is None else "season_unknown"
            return out
        out.update(B=B, rung=rung)

        s_min = S_MIN
        if region == "asia":
            s_min = S_MIN_ASIA
        if tag == "pooled":
            s_min = max(s_min, S_MIN_POOLED)
        if kind == "soft" and rung == "cell":
            s_min = max(s_min, S_MIN_SOFT_NO_D3)
        out["s_min"] = s_min

        if price is not None:
            price = float(price)
            s = 1 - price / B
            out["savings"] = s
            out["deal"] = bool(s_min <= s <= S_MAX and (B - price) >= ABS_MIN)
        return out
    except Exception as e:   # never raise into the daily job
        out["B"] = None
        out["deal"] = False
        out["reason"] = f"error:{type(e).__name__}:{str(e)[:60]}"
        return out
