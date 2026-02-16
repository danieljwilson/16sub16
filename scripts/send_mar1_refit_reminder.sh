#!/bin/zsh
set -euo pipefail

RECIPIENT="daniel.j.wilson@gmail.com"
SUBJECT="16sub16 reminder: update Banister after March 1 race"
BODY=$(cat <<'EOF'
Reminder for you: after the March 1, 2026 race, add the result to RACE_RESULTS and run:

python scripts/update_banister.py
EOF
)

printf '%s\n' "$BODY" | /usr/bin/mail -s "$SUBJECT" "$RECIPIENT"
