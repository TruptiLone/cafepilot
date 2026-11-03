#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# The project venv has every dependency (Pillow for the delivery count); override with CAFEPILOT_PYTHON.
PYTHON_BIN="${CAFEPILOT_PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
PORT="${BARISTA_PHOTO_PORT:-8004}"
export BARISTA_PHOTO_PORT="$PORT"
SERVICE_NAME="${BARISTA_PHOTO_SERVICE_NAME:-$(scutil --get ComputerName | tr -cd '[:alnum:] -')}"

"$PYTHON_BIN" -m uvicorn backend.media_demo:app --host 0.0.0.0 --port "$PORT" &
API_PID=$!
dns-sd -R "Barista Photo Receiver ($SERVICE_NAME)" _baristaphoto._tcp local "$PORT" path=/api/upload &
DNS_PID=$!
cleanup() {
  kill "$API_PID" "$DNS_PID" 2>/dev/null || true
  wait "$API_PID" "$DNS_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
printf 'Barista photo receiver is available through Bonjour on port %s (/api/upload).\n' "$PORT"
printf 'Bonjour service: _baristaphoto._tcp · Mac folder: %s/Pictures/BaristaShared\n' "$HOME"
printf 'Keep this terminal open while using automatic photo transfer. Press Ctrl-C to stop.\n'
wait "$API_PID"
