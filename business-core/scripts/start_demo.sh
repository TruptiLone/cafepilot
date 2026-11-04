#!/bin/bash
# One command for a recording take: stop old servers, reset the demo, start everything.
#   1. stop whatever listens on 8001 (API), 5173 (web app) and 8004 (photo receiver)
#   2. Postgres up (docker compose), migrations to head
#   3. empty the receiver's shared photo folder (~/Pictures/BaristaShared) so old photo pairs don't come back
#   4. reset the data: seed_event_surge.py --fresh (the Farmers Market stays hidden until the first
#      question in Ask; the plan's first order is PO-123, the only delivery on Receiving)
#   5. start the API, the web app and the photo receiver; logs go to var/demo-logs/
# Run from business-core/:   ./scripts/start_demo.sh            (Ctrl-C stops all three)
# Reset between takes:       ./scripts/start_demo.sh --reset    (data and photos only; servers keep running)
# Stop servers started elsewhere: ./scripts/start_demo.sh --stop
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${CAFEPILOT_PYTHON:-$ROOT/.venv/bin/python}"
SHARED="${BARISTA_SHARED_PHOTOS_DIR:-$HOME/Pictures/BaristaShared}"
SHARED="${SHARED/#\~/$HOME}"
LOGS="$ROOT/var/demo-logs"
PORTS=(8001 5173 8004)

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "No Python at $PYTHON_BIN. Make the venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)" >&2
  echo "or point CAFEPILOT_PYTHON at one." >&2
  exit 1
fi

stop_servers() {
  for port in "${PORTS[@]}"; do
    pids=$(lsof -ti ":$port" -sTCP:LISTEN 2>/dev/null || true)
    [[ -z "$pids" ]] && continue
    for pid in $pids; do
      # uvicorn --reload and npm run dev listen from a child; stop the parent too so nothing respawns.
      parent=$(ps -o ppid= -p "$pid" | tr -d ' ')
      if [[ -n "$parent" && "$parent" != 1 ]] && ps -o command= -p "$parent" | grep -qE "uvicorn|vite|npm|run_local_photo_receiver"; then
        kill "$parent" 2>/dev/null || true
      fi
      kill "$pid" 2>/dev/null || true
    done
    for _ in $(seq 20); do lsof -ti ":$port" -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 0.25; done
    echo "Stopped the server on port $port."
  done
}

empty_photos() {
  case "$SHARED" in
    "" | "/" | "$HOME" | "$HOME/" | "$HOME/Pictures" | "$HOME/Pictures/")
      echo "Refusing to empty '$SHARED': set BARISTA_SHARED_PHOTOS_DIR to the receiver's own folder." >&2
      exit 1 ;;
  esac
  if [[ -d "$SHARED" ]]; then
    count=$(find "$SHARED" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')
    find "$SHARED" -mindepth 1 -delete
    echo "Emptied $SHARED ($count item(s) removed)."
  fi
}

reset_data() {
  empty_photos
  PYTHONPATH=. "$PYTHON_BIN" scripts/seed_event_surge.py --fresh
}

wait_for() {  # name, url
  for _ in $(seq 60); do
    curl -s -o /dev/null "$2" && { echo "$1 is up: $2"; return 0; }
    sleep 0.5
  done
  echo "$1 didn't start; see $LOGS." >&2
  return 1
}

case "${1:-}" in
  --stop) stop_servers; exit 0 ;;
  --reset) reset_data; echo "Reset done. Reload http://localhost:5173 (Cmd+Shift+R)."; exit 0 ;;
  "") ;;
  *) echo "Usage: $0 [--reset | --stop]" >&2; exit 2 ;;
esac

stop_servers
docker compose up -d --wait >/dev/null
"$PYTHON_BIN" -m alembic upgrade head
reset_data

mkdir -p "$LOGS"
PIDS=()
cleanup() {
  trap - EXIT INT TERM
  echo; echo "Stopping the demo servers..."
  kill "${PIDS[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
  stop_servers
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" -m uvicorn backend.main:app --port 8001 >"$LOGS/api.log" 2>&1 &
PIDS+=($!)
(cd frontend && exec npm run dev -- --port 5173 --strictPort) >"$LOGS/web.log" 2>&1 &
PIDS+=($!)
CAFEPILOT_PYTHON="$PYTHON_BIN" ./scripts/run_local_photo_receiver.sh >"$LOGS/receiver.log" 2>&1 &
PIDS+=($!)

wait_for "API" http://127.0.0.1:8001/api/v1/agent/events/past
wait_for "Web app" http://localhost:5173/
wait_for "Photo receiver" http://127.0.0.1:8004/
echo
echo "Demo ready: http://localhost:5173  (opens on Ask; the Farmers Market appears after the first question)"
echo "Reset between takes, in another terminal:  ./scripts/start_demo.sh --reset"
echo "Logs: $LOGS   ·   Ctrl-C here stops everything."
wait
