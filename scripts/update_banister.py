#!/usr/bin/env python3
"""
update_banister.py — Strava → Banister fitness-fatigue pipeline.

Fetches recent runs from the Strava API, computes daily TRIMP, accumulates
FIT (τ=42d) and FAT (τ=7d) time series, fits p₀/k₁/k₂ from known race
times, then injects the result into 16sub16_tracker.html.

Usage:
    python scripts/update_banister.py          # full run
    python scripts/update_banister.py --dry-run  # no file writes
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, date
from pathlib import Path

# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "index.html"
LOG_PATH  = ROOT / "data" / "training_log.json"

# Campaign constants (mirror the HTML)
START_DATE  = date(2025, 12, 28)
GOAL_DAY    = 112                 # Apr 19, 2026
START_EPOCH = 1735344000          # 2025-12-28T00:00:00 UTC (approx)

# Race results for fitting p₀, k₁, k₂
# day = days since START_DATE; time_s = finish time in seconds
RACE_RESULTS = [
    {"day": 0,  "time_s": 1171, "name": "Marseille"},
    {"day": 7,  "time_s": 1088, "name": "Nice"},
    {"day": 49, "time_s": 1049, "name": "Monaco"},
]

# Athlete physiology
REST_HR = 45
MAX_HR  = 185

# Banister time constants (days)
TAU_FIT = 42.0
TAU_FAT = 7.0

# Strava OAuth
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"


# ─────────────────────────────────────────────────────────
# STRAVA API
# ─────────────────────────────────────────────────────────
def _http_post(url, data):
    """Simple urllib POST; returns parsed JSON or raises."""
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _http_get(url, token, params=None):
    """Simple urllib GET with Bearer token; returns parsed JSON or raises."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_access_token():
    """Exchange refresh token for a short-lived access token."""
    client_id     = os.environ["STRAVA_CLIENT_ID"]
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    refresh_token = os.environ["STRAVA_REFRESH_TOKEN"]
    resp = _http_post(STRAVA_TOKEN_URL, {
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    })
    return resp["access_token"]


def fetch_activities(token, after_epoch):
    """Fetch all activities after after_epoch (unix), paginated 100/page."""
    activities = []
    page = 1
    while True:
        page_data = _http_get(
            f"{STRAVA_API_BASE}/athlete/activities",
            token,
            params={"after": after_epoch, "per_page": 100, "page": page},
        )
        if not page_data:
            break
        activities.extend(page_data)
        if len(page_data) < 100:
            break
        page += 1
    return activities


# ─────────────────────────────────────────────────────────
# TRIMP
# ─────────────────────────────────────────────────────────
def compute_trimp(duration_s, avg_hr=None):
    """
    Morton 1990 TRIMP:
        TRIMP = duration_min × hr_ratio × exp(1.92 × hr_ratio)

    hr_ratio = (avg_hr − REST_HR) / (MAX_HR − REST_HR)

    If avg_hr is missing (no HR monitor), default to 65% HRR (easy aerobic).
    """
    hrr = MAX_HR - REST_HR  # heart rate reserve
    if avg_hr is None or avg_hr <= 0:
        hr_ratio = 0.65       # easy aerobic assumption
    else:
        hr_ratio = max(0.0, min(1.0, (avg_hr - REST_HR) / hrr))

    dur_min = duration_s / 60.0
    trimp = dur_min * hr_ratio * math.exp(1.92 * hr_ratio)
    return round(trimp, 3)


# ─────────────────────────────────────────────────────────
# TRAINING LOG
# ─────────────────────────────────────────────────────────
def load_log():
    """Load training_log.json; return default structure if missing."""
    if LOG_PATH.exists():
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"activities": [], "last_fetch_epoch": START_EPOCH}


