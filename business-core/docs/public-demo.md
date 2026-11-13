# Public demo: one container

The public demo runs as one Docker container that serves the web app and the API. Visitors enter an access code;
each one gets a private copy of the demo café (a Postgres database copied from `cafepilot_template`).

## Build (from the repository root)

```bash
cd ~/Documents/GitHub/cafepilot
docker build -f business-core/Dockerfile -t cafepilot-demo .
```

The build uses the repository root because the seeds read `data/*.json`. `business-core/Dockerfile.dockerignore`
keeps `.env`, `node_modules`, `var/` and recordings out of the image.

## Run against your local Postgres

```bash
cd ~/Documents/GitHub/cafepilot/business-core
docker compose up -d
set -a && . ./.env && set +a
docker run --rm -p 127.0.0.1:8090:8000 \
  -e POSTGRES_DB -e POSTGRES_USER -e POSTGRES_PASSWORD -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT \
  -e MODEL_API_KEY -e DEMO_ACCESS_CODE=pick-a-code cafepilot-demo
```

Open http://localhost:8090 in Chrome (two private windows give two separate cafés). Stop with Ctrl+C.
On start the container waits for Postgres, rebuilds `cafepilot_template` (about 10 s), then starts one server
process.

## Settings

| Variable | In the image | Meaning |
|---|---|---|
| `DEMO_ACCESS_CODE` | none: **required** | The code visitors type. Changing it signs everyone out. |
| `MODEL_API_KEY` | none: **required** | Meta Model API key. |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | none: **required** | The database server; the user needs CREATEDB. |
| `MUSE_DAILY_LIMIT` | 200 | Muse calls per day (Pacific); resets at midnight and on restart. |
| `MAX_VISITOR_DATABASES` | 15 | Cafés at once; when full, one idle 10+ minutes is closed, else "busy". |
| `VISITOR_IDLE_MINUTES` | 120 | A café unused this long is dropped. |
| `PUBLIC_DEMO` | 1 | Hides the API explorer, marks cookies Secure (HTTPS). |
| `PER_VISITOR_DATABASES` | 1 | A private café per visitor. |
| `PORT` | 8000 | The port to listen on (hosts like Render set it). |

Run **one** instance with one server process: the café list and the Muse count live in its memory.

## Host on Render

`render.yaml` (repository root) describes everything: the web service built from `business-core/Dockerfile`, a
Postgres 17 database (`basic-256mb`, 5 GB, reachable only from the web service), and the settings above. Both are
in Oregon. Plans: web `starter` (stays awake), database `basic-256mb`.

1. Push the branch to GitHub (Render reads `render.yaml` from it).
2. In the Render dashboard: **New → Blueprint**, connect GitHub, pick the `cafepilot` repo and the branch.
3. Render asks for the two secrets: `MODEL_API_KEY` and `DEMO_ACCESS_CODE`. Click **Apply**.
4. Watch the web service's **Logs**. The first start should end with
   `Template cafepilot_template ready: 35 MB; one visitor copy takes … s` and `Uvicorn running`.
   If it stops with `permission denied to create database`, Render's user can't copy databases: tell Claude
   (the fallback is a Neon database).
5. Open `https://cafepilot-demo.onrender.com` (the name may differ: Render shows it), enter the code, then check
   in a second private window that you get a separate café.

Later pushes deploy by themselves once CI passes (`autoDeployTrigger: checksPass`). To change the code or the
limit: the service's **Environment** tab, then **Save**; Render restarts it. A restart clears the daily Muse
count, and visitors keep their cafés (they're adopted on start).
