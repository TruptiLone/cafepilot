#!/bin/sh
# The public demo container (business-core/Dockerfile):
#   1. wait for Postgres, then build the template café every visitor's copy starts from
#      (fresh on every start, so a new deploy's migrations and seeds reach new visitors)
#   2. start ONE server process: the visitor list and the daily Muse count live in its memory
set -eu
cd "$(dirname "$0")/.."
python scripts/build_template.py --wait
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 \
  --proxy-headers --forwarded-allow-ips "*"
