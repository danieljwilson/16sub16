# 16sub16 Tracker — Reference Notes

*For Claude (or anyone picking this up mid-project). Written Feb 15, 2026 after Monaco race.*

---

## 1. Project Overview

**Goal:** Run a 5K in under 16:00 within 16 weeks of training.
**Start:** December 28, 2025 (Marseille baseline race, 19:31)
**Finish:** April 19, 2026 (Nice Semi 5K, goal race)
**Athlete background:** Serious cyclist (Transcontinental Race, multi-year training base). Transitioning to running. High aerobic capacity, low running-specific neuromuscular economy.

### Race Schedule

| Race | Date | Day | Result | Notes |
|------|------|-----|--------|-------|
| Marseille | Dec 28, 2025 | 0 | 19:31 (1171s) | Baseline |
| Nice | Jan 4, 2026 | 7 | 18:08 (1088s) | Big early jump |
| Monaco | Feb 15, 2026 | 49 | 17:29 (1049s) | Current best |
| Cagnes sur Mer | Mar 1, 2026 | 63 | — | Upcoming |
| La Calvaire Antibois | Mar 29, 2026 | 91 | — | Upcoming |
| Nice Semi (goal) | Apr 19, 2026 | 112 | — | **Must be <16:00** |

**To add a new race result:** In `16sub16_tracker.html`, find the `races` array near the top of the `<script>` block and add a new entry:

```js
const races = [
  { name:'Marseille', date:'2025-12-28', day:0,  s:1171, note:'Baseline' },
  { name:'Nice',      date:'2026-01-04', day:7,  s:1088, note:'Big early jump' },
  { name:'Monaco',    date:'2026-02-15', day:49, s:1049, note:'Steady progress' },
  // ADD NEW RACE HERE:
  { name:'Cagnes sur Mer', date:'2026-03-01', day:63, s:XXXX, note:'your note' },
];
```

Replace `XXXX` with the race time converted to **total seconds** (e.g. 17:05 = 17×60+5 = 1025).
All model fits, predictions, and probabilities recalculate automatically on page reload.

---

## 2. The Tracker File

**File:** `16sub16_tracker.html`
**Dependencies:** Single file, no local server needed. Uses Plotly.js from CDN (requires internet). Open directly in any browser.

**Auto-updates on open:**
- Current day (`TODAY_DAY`) is computed from `new Date()` vs the Dec 28 start date — the "today" marker and week counter update automatically every time you open the file.
- Week progress dots reflect current week without manual editing.

**What you need to manually update:**
- Race results (add to `races` array, see above)
- Training log data if you add it (see Section 5)

---

## 3. The Five Models

### 3.1 Linear Model

**What it is:** Ordinary least-squares regression line through all completed race times.

**Formula:** `time(t) = a + b·t`
where `t` = days since Dec 28, `b` is the improvement rate in seconds/day.

**Current fit (3 races):**
- Slope `b` ≈ −1.95 s/day
- Predicts **15:21** at Day 112

**Why it's optimistic:** The Marseille→Nice jump (83 seconds in 7 days) was unusually large — likely a combination of running neuromuscular adaptation, race conditions, and carbon shoe benefit all hitting at once. The linear model weights this equally with slower subsequent gains, inflating the projected slope.

**Data needed:** Race times only.

**Limitation:** Assumes improvement continues at a constant rate forever. No physiological ceiling. Will always predict eventually hitting 0:00 if extrapolated far enough.

---

### 3.2 Log-Linear Model

**What it is:** Fits a logarithmic curve to the race data. Fast early gains that slow predictably over time.

**Formula:** `time(t) = a + b·ln(t+1)`
where `b` is negative (performance improves, i.e. time decreases).

**Current fit:** Predicts **~16:57** at Day 112.