def save_log(log):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def _epoch_to_campaign_day(epoch):
    """Convert Unix epoch to campaign day (0 = 2025-12-28)."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc).date()
    return (dt - START_DATE).days


def merge_activities(log, raw_activities):
    """
    Merge newly fetched raw Strava activities into the log.
    - Deduplicates by strava_id
    - Filters to Run types only
    - Only keeps days 0..GOAL_DAY
    """
    existing_ids = {a["strava_id"] for a in log["activities"]}
    added = 0
    for act in raw_activities:
        if act.get("type") != "Run" and act.get("sport_type") != "Run":
            continue
        sid = act["id"]
        if sid in existing_ids:
            continue
        start_epoch = int(
            datetime.strptime(act["start_date"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        campaign_day = _epoch_to_campaign_day(start_epoch)
        if campaign_day < 0 or campaign_day > GOAL_DAY:
            continue
        avg_hr = act.get("average_heartrate") or None
        trimp = compute_trimp(act.get("elapsed_time", 0), avg_hr)
        log["activities"].append({
            "strava_id":    sid,
            "name":         act.get("name", ""),
            "day":          campaign_day,
            "duration_s":   act.get("elapsed_time", 0),
            "avg_hr":       avg_hr,
            "trimp":        trimp,
        })
        existing_ids.add(sid)
        added += 1
    # Sort by day ascending
    log["activities"].sort(key=lambda a: a["day"])
    return added


# ─────────────────────────────────────────────────────────
# BANISTER MODEL
# ─────────────────────────────────────────────────────────
def _decay(tau):
    """Daily decay factor for a time constant τ (days)."""
    return math.exp(-1.0 / tau)


def build_series(activities, t_today):
    """
    Build FIT/FAT time series for days 0..GOAL_DAY.

    For days 0..t_today: use actual TRIMP (0 if no run that day).
    For days t_today+1..GOAL_DAY: project at mean daily TRIMP of last 14 days
    ("maintain current load" assumption).
    """
    # Aggregate TRIMP per campaign day
    trimp_by_day = {}
    for act in activities:
        d = act["day"]
        trimp_by_day[d] = trimp_by_day.get(d, 0.0) + act["trimp"]

    # Mean daily TRIMP for the last 14 days up to t_today
    recent_days = [d for d in range(max(0, t_today - 13), t_today + 1)]
    recent_trimp = [trimp_by_day.get(d, 0.0) for d in recent_days]
    mean_trimp = sum(recent_trimp) / len(recent_trimp) if recent_trimp else 0.0

    kf = _decay(TAU_FIT)
    kn = _decay(TAU_FAT)

    fit = 0.0
    fat = 0.0
    series = []

    for day in range(GOAL_DAY + 1):
        if day <= t_today:
            w = trimp_by_day.get(day, 0.0)
        else:
            w = mean_trimp

        fit = fit * kf + w * (1 - kf)
        fat = fat * kn + w * (1 - kn)
        series.append({"day": day, "fit": round(fit, 4), "fat": round(fat, 4)})

    return series, mean_trimp


def _gauss(A, b):
    """
    Solve A·x = b via Gaussian elimination with partial pivoting.
    A is n×n list of lists, b is list of n values.
    Returns x as list, or None if singular.
    """
    n = len(b)
    # Augmented matrix
    M = [A[i][:] + [b[i]] for i in range(n)]

    for col in range(n):
        # Partial pivot
        max_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[max_row] = M[max_row], M[col]
        if abs(M[col][col]) < 1e-12:
            return None
        # Eliminate below
        for row in range(col + 1, n):
            factor = M[row][col] / M[col][col]
            for j in range(col, n + 1):
                M[row][j] -= factor * M[col][j]

    # Back-substitution
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = M[i][n]
        for j in range(i + 1, n):
            x[i] -= M[i][j] * x[j]
        x[i] /= M[i][i]
    return x


def fit_params(series):
    """
    Fit p₀, k₁, k₂ from race results using OLS normal equations.

    Model: p(t) = p₀ + k₁·FIT(t) − k₂·FAT(t)
    Rewrite as: p(t) = c₀·1 + c₁·FIT(t) + c₂·(−FAT(t))

    Uses a 3×3 system (one equation per race).
    Returns dict {p0, k1, k2} or None if data insufficient / unphysical.
    """
    by_day = {s["day"]: s for s in series}

    # Check we have enough FIT variance
    fit_vals = [by_day.get(r["day"], {}).get("fit", 0.0) for r in RACE_RESULTS]
    if max(fit_vals) < 0.1:
        return None  # No real training data yet

    # Build 3×3 normal equations for exactly 3 race points
    # [1, FIT, -FAT] · [p0, k1, k2]^T = time_s
    rows = []
    rhs  = []
    for r in RACE_RESULTS:
        s = by_day.get(r["day"])
        if s is None:
            return None
        rows.append([1.0, s["fit"], -s["fat"]])
        rhs.append(float(r["time_s"]))

    # Normal equations: A^T A x = A^T b
    A = rows
    b = rhs
    # A^T A
    ATA = [[sum(A[k][i] * A[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    ATb = [sum(A[k][i] * b[k] for k in range(3)) for i in range(3)]

    sol = _gauss(ATA, ATb)
    if sol is None:
        return None

    p0, k1, k2 = sol
    # Sanity check: k1, k2 must be positive (fitness helps, fatigue hurts)
    if k1 <= 0 or k2 <= 0:
        return None

    return {"p0": round(p0, 4), "k1": round(k1, 6), "k2": round(k2, 6)}


# ─────────────────────────────────────────────────────────
# HTML INJECTION
# ─────────────────────────────────────────────────────────
def inject_into_html(series, params, t_today, dry_run=False):
    """
    Replace the content between // BANISTER_DATA_START and // BANISTER_DATA_END
    markers in 16sub16_tracker.html with updated JS constants.
    """
    if not HTML_PATH.exists():
        print(f"ERROR: HTML file not found at {HTML_PATH}", file=sys.stderr)
        sys.exit(1)

    html = HTML_PATH.read_text(encoding="utf-8")

    # Compact series: only entries where fit > 0.01
    compact = [s for s in series if s["fit"] > 0.01]
    series_json = json.dumps(compact, separators=(",", ":"))

    params_json = json.dumps(params) if params else "null"

    today_str = date.today().isoformat()

    new_block = (
        "// BANISTER_DATA_START\n"
        f"const BANISTER_SERIES={series_json};\n"
        f"const BANISTER_PARAMS={params_json};\n"
        f"const BANISTER_UPDATED={json.dumps(today_str)};\n"
        f"const BANISTER_UPDATED_DAY={t_today};\n"
        "// BANISTER_DATA_END"
    )

    pattern = re.compile(
        r"// BANISTER_DATA_START.*?// BANISTER_DATA_END",
        re.DOTALL,
    )
    if not pattern.search(html):
        print("ERROR: Banister markers not found in HTML", file=sys.stderr)
        sys.exit(1)

    new_html = pattern.sub(new_block, html)

    if dry_run:
        print("Would update HTML (dry run — no file written)")
        return

    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"HTML updated: {HTML_PATH}")


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────
def fmt_time(s):
    if s is None or s <= 0:
        return "--:--"
    m = int(abs(s) // 60)
    sec = round(abs(s) % 60)
    return f"{m}:{sec:02d}"


def main():
    parser = argparse.ArgumentParser(description="Update Banister model from Strava.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch data and compute, but do not write files")
    args = parser.parse_args()

    today = date.today()
    t_today = min(GOAL_DAY, max(0, (today - START_DATE).days))

    print(f"Campaign day: {t_today} / {GOAL_DAY}  ({today})")

    # --- Strava fetch (skip if env vars absent) ---
    log = load_log()
    have_strava_env = all(
        os.environ.get(v)
        for v in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN")
    )

    if have_strava_env:
        print("Fetching Strava access token…")
        try:
            token = get_access_token()
        except Exception as e:
            print(f"ERROR getting Strava token: {e}", file=sys.stderr)
            sys.exit(1)

        after_epoch = log.get("last_fetch_epoch", START_EPOCH)
        print(f"Fetching activities after epoch {after_epoch}…")
        try:
            raw = fetch_activities(token, after_epoch)
        except Exception as e:
            print(f"ERROR fetching activities: {e}", file=sys.stderr)
            sys.exit(1)

        added = merge_activities(log, raw)
        print(f"  {len(raw)} activities fetched, {added} new runs added")

        # Update last_fetch_epoch to now
        log["last_fetch_epoch"] = int(time.time())

        if not args.dry_run:
            save_log(log)
            print(f"Training log saved: {LOG_PATH}")
    else:
        print("Strava env vars not set — using existing training log")

    # --- Build Banister series ---
    activities = log.get("activities", [])
    total_trimp = sum(a["trimp"] for a in activities)
    print(f"Activities in log: {len(activities)}, total TRIMP: {total_trimp:.1f}")

    series, mean_trimp = build_series(activities, t_today)
    print(f"14-day mean daily TRIMP (projection load): {mean_trimp:.2f}")

    # --- Fit model ---
    params = fit_params(series)

    if params is None:
        print("Banister model not yet active (insufficient data or unphysical fit)")
        predicted_s = None
    else:
        print(f"Fit params: p0={params['p0']:.1f}, k1={params['k1']:.6f}, k2={params['k2']:.6f}")
        goal_s = series[-1]  # GOAL_DAY entry
        predicted_s = params["p0"] + params["k1"] * goal_s["fit"] - params["k2"] * goal_s["fat"]
        print(f"Predicted Apr 19 finish: {fmt_time(predicted_s)}  ({predicted_s:.0f}s)")

    # --- Inject HTML ---
    inject_into_html(series, params, t_today, dry_run=args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()
