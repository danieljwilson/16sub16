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
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import zipfile
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
PRELOAD_DAYS = 120                # warmup lookback to seed day-0 FIT/FAT

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

# Projection settings (future days > t_today)
# Phase-based projection: modest build, then sharpening, then taper.
PROJ_BUILD_END_FRACTION = 0.70       # first 70% of remaining days
PROJ_SHARPEN_END_FRACTION = 0.90     # next 20%; final 10% is taper
PROJ_BUILD_LOAD_FACTOR = 1.08
PROJ_SHARPEN_LOAD_FACTOR = 0.92
PROJ_TAPER_FINAL_FACTOR = 0.60

# Goal-conditioning prior (soft constraint, not a hard override).
# If you have a strong expectation for race-day performance, the fitter can
# penalize parameter sets that miss this target while still respecting race data.
GOAL_EXPECTED_MAX_S = 17 * 60 - 2   # "sub-17" expectation buffer
GOAL_EXPECTATION_WEIGHT = 60.0       # higher => stronger pull toward expectation

# TrainingPeaks import settings
TP_TSS_TO_TRIMP = 1.0
TP_DEFAULT_SPORT_FACTOR = 0.30
TP_SPORT_FACTORS = {
    "run": 1.00,
    "bike": 0.45,
    "ride": 0.45,
    "virtualride": 0.45,
    "swim": 0.25,
    "strength": 0.15,
    "other": 0.30,
}
TP_RUN_DUP_TOL_S = 180
TP_RUN_DUP_TOL_FRACTION = 0.20

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


def _to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _norm_sport(s):
    return re.sub(r"[^a-z]", "", (s or "").strip().lower())


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
    existing_ids = {a["strava_id"] for a in log["activities"] if "strava_id" in a}
    # If TP runs were imported first, avoid double-counting overlapping Strava runs.
    non_strava_run_durations_by_day = {}
    for a in log["activities"]:
        source = a.get("source")
        if source is None and "strava_id" in a:
            source = "strava"
        sport_key = _norm_sport(a.get("sport_type") or ("Run" if source == "strava" else ""))
        if source != "strava" and sport_key == "run":
            d = int(a.get("day", 0))
            dur = int(a.get("duration_s", 0))
            if dur > 0:
                non_strava_run_durations_by_day.setdefault(d, []).append(dur)

    added = 0
    skipped_dupe = 0
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
        if campaign_day < -PRELOAD_DAYS or campaign_day > GOAL_DAY:
            continue
        duration_s = int(act.get("elapsed_time", 0) or 0)

        dup_hit = False
        for other_dur in non_strava_run_durations_by_day.get(campaign_day, []):
            tol = max(TP_RUN_DUP_TOL_S, int(TP_RUN_DUP_TOL_FRACTION * max(other_dur, duration_s)))
            if abs(other_dur - duration_s) <= tol:
                dup_hit = True
                break
        if dup_hit:
            skipped_dupe += 1
            continue

        avg_hr = act.get("average_heartrate") or None
        trimp = compute_trimp(duration_s, avg_hr)
        log["activities"].append({
            "source":       "strava",
            "strava_id":    sid,
            "name":         act.get("name", ""),
            "sport_type":   "Run",
            "day":          campaign_day,
            "duration_s":   duration_s,
            "avg_hr":       avg_hr,
            "trimp":        trimp,
        })
        if duration_s > 0:
            non_strava_run_durations_by_day.setdefault(campaign_day, []).append(duration_s)
        existing_ids.add(sid)
        added += 1
    # Sort by day ascending
    log["activities"].sort(key=lambda a: a["day"])
    return {"added": added, "skipped_dupe": skipped_dupe}


