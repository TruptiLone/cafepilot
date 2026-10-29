#!/usr/bin/env bash
# Seed the local demo database in dependency order. Run from business-core/.
# Each script is idempotent: re-running reports zero new records.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
alembic upgrade head
for script in seed_inventory seed_sales seed_sales_catalog seed_sales_recipes seed_demo_operations; do
  echo "==> ${script}"
  python "scripts/${script}.py"
done
