# 16→sub16

Road-to-sub-16 5K tracker with a static dashboard (`index.html`) and a Python
pipeline that updates Banister fitness-fatigue model inputs from Strava and
optional TrainingPeaks exports.

## What This Repo Contains

- `index.html` — single-page dashboard with model curves, cards, and controls
- `scripts/update_banister.py` — data pipeline (Strava fetch, TRIMP, Banister fit, HTML injection)
- `data/training_log.json` — normalized activity log used by the model
- `data/raw/` — optional raw imports (for example TrainingPeaks ZIP exports)
- `.github/workflows/update_banister.yml` — daily + manual GitHub Actions refresh
- `STRAVA_SETUP.md` — one-time OAuth and GitHub secrets setup guide

## Quick Start

### 1) Install Python dependency

```bash
python3 -m pip install -r requirements.txt
```

### 2) Open the dashboard

Open `index.html` directly in your browser.

## Updating Banister Data

### Run the updater locally

```bash
python3 scripts/update_banister.py
```

Useful flags:

- `--dry-run` — compute everything but do not write files
- `--backfill-from YYYY-MM-DD` — reset Strava fetch cursor for a one-off backfill
- `--import-trainingpeaks-zip /path/to/WorkoutExport.zip` — import one or more TrainingPeaks exports
- `--trainingpeaks-all-sports` — include non-run TrainingPeaks workouts with sport weighting

Examples:

```bash
python3 scripts/update_banister.py --dry-run
python3 scripts/update_banister.py --backfill-from 2025-12-28
python3 scripts/update_banister.py \
  --import-trainingpeaks-zip data/raw/WorkoutExport-Wilson-Daniel-2025-10-01-2026-02-16.zip
```

## Strava + GitHub Actions Automation

After adding Strava secrets, GitHub Actions runs the updater daily at `06:00 UTC`
and can also be triggered manually from the Actions tab.

Required repository secrets:

- `STRAVA_CLIENT_ID`
- `STRAVA_CLIENT_SECRET`
- `STRAVA_REFRESH_TOKEN`

Setup details are in `STRAVA_SETUP.md`.

## Screenshots

Add screenshots to `docs/screenshots/` and reference them here.

Example placeholders:

- Main dashboard view
- Model comparison cards
- Banister tau tuning controls

Suggested filenames:

- `docs/screenshots/dashboard-overview.png`
- `docs/screenshots/model-cards.png`
- `docs/screenshots/banister-controls.png`

## Publish with GitHub Pages

1. Push `index.html` to the `master` branch.
2. In GitHub, open **Settings → Pages**.
3. Under **Build and deployment**, set:
   - **Source:** `Deploy from a branch`
   - **Branch:** `master`
   - **Folder:** `/ (root)`
4. Save, then wait for the Pages deploy to complete.
5. Open the site URL shown in Pages settings.

Tip: After each model update commit, GitHub Pages will automatically serve the
latest `index.html`.

## Development Notes

- The project is intentionally lightweight: no frontend build step, no framework.
- `index.html` is the published artifact and contains injected Banister data blocks.
- Model logic and rendering behavior are documented in `tracker_reference.md`.

