#!/bin/bash
# Fresh start for the glasses demo, then the photo receiver:
#   1. empty the shared photo folder (~/Pictures/BaristaShared): old delivery-check pairs and loose photos
#   2. reset the Santa Clara market: its plans, the orders they made, delivery checks and credit requests
#      (scripts/seed_event_surge.py --fresh; stock, shifts and the next PO number go back to the start, PO-123)
#   3. start the receiver (scripts/run_local_photo_receiver.sh)
# Run from business-core/:  ./scripts/reset_demo_and_start_receiver.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${CAFEPILOT_PYTHON:-$ROOT/.venv/bin/python}"
PORT="${BARISTA_PHOTO_PORT:-8004}"
SHARED="${BARISTA_SHARED_PHOTOS_DIR:-$HOME/Pictures/BaristaShared}"
SHARED="${SHARED/#\~/$HOME}"

if lsof -ti ":$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "A receiver is already running on port $PORT. Stop it first (Ctrl-C in its terminal), then run this again." >&2
  exit 1
fi
case "$SHARED" in
  "" | "/" | "$HOME" | "$HOME/" | "$HOME/Pictures" | "$HOME/Pictures/")
    echo "Refusing to empty '$SHARED': set BARISTA_SHARED_PHOTOS_DIR to the receiver's own folder." >&2
    exit 1 ;;
esac

if [[ -d "$SHARED" ]]; then
  count=$(find "$SHARED" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')
  find "$SHARED" -mindepth 1 -delete
  echo "Emptied $SHARED ($count item(s) removed)."
else
  echo "$SHARED doesn't exist yet; the receiver creates it."
fi

PYTHONPATH=. "$PYTHON_BIN" scripts/seed_event_surge.py --fresh

exec ./scripts/run_local_photo_receiver.sh