def merge_trainingpeaks_zip(log, zip_path, include_all_sports=False):
    """
    Merge TrainingPeaks WorkoutExport ZIP into the unified training log.
    - Reads workouts.csv inside ZIP
    - Converts TSS (or HR-derived TRIMP fallback) to load
    - Default behavior: imports Run only (to avoid sport-mixing bias)
    - Optional all-sports mode applies sport-weight factors
    - Keeps only days in [-PRELOAD_DAYS, GOAL_DAY]
    - Skips likely duplicate Strava runs on same day with similar duration
    """
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(f"TrainingPeaks ZIP not found: {zp}")

    with zipfile.ZipFile(zp, "r") as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError(f"No CSV found in ZIP: {zp}")
        target = None
        for n in names:
            if Path(n).name.lower() == "workouts.csv":
                target = n
                break
        if target is None:
            target = names[0]

        with zf.open(target) as raw_fp:
            text_fp = io.TextIOWrapper(raw_fp, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text_fp)
            rows = list(reader)

    existing_tp_keys = {
        a.get("tp_key")
        for a in log["activities"]
        if a.get("source") == "trainingpeaks" and a.get("tp_key")
    }
    strava_run_durations_by_day = {}
    for a in log["activities"]:
        source = a.get("source")
        if source is None and "strava_id" in a:
            source = "strava"
        sport = (a.get("sport_type") or "").lower()
        if source == "strava" and (sport in ("", "run")):
            d = int(a.get("day", 0))
            strava_run_durations_by_day.setdefault(d, []).append(int(a.get("duration_s", 0)))

    added = 0
    skipped_dupe = 0
    skipped_empty = 0
    skipped_non_run = 0

    for row in rows:
        day_date = _parse_date(row.get("WorkoutDay"))
        if day_date is None:
            continue
        campaign_day = (day_date - START_DATE).days
        if campaign_day < -PRELOAD_DAYS or campaign_day > GOAL_DAY:
            continue

        workout_type = (row.get("WorkoutType") or "Other").strip() or "Other"
        sport_key = _norm_sport(workout_type)
        if not include_all_sports and sport_key != "run":
            skipped_non_run += 1
            continue
        sport_factor = TP_SPORT_FACTORS.get(sport_key, TP_DEFAULT_SPORT_FACTOR)

        title = (row.get("Title") or row.get("WorkoutDescription") or workout_type).strip()

        duration_h = _to_float(row.get("TimeTotalInHours")) or 0.0
        duration_s = int(round(duration_h * 3600.0))
        avg_hr = _to_float(row.get("HeartRateAverage"))
        tss = _to_float(row.get("TSS"))

        raw_load = None
        if tss is not None and tss > 0:
            raw_load = tss * TP_TSS_TO_TRIMP
        elif duration_s > 0:
            raw_load = compute_trimp(duration_s, avg_hr)
        else:
            skipped_empty += 1
            continue

        if include_all_sports:
            trimp = round(float(raw_load) * sport_factor, 3)
        else:
            trimp = round(float(raw_load), 3)
        if trimp <= 0:
            skipped_empty += 1
            continue

        tp_key = (
            f"{day_date.isoformat()}|{workout_type}|{title}|{duration_s}|"
            f"{'' if tss is None else round(tss, 2)}"
        )
        if tp_key in existing_tp_keys:
            continue

        if sport_key == "run" and duration_s > 0:
            dup_hit = False
            for sd in strava_run_durations_by_day.get(campaign_day, []):
                tol = max(TP_RUN_DUP_TOL_S, int(TP_RUN_DUP_TOL_FRACTION * max(sd, duration_s)))
                if abs(sd - duration_s) <= tol:
                    dup_hit = True
                    break
            if dup_hit:
                skipped_dupe += 1
                continue

        log["activities"].append({
            "source": "trainingpeaks",
            "tp_key": tp_key,
            "name": title,
            "sport_type": workout_type,
            "day": campaign_day,
            "duration_s": duration_s,
            "avg_hr": avg_hr,
            "tss": tss,
            "trimp": trimp,
        })
        existing_tp_keys.add(tp_key)
        added += 1

    log["activities"].sort(key=lambda a: (a["day"], a.get("source", ""), a.get("name", "")))
    return {
        "added": added,
        "skipped_dupe": skipped_dupe,
        "skipped_empty": skipped_empty,
        "skipped_non_run": skipped_non_run,
    }


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
    For days t_today+1..GOAL_DAY: project using a simple periodized pattern:
    modest build, then sharpening, then taper into the goal race.
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

    days_to_goal = max(1, GOAL_DAY - t_today)

    # Warmup state from pre-campaign load so day-0 is not forced to zero state.
    min_day = min(trimp_by_day.keys(), default=0)
    if min_day < 0:
        warmup_start = max(min_day, -PRELOAD_DAYS)
        for day in range(warmup_start, 0):
            w = trimp_by_day.get(day, 0.0)
            fit = fit * kf + w * (1 - kf)
            fat = fat * kn + w * (1 - kn)
    pre_day0 = {"fit": round(fit, 4), "fat": round(fat, 4)}

    for day in range(GOAL_DAY + 1):
        if day <= t_today:
            w = trimp_by_day.get(day, 0.0)
        else:
            frac_to_goal = (day - t_today) / days_to_goal
            if frac_to_goal <= PROJ_BUILD_END_FRACTION:
                load_factor = PROJ_BUILD_LOAD_FACTOR
            elif frac_to_goal <= PROJ_SHARPEN_END_FRACTION:
                load_factor = PROJ_SHARPEN_LOAD_FACTOR
            else:
                # Taper linearly from sharpening load down to final taper load.
                taper_span = max(1e-9, 1.0 - PROJ_SHARPEN_END_FRACTION)
                taper_progress = (frac_to_goal - PROJ_SHARPEN_END_FRACTION) / taper_span
                load_factor = (
                    PROJ_SHARPEN_LOAD_FACTOR
                    + (PROJ_TAPER_FINAL_FACTOR - PROJ_SHARPEN_LOAD_FACTOR) * taper_progress
                )
            w = mean_trimp * max(0.0, load_factor)

        fit = fit * kf + w * (1 - kf)
        fat = fat * kn + w * (1 - kn)
        series.append({"day": day, "fit": round(fit, 4), "fat": round(fat, 4)})

    return series, mean_trimp, pre_day0


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


