#!/usr/bin/env bash
# Long-running hourly loop (GitHub Actions job). Each iteration: pull, run the engine, guard, commit + push (rebase retry), trigger the Pages build.
# Usage: engine/loop.sh <minutes_to_run>
set -u
MINUTES="${1:-325}"
START=$(date +%s); DEADLINE=$((START + MINUTES*60))
say() { echo "$(date -u '+%F %T') UTC | loop: $*"; }

iteration() {
  git pull -q --rebase origin main || say "pull failed (continuing)"
  python engine/run_all.py || say "run_all exit code $? (continuing)"
  if ! python engine/guard.py; then say "GUARD REFUSED: not publishing this iteration"; git checkout -q -- index.html backtest.html meme.html 2>/dev/null; return; fi
  printf 'User-agent: *\nDisallow: /\n' > robots.txt; : > .nojekyll
  git add data index.html meme.html backtest.html robots.txt .nojekyll
  if git diff --cached --quiet; then say "nothing to commit"; return; fi
  git commit -q -m "paper run $(date -u '+%Y-%m-%d %H:%M') UTC"
  for i in 1 2 3 4; do
    if git pull -q --rebase origin main && git push -q origin HEAD:main; then say "pushed"; break; fi
    say "push attempt $i failed"; sleep $((8*i))
  done
  gh api -X POST "repos/${GITHUB_REPOSITORY}/pages/builds" --silent || true
}

# first iteration now, unless the last commit of the equity log is recent (hand-over from the previous loop run)
LAST=$(git log -1 --format=%ct -- data/combined/equity.csv 2>/dev/null || echo 0)
if (( $(date +%s) - LAST > 1500 )); then iteration; else say "recent run found, waiting for the next hour"; fi

while true; do
  NOW=$(date +%s)
  NEXT=$(( (NOW/3600 + 1)*3600 + 120 ))                 # next hh:02 UTC
  if (( NEXT - NOW > 3600 + 60 )); then NEXT=$((NEXT - 3600)); fi
  if (( NEXT > DEADLINE )); then say "deadline reached, handing over"; break; fi
  say "sleeping $((NEXT-NOW))s until $(date -u -d @$NEXT '+%H:%M')"
  sleep $((NEXT-NOW))
  iteration
done
