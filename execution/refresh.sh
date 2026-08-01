#!/bin/bash
# Regenerate stats.json, both card PNGs, and freeze any newly-completed month.
# Called on a schedule by launchd (com.claude-counter) and safe to run by hand.
#
# NOT `set -e`: a failure in one step must not silently skip the rest. Each step
# is run explicitly and its exit code recorded, so a partial failure is visible in
# the log instead of the script vanishing mid-run.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# launchd gives a minimal PATH; python3 and Chrome both need a real one.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

LOG="$HERE/../out/refresh.log"
mkdir -p "$(dirname "$LOG")"

run () {   # run <label> <cmd...>
  local label="$1"; shift
  echo "--- $label"
  "$@"
  local rc=$?
  [ $rc -ne 0 ] && echo "!!! $label FAILED rc=$rc"
  return $rc
}

status=0
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') === python3=$(command -v python3)"
  run count   python3 count.py   || status=1
  run archive python3 archive.py || status=1
  run render  python3 render.py  || status=1
  echo "=== done, status=$status"
  echo
} >>"$LOG" 2>&1

tail -n 500 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $status