def fit_params(series, pre_day0=None):
    """
    Fit p₀, k₁, k₂ from race results using pre-race state and latest-race anchoring.

    Model: p(t) = p₀ − k₁·FIT(t) + k₂·FAT(t), evaluated on pre-race state.
    A race on day d is fit against state from day d-1 (or zero state for day 0).

    To improve practical calibration with sparse race anchors, solve for k₁/k₂ from
    race-to-race deltas and force exact agreement at the latest race (anchor):
      p(anchor) == latest race time
    Then recover p₀ analytically from the anchor equation.

    Returns dict {p0, k1, k2} or None if data insufficient.
    """
    by_day = {s["day"]: s for s in series}

    def prerace_state(day):
        if day <= 0:
            if pre_day0:
                return pre_day0
            return {"fit": 0.0, "fat": 0.0}
        s = by_day.get(day - 1)
        if s is None:
            return {"fit": 0.0, "fat": 0.0}
        return s

    samples = []
    for r in RACE_RESULTS:
        s = prerace_state(r["day"])
        samples.append({
            "day": r["day"],
            "time_s": float(r["time_s"]),
            "fit": float(s["fit"]),
            "fat": float(s["fat"]),
        })

    # Check we have enough fitness spread to identify a meaningful signal.
    fit_vals = [s["fit"] for s in samples]
    if max(fit_vals) < 0.1:
        return None  # No real training data yet

    # Anchor to the latest race so current known performance is matched exactly.
    anchor = max(samples, key=lambda s: s["day"])
    a_fit = anchor["fit"]
    a_fat = anchor["fat"]
    a_time = anchor["time_s"]

    # Constrained least squares over (k1, k2), with p0 recovered from anchor:
    # p0 = t_anchor + k1*FIT_anchor - k2*FAT_anchor
    # and prediction for any sample i:
    # p_i = t_anchor - k1*(fit_i - fit_anchor) + k2*(fat_i - fat_anchor)
    #
    # Constraint keeps taper behavior physiologically plausible.
    #
    # Add a soft goal-day prior so forecast aligns with stated expectation
    # without becoming a hard-coded result.
    goal_state = by_day.get(max(0, GOAL_DAY - 1))
    if goal_state is None:
        return None
    g_fit = float(goal_state["fit"])
    g_fat = float(goal_state["fat"])

    best = None
    step = 0.05
    k1 = 0.2
    while k1 <= 8.0 + 1e-9:
        k2_min = max(0.2, 0.8 * k1)
        k2 = k2_min
        while k2 <= 8.0 + 1e-9:
            err = 0.0
            for s in samples:
                pred = a_time - k1 * (s["fit"] - a_fit) + k2 * (s["fat"] - a_fat)
                t = s["time_s"]
                err += (pred - t) ** 2

            # Soft prior: prefer parameter sets that get to at least sub-17.
            p0 = a_time + k1 * a_fit - k2 * a_fat
            goal_pred = p0 - k1 * g_fit + k2 * g_fat
            if goal_pred > GOAL_EXPECTED_MAX_S:
                err += GOAL_EXPECTATION_WEIGHT * (goal_pred - GOAL_EXPECTED_MAX_S) ** 2

            if best is None or err < best["err"]:
                best = {"err": err, "p0": p0, "k1": k1, "k2": k2}
            k2 += step
        k1 += step

    if best is None:
        return None

    return {
        "p0": round(best["p0"], 4),
        "k1": round(best["k1"], 6),
        "k2": round(best["k2"], 6),
    }


