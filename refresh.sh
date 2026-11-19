#!/usr/bin/env bash
# Scrape + commit + push, for running on a machine we control (the pi's cron).
# The GitHub workflow does the same thing; either can run, a second run just
# finds nothing new to commit.
set -euo pipefail
cd "$(dirname "$0")"
echo "=== $(date -Is) refresh start"
git pull -q --rebase --autostash
python3 fetch.py
if git diff --quiet -- data.js; then
  echo "=== $(date -Is) no change"
  exit 0
fi
git commit -qam "data: refresh $(date +%F)"
git push -q
echo "=== $(date -Is) pushed"
