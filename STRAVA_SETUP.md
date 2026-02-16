# Strava → Banister Setup Guide

This guide walks you through connecting your Strava account so that the
Banister fitness-fatigue model auto-updates daily via GitHub Actions.

---

## 1. Create a Strava API Application

1. Log in to Strava and go to **https://www.strava.com/settings/api**
2. Fill in:
   - **Application Name**: something like `16sub16 Tracker`
   - **Category**: `Data Importer`
   - **Website**: your GitHub Pages URL (or any URL)
   - **Authorization Callback Domain**: `localhost`
3. Click **Create** and note down:
   - **Client ID** (a number, e.g. `12345`)
   - **Client Secret** (a long hex string)

---

## 2. One-Time OAuth2 Flow to Get a Refresh Token

Strava uses OAuth2. You need to do this browser dance once to get a
long-lived `refresh_token`.

### Step A — Open this URL in your browser

Replace `YOUR_CLIENT_ID` with your actual Client ID:

```
https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all
```

### Step B — Authorize the app

Click **Authorize** on the Strava page. Your browser will redirect to
something like:

```
http://localhost/?state=&code=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX&scope=...
```

Copy the `code=...` value (everything between `code=` and `&scope`).

### Step C — Exchange the code for tokens

Run this in your terminal (replace placeholders):

```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d code=THE_CODE_FROM_STEP_B \
  -d grant_type=authorization_code
```

The JSON response contains `"refresh_token": "..."` — copy that value.

---

## 3. Create the GitHub Repository (if not already done)

```bash
cd /path/to/2026_16sub16
git init
git add .
git commit -m "Initial commit"
gh repo create 16sub16 --public --source=. --push
```

Or create the repo on GitHub first, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/16sub16.git
git push -u origin main
```

---

## 4. Add GitHub Secrets

In your GitHub repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret Name            | Value                          |
|------------------------|--------------------------------|
| `STRAVA_CLIENT_ID`     | Your numeric Client ID         |
| `STRAVA_CLIENT_SECRET` | Your Client Secret string      |
| `STRAVA_REFRESH_TOKEN` | The refresh token from Step 2C |

---

## 5. Trigger a Manual First Run

1. Go to your repository on GitHub
2. Click the **Actions** tab
3. Click **Update Banister Model** in the left sidebar
4. Click **Run workflow** → **Run workflow**
5. Watch the run complete — it should fetch your Strava activities and
   update `16sub16_tracker.html` and `data/training_log.json`

After the first run, the workflow runs automatically every day at 06:00 UTC.

---

## 6. Personalising Athlete Constants

Open `scripts/update_banister.py` and adjust these values near the top:

```python
REST_HR = 45   # your resting heart rate (bpm)
MAX_HR  = 185  # your maximum heart rate (bpm)
```

The model uses these to compute Heart Rate Reserve (HRR) for TRIMP:

```
hr_ratio = (avg_hr - REST_HR) / (MAX_HR - REST_HR)
TRIMP    = duration_min × hr_ratio × exp(1.92 × hr_ratio)
```

A rough rule for MAX_HR: `220 - age`. For REST_HR, measure after waking
before getting up.

---

## Notes

- **No HR monitor?** The script defaults missing `avg_hr` to 65% HRR
  (comfortable easy pace). TRIMP will still accumulate but less accurately.
- **The model needs all 3 race results** to fit p₀, k₁, k₂. Until then
  it prints "Banister model not yet active" and the HTML shows no Banister line.
- **FIT (τ=47d)** represents long-term aerobic fitness accumulation.
  **FAT (τ=6d)** represents short-term fatigue that decays quickly.
- After a race or rest week, FAT drops faster than FIT — this is the
  classic "taper" effect and why form peaks a week before race day.