# ─────────────────────────────────────────────────────────
# HTML INJECTION
# ─────────────────────────────────────────────────────────
def inject_into_html(series, params, t_today, pre_day0=None, dry_run=False):
    """
    Replace the content between // BANISTER_DATA_START and // BANISTER_DATA_END
    markers in 16sub16_tracker.html with updated JS constants.
    """
    if not HTML_PATH.exists():
        print(f"ERROR: HTML file not found at {HTML_PATH}", file=sys.stderr)
        sys.exit(1)

    html = HTML_PATH.read_text(encoding="utf-8")

    # Compact series: include pre-day0 state (-1) plus all modeled campaign days.
    compact = []
    if pre_day0 is not None:
        compact.append({
            "day": -1,
            "fit": round(float(pre_day0.get("fit", 0.0)), 4),
            "fat": round(float(pre_day0.get("fat", 0.0)), 4),
        })
    compact.extend(s for s in series if s["fit"] > 0.01)
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
    parser.add_argument(
        "--backfill-from",
        help="Override Strava fetch cursor for this run (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--import-trainingpeaks-zip",
        action="append",
        default=[],
        help="Path to TrainingPeaks WorkoutExport ZIP (repeat flag for multiple files).",
    )
    parser.add_argument(
        "--trainingpeaks-all-sports",
        action="store_true",
        help="Import all TP workout types with sport-weighted load (default: Run only).",
    )
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

    log_dirty = False

    if have_strava_env:
        print("Fetching Strava access token…")
        try:
            token = get_access_token()
        except Exception as e:
            print(f"ERROR getting Strava token: {e}", file=sys.stderr)
            sys.exit(1)

        if args.backfill_from:
            try:
                backfill_day = datetime.strptime(args.backfill_from, "%Y-%m-%d").date()
            except ValueError:
                print(
                    "ERROR: --backfill-from must be YYYY-MM-DD",
                    file=sys.stderr,
                )
                sys.exit(1)
            after_epoch = int(datetime.combine(backfill_day, datetime.min.time(), tzinfo=timezone.utc).timestamp())
            print(f"Backfill mode: forcing fetch cursor to {args.backfill_from} ({after_epoch})")
        else:
            after_epoch = log.get("last_fetch_epoch", START_EPOCH)
        print(f"Fetching activities after epoch {after_epoch}…")
        try:
            raw = fetch_activities(token, after_epoch)
        except Exception as e:
            print(f"ERROR fetching activities: {e}", file=sys.stderr)
            sys.exit(1)

        mstats = merge_activities(log, raw)
        print(
            f"  {len(raw)} activities fetched, {mstats['added']} new runs added "
            f"(skipped dup runs={mstats['skipped_dupe']})"
        )

        # Update last_fetch_epoch to now
        log["last_fetch_epoch"] = int(time.time())
        log_dirty = True
    else:
        print("Strava env vars not set — using existing training log")
        if args.backfill_from:
            print("WARNING: --backfill-from ignored (Strava env vars are missing)")

    # --- TrainingPeaks import (optional) ---
    if args.import_trainingpeaks_zip:
        for zp in args.import_trainingpeaks_zip:
            try:
                stats = merge_trainingpeaks_zip(
                    log,
                    zp,
                    include_all_sports=args.trainingpeaks_all_sports,
                )
            except Exception as e:
                print(f"ERROR importing TrainingPeaks ZIP {zp}: {e}", file=sys.stderr)
                sys.exit(1)
            print(
                f"TrainingPeaks import {zp}: +{stats['added']} activities "
                f"(skipped non-run={stats['skipped_non_run']}, "
                f"skipped dup runs={stats['skipped_dupe']}, "
                f"skipped empty={stats['skipped_empty']})"
            )
            if stats["added"] > 0:
                log_dirty = True

    if log_dirty and not args.dry_run:
        save_log(log)
        print(f"Training log saved: {LOG_PATH}")

    # --- Build Banister series ---
    activities = log.get("activities", [])
    total_trimp = sum(a["trimp"] for a in activities)
    print(f"Activities in log: {len(activities)}, total TRIMP: {total_trimp:.1f}")

    series, mean_trimp, pre_day0 = build_series(activities, t_today)
    print(f"14-day mean daily TRIMP (projection base load): {mean_trimp:.2f}")

    # --- Fit model ---
    params = fit_params(series, pre_day0=pre_day0)

    if params is None:
        print("Banister model not yet active (insufficient data or unphysical fit)")
        predicted_s = None
    else:
        print(f"Fit params: p0={params['p0']:.1f}, k1={params['k1']:.6f}, k2={params['k2']:.6f}")
        # Predict race on pre-race state (start of race day), not post-day load.
        goal_s = series[max(0, GOAL_DAY - 1)]
        predicted_s = params["p0"] - params["k1"] * goal_s["fit"] + params["k2"] * goal_s["fat"]
        print(f"Predicted Apr 19 finish (pre-race): {fmt_time(predicted_s)}  ({predicted_s:.0f}s)")

    # --- Inject HTML ---
    inject_into_html(series, params, t_today, pre_day0=pre_day0, dry_run=args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()
