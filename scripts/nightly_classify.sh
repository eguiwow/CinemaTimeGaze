#!/bin/zsh
# Nightly classification pass, on the personal Claude Code account.
#
# Resumable and idempotent: it labels whatever is still unlabelled and exits. Once the
# corpus is fully labelled it does nothing and returns 0, so it is safe to leave scheduled.
#
#   manual run:   ./scripts/nightly_classify.sh
#   install:      launchctl load -w ~/Library/LaunchAgents/com.eguiwow.cinematimegaze.plist

set -u
PROJECT="${PROJECT:-$HOME/Projects/Ander/CinemaTimeGaze}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"                    # real binary, never the alias
ACCOUNT_DIR="${ACCOUNT_DIR:-$HOME/.claude-ander}"     # the personal account
PER_YEAR="${PER_YEAR:-50}"

cd "$PROJECT" || { echo "no such project: $PROJECT"; exit 1; }
mkdir -p logs
LOG="logs/classify-$(date +%Y%m%d-%H%M).log"
LOCK="logs/.classify.lock"

# one run at a time - a long night's run must not collide with the next schedule
if [ -e "$LOCK" ]; then
  if kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "$(date '+%F %T') another run is still going (pid $(cat "$LOCK")), skipping" >>"$LOG"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ >"$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

# resolve the binary the way a subprocess must: no aliases
BIN="$(command -v "$CLAUDE_BIN" 2>/dev/null)"
if [ -z "$BIN" ]; then
  echo "$(date '+%F %T') claude binary not found (CLAUDE_BIN=$CLAUDE_BIN)" >>"$LOG"; exit 1
fi

{
  echo "=== $(date '+%F %T') start ==="
  echo "binary   $BIN"
  echo "account  $ACCOUNT_DIR"
  python3 src/classify_api.py \
      --backend cli \
      --cli-cmd "$BIN" \
      --claude-config-dir "$ACCOUNT_DIR" \
      --per-year "$PER_YEAR"
  rc=$?
  echo "=== $(date '+%F %T') finished rc=$rc ==="
} >>"$LOG" 2>&1

# rebuild the page only if there is something to build - an empty label file is not an error
if [ -s data/labels_api.jsonl ]; then
  {
    python3 src/build_targets.py --labels labels_api.jsonl && python3 src/build_viz.py
  } >>"$LOG" 2>&1
else
  echo "no labels yet - skipping the rebuild" >>"$LOG"
fi

tail -6 "$LOG"
