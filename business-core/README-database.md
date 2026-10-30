# Café Business Core database

This directory runs the Café Business Core PostgreSQL database locally. The
database listens only on `127.0.0.1:5433`, so it is unavailable from other
machines on the network.

## Configure the local environment

Create `business-core/.env` from the example and replace the password
placeholder with a nonempty local development password. Do not commit this
file.

```bash
cp business-core/.env.example business-core/.env
```

The configured values are:

- `POSTGRES_DB`: `cafe_business_db`
- `POSTGRES_USER`: `cafepilot`
- `POSTGRES_PASSWORD`: your nonempty local development password
- `POSTGRES_PORT`: `5433`

## Start the database

Run these commands from the repository root:

```bash
docker compose -f business-core/compose.yaml --env-file business-core/.env config --quiet
docker compose -f business-core/compose.yaml --env-file business-core/.env up -d db
```

## Check health and connect

Check the service state:

```bash
docker compose -f business-core/compose.yaml --env-file business-core/.env ps
```

Run the connectivity queries:

```bash
docker compose -f business-core/compose.yaml --env-file business-core/.env exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT current_database(), current_user;"'
docker compose -f business-core/compose.yaml --env-file business-core/.env exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version();"'
```

To connect interactively using a locally installed `psql` client:

```bash
psql -h 127.0.0.1 -p 5433 -U cafepilot -d cafe_business_db
```

## Seed the demo data

Seed scripts depend on each other and must run in this order:

```bash
cd business-core
./scripts/seed_all.sh
```

which runs `alembic upgrade head`, then `seed_inventory → seed_sales → seed_sales_catalog → seed_sales_recipes → seed_demo_operations` with `PYTHONPATH=.`. Every script is idempotent, so re-running is safe. Tests marked `database` that read the January 2025 demo data are skipped until this has been run.

## Demo scenario: Sunday farmers market

After `seed_all.sh`, load the Event Surge scenario (Thursday Oct 29 closing count, an oat-milk
spill, and three existing Sunday Nov 1 shifts):

```bash
PYTHONPATH=. python scripts/seed_event_surge.py --fresh
```

`--fresh` first removes the scenario and everything the agent changed for CAFE001 (its runs and
the shifts, counts and movements created by approved steps), then seeds again, so every demo
starts from the same state. `--reset` alone is a dry run that lists what would be removed;
add `--yes` to delete. January 2025 demo data is never touched.

`seed_all.sh` also loads synthetic sales history (May–Oct 2026 and Sep–Nov 2025, line-level with
milk choices) and the event calendar (`scripts/seed_sales_history.py`): six past monthly farmers
markets, last Halloween, holidays, festivals, football nights and heat waves, plus the upcoming
Halloween and Nov 1 market. Expected demand for an event is read from that history in code
(`estimate_event_demand`). The history is demo data: the method is real, the numbers are not a
validated forecast.

Expected picture as of Thursday 6 PM: whole milk 3 L and oat milk 1.25 L on hand (low before the next
morning's regular delivery); past markets added about +19 to +53 drinks (typically +35) on a normal
96; Sunday 8 AM-noon has 2 of the 4 people the café wants on an event day, noon-2 PM has 3 of 4.

## Stop without deleting data

```bash
docker compose -f business-core/compose.yaml --env-file business-core/.env stop db
```

The `cafe_business_postgres_data` named volume persists database data across
ordinary container stops and restarts. Do not use `docker compose down -v`
unless you intentionally want to remove that data.

## Troubleshooting

```bash
docker compose -f business-core/compose.yaml --env-file business-core/.env logs db
docker compose -f business-core/compose.yaml --env-file business-core/.env ps
docker volume ls | grep cafe_business_postgres_data
```