**Why this is often the most realistic model for training:** The log curve captures a well-documented pattern in exercise science — "beginner gains" are large because you're adapting from a low baseline, and the returns diminish as you approach your near-term ceiling. For a cyclist with high aerobic fitness transitioning to running:
- Weeks 1–4: Fast improvement from neuromuscular adaptation (running economy, stride mechanics)
- Weeks 5–12: Slower but real aerobic gains (capillary density, mitochondrial density in running-specific muscles)
- Weeks 12–16: Fine-tuning, very modest absolute gains

**Data needed:** Race times only.

**Limitation:** The shape of the curve (how fast it flattens) is determined by the data. With only 3 points, the curve is still uncertain.

---

### 3.3 Exponential Saturation (Simplified Banister)

**What it is:** Models performance decaying exponentially toward a physiological asymptote.

**Formula:** `time(t) = B + A·e^(−k·t)`
- `A` = size of the initial improvement "burst"
- `k` = decay rate (how fast the burst is used up)
- `B` = plateau level (the asymptote)

**Current fit:**
Fitted by grid search to the 3 race points. Finds that the data is well-described by a model that has mostly "used up" its initial burst — predicts **~17:29** at Day 112 (essentially a plateau near current performance).

**The full Banister Impulse-Response Model:**
The actual Banister (1975) model separates *fitness* from *fatigue* using training load:

```
performance(t) = p0 + k1·Σ[w(i)·e^(-(t-i)/τ1)] - k2·Σ[w(i)·e^(-(t-i)/τ2)]
```

- `w(i)` = training load on day `i` (TRIMP score — see Section 5)
- `τ1` ≈ 45 days = fitness time constant (fitness fades slowly)
- `τ2` ≈ 15 days = fatigue time constant (fatigue fades faster)
- `k1`, `k2` = sensitivity constants

This model explains why you feel terrible mid-week (fatigue high) but race well on Sunday (fatigue dissipated, fitness intact). The model's current simplified version treats each race as a point observation and fits an exponential trend — it can't do the fitness/fatigue separation without daily load data.

**What it predicts now:** Near-plateau. This is the most pessimistic of the models, but that pessimism is meaningful: it says the initial gains from transitioning to running are mostly spent, and further improvement requires continued structured training stimulus (which the model can't see without load data).

**Data needed to upgrade:**
Daily training sessions with duration + average HR (or session type). See Section 5.

---

### 3.4 Bayesian Update Model

**What it is:** Starts from a *prior belief* about improvement rates based on exercise science literature, then updates that belief with each race observation using Bayes' theorem.

**Prior:** A trained endurance athlete (like a strong cyclist) transitioning to running typically improves 5K performance at roughly **−1.5 seconds/day** on average over 16 weeks (this reflects the aggregate of multiple individual case studies and research on cross-training populations).

**Update rule (Gaussian conjugate prior):**
```
posterior_mean = (prior_mean/σ²_prior + data_mean/σ²_data) / (1/σ²_prior + 1/σ²_data)
posterior_σ² = 1 / (1/σ²_prior + 1/σ²_data)
```

After 3 races, posterior slope ≈ **−1.70 s/day** (between the prior −1.5 and the data's −1.95).

**Prediction at Day 112:** **~16:20** (anchored to Marseille baseline of 1171s).

**Uncertainty bands:**
The ±1σ and ±2σ shaded regions show forecast uncertainty. The bands grow wider further into the future because:
1. Uncertainty in the slope estimate propagates as `σ_slope × (forecast_horizon)²`
2. Race-to-race variability (course, weather, fitness on the day) adds baseline noise

At the goal race (Apr 19), ±1σ ≈ ±75 seconds. This is honest — with only 3 data points, the future is genuinely uncertain. The bands should narrow significantly after Cagnes sur Mer and La Calvaire results are added.

**Data needed to improve:** Every additional race result tightens the posterior. HRV data or weekly mileage could inform a more structured prior.

---

### 3.5 Conservative Model (Recent Trend)

**What it is:** Linear regression through the **two most recent races only** (Nice and Monaco). Removes the early "pop" from Marseille→Nice.

**Current fit:** Slope ≈ **−0.93 s/day** (the most recent actual improvement rate).
**Prediction at Day 112:** **~16:30**

**Why it matters:** This is the "what if the rate I'm currently improving continues exactly?" model. It's valuable because:
- It strips out the one-time adaptation effects from the first week
- It's most sensitive to recent trajectory — if you run a good Cagnes race, this line drops sharply
- It's a useful floor for the optimistic models: if your actual improvement rate is only −0.93 s/day, you end up at 16:30

**Data needed:** Race times only.

---

## 4. Goal Probability

**How it's calculated:**
Each model makes a point prediction for Day 112. We then ask: given prediction `μ` and forecast uncertainty `σ ≈ 75s`, what is the probability the actual time falls below 960 seconds?

```
P(goal) = P(time < 960) = Φ((960 - μ) / σ)
```

where `Φ` is the standard normal CDF.

**Current probabilities (after Monaco):**

| Model | Prediction | P(sub-16) |
|-------|-----------|-----------|
| Linear | 15:21 | ~70% |
| Log-Linear | 16:57 | ~22% |
| Exp. Saturation | 17:29 | ~12% |
| Bayesian | 16:20 | ~39% |
| Conservative | 16:30 | ~34% |
| **Ensemble** | **~16:28** | **~35%** |

**Interpretation:** The honest read is roughly a 1-in-3 chance based on current trajectory. The goal is achievable but requires improvement to continue at or above the current rate. The next race (Cagnes, Day 63) is the most important update — if it's around 16:50 or better, the Bayesian and Conservative models move into striking distance.

**What a Cagnes result of 16:45 (1005s) would do:**
The Conservative model slope would update to ~−1.1 s/day, predicting 16:03 — essentially on the goal line. The Bayesian posterior would tighten and shift downward.

---

## 5. Adding Training Data (Daily Load)

### What data is needed and why

The full Banister model (and a meaningful Critical Speed model) need:

| Field | Why |
|-------|-----|
| Date | Link to training day |
| Duration (minutes) | Volume metric |
| Avg HR (bpm) | Intensity metric |
| Session type | Easy / Tempo / Interval / Race |
| Optional: RPE (1–10) | Subjective intensity check |

From duration + HR, we compute **TRIMP** (Training Impulse):
```
TRIMP = duration_min × ΔHR_ratio × e^(1.92 × ΔHR_ratio)
where ΔHR_ratio = (avg_HR - HR_rest) / (HR_max - HR_rest)
```

Typical values: easy run ~30–60 TRIMP, tempo ~80–120, hard interval ~150+.

---

### Option A: Strava API (most automated)

If you use Strava, this is the best path. A simple Python script can pull all activities automatically.

**Setup (one time):**
1. Go to [strava.com/settings/api](https://www.strava.com/settings/api) and create an app (free).
2. Get your `client_id`, `client_secret`, and `refresh_token`.
3. Save them in a `.env` file in the project directory.

**Script to create** (`sync_strava.py`):

```python
import requests, json, os, csv
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
CLIENT_ID     = os.getenv('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = os.getenv('STRAVA_REFRESH_TOKEN')

def get_access_token():
    r = requests.post('https://www.strava.com/oauth/token', data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
    })
    return r.json()['access_token']

def fetch_activities(token, after='2025-12-28'):
    after_ts = int(datetime.strptime(after, '%Y-%m-%d').timestamp())
    r = requests.get('https://www.strava.com/api/v3/athlete/activities',
        headers={'Authorization': f'Bearer {token}'},
        params={'after': after_ts, 'per_page': 200}
    )
    return r.json()

HR_REST = 40    # update with your resting HR
HR_MAX  = 190   # update with your max HR

def compute_trimp(duration_s, avg_hr):
    if not avg_hr or avg_hr <= HR_REST: return None
    dur_min = duration_s / 60
    delta_hr = (avg_hr - HR_REST) / (HR_MAX - HR_REST)
    import math
    return round(dur_min * delta_hr * math.exp(1.92 * delta_hr), 1)

token = get_access_token()
activities = fetch_activities(token)

rows = []
for a in activities:
    if a['type'] not in ('Run', 'VirtualRun'): continue
    date = a['start_date_local'][:10]
    dur  = a.get('moving_time', 0)
    hr   = a.get('average_heartrate')
    name = a.get('name', '')
    trimp = compute_trimp(dur, hr)
    rows.append({'date': date, 'duration_min': round(dur/60,1),
                 'avg_hr': hr, 'TRIMP': trimp, 'name': name})

with open('training_log.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['date','duration_min','avg_hr','TRIMP','name'])
    w.writeheader(); w.writerows(rows)

print(f"Saved {len(rows)} runs to training_log.csv")
```

**Run it whenever you want to update:**
```bash
python sync_strava.py
```

Then reload `16sub16_tracker.html` — it will need to be updated to read the CSV (see below).

---

### Option B: Garmin Connect (export GPX/FIT)

Garmin doesn't have a free public API, but you can export data manually:

1. Go to Garmin Connect → Activities → Export CSV (for a date range).
2. Or use the community tool **garmin-connect-export** (Python, GitHub) to batch-download all activities.
3. The exported CSV includes duration, avg HR, distance — enough to compute TRIMP.

---

### Option C: Manual CSV (simplest, no automation)

Create `training_log.csv` in the same project folder:

```csv
date,duration_min,avg_hr,session_type,notes
2026-01-05,40,135,easy,recovery run
2026-01-07,35,155,tempo,felt good
2026-01-09,45,165,interval,6x1km reps
2026-01-11,60,130,long,easy long run
```

Then add a small section to the HTML that reads this (or paste the data directly into a `training` array in the JS, same format as `races`).

---

### Reading training_log.csv in the HTML

Because browsers block local file reads for security, the cleanest options are:

1. **Paste data directly into the HTML** as a JS array — simplest, zero infrastructure:
   ```js
   const training = [
     { date:'2026-01-05', day:8,  dur:40, hr:135, trimp:62, type:'easy' },
     { date:'2026-01-07', day:10, dur:35, hr:155, trimp:95, type:'tempo' },
     // ...
   ];
   ```

2. **Run a local server** (one command):
   ```bash
   # From the project directory:
   python3 -m http.server 8080
   # Then open: http://localhost:8080/16sub16_tracker.html
   ```
   This allows `fetch('training_log.csv')` to work in the browser.

3. **Use GitHub Pages** — push the project to a GitHub repo, enable Pages. The HTML + CSV are served statically and you can update the CSV by committing a new version.

---

## 6. Critical Speed Model

### What it is

Critical Speed (CS) is the highest speed you can sustain for a very long time without progressively accumulating fatigue. Physiologically it corresponds closely to the **maximal lactate steady state** — the pace just below where lactate starts to accumulate uncontrollably.

The model comes from the **hyperbolic speed-duration relationship**:

```
d = CS × t + D'
```

Or equivalently:
```
speed = CS + D'/t
```

- `d` = distance run
- `t` = time taken
- `CS` = critical speed (m/s)
- `D'` = "W-prime" analog — a finite anaerobic capacity (meters of "reserve" above CS)

**Why it matters for 16sub16:**

For a 5K in 16:00, you need to average 5:12/km = 3:12/mile = 5.21 m/s. Whether that's above or below your CS determines how achievable it is. If your CS is already above 5.21 m/s, the 5K is aerobically limited and you just need to run well. If CS is below 5.21, you'll accumulate fatigue in the race and need to pace carefully.

---

### Can you estimate CS without dedicated time trials?

**Yes — three ways, without adding any races:**

#### Method 1: Use your existing 5K races as "best effort" data points in disguise

CS is typically ~92–97% of your 5K pace for a trained runner. The rule of thumb:

```
CS ≈ 5K_pace × 0.95
```

With your current Monaco time of 17:29, your 5K pace is 3:30/km = 4.76 m/s, so estimated CS ≈ 4.52 m/s ≈ 3:42/km.

This is a rough estimate but useful for knowing if 16:00 pace (3:12/km = 5.21 m/s) is above or below CS — it almost certainly is (you'd be running 15% above CS in the race, which is normal for a 5K, a ~16-min effort).

#### Method 2: Use Strava/Garmin "best effort" segments from training

Strava automatically records your best times at 400m, 1K, 1 mile, 5K, etc. from **training runs**, not just races. If you have a training run where you pushed a segment hard (even accidentally), Strava will log it.

CS can be estimated from any two different duration/distance bests:

```
CS = (d2 - d1) / (t2 - t1)
D' = (d1 × t2 - d2 × t1) / (t2 - t1)
```

For example, if Strava shows your best 1K from training = 3:45 and best 3K from training = 12:30:
```
CS = (3000 - 1000) / (750 - 225) = 2000/525 = 3.81 m/s = 4:22/km
D' = (1000×750 - 3000×225) / 525 = (750000 - 675000)/525 = 143m
```

**This is the best path without extra races.** Check your Strava profile → "My Performance" or look at individual run segment data.

#### Method 3: Use the Nice Semi result (if it's a different distance)

If "Nice Semi" is a **half marathon** (21.1km), your result there combined with your best 5K gives two very different time/distance points — ideal for fitting the CS model. This would give a highly accurate CS estimate.

Ask: is the April 19 goal race a 5K, or is the goal embedded in a half marathon event?

---

### Updating the CS model going forward

Each time you add a new effort at a **different distance**, update the CS calculation. Best sources:
1. Strava "best efforts" tab on your profile page
2. Any race at a non-5K distance (3K, 10K, half marathon)
3. A single 6-minute max effort on a track (gives ~1500m distance) paired with any 5K

The model can be added to the HTML once you have two data points of different distances. It predicts what 5K time is *physiologically achievable* given your current CS and D', independently of the training trend models.

---

## 7. What to Watch For

### After each race, ask:

1. **Did I improve?** If yes, by how much vs. what the models expected?
2. **Which model was closest?** Over time the model that tracks best is probably the most useful predictor.
3. **Is the Bayesian band narrowing?** After 5 races, the ±1σ band should be roughly half the current width.

### Key milestones:

| Cagnes (Day 63) | What it means |
|-----------------|---------------|
| < 16:30 (1020s) | Goal is very realistic — all models shift above 50% probability |
| 16:30–17:00 | Goal borderline — needs a big final 3 weeks |
| > 17:00 | Trajectory is flat; training stimulus needs to change |

### Red flags:
- Sick week (already happened, Week 6–7) — the Banister model would show a fitness dip if you had load data. Without it, just note the gap in training and don't expect a huge race PB immediately after illness.
- Consecutive races too close together (less than 2 weeks) — fatigue doesn't dissipate fully between races.

---

## 8. Future Enhancements

If time allows, these would meaningfully improve the tracker:

1. **Full Banister model**: Add training load CSV → models fitness and fatigue separately → shows "form" (fitness − fatigue) → predicts optimal race timing.
2. **Critical Speed curve**: Add 1+ non-5K data point → plots the hyperbolic speed-duration curve → shows what 5K time is achievable at current CS.
3. **Race day weather/course correction**: Adjust times for temperature, wind, elevation (e.g. Monaco may have hills that make it slower than it looks flat).
4. **Strava sync button**: Button in the HTML that triggers the Python sync script (or fetches Strava API directly with OAuth token stored in localStorage).
5. **Workout planner overlay**: Show planned vs. actual training load on the chart — gaps (illness, rest) become visible.

---

*Last updated: Feb 15, 2026 · After Monaco (17:29) · Week 7 of 16*
