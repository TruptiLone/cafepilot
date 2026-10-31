# CaféPilot: notes for Claude Code

Trupti Lone's entry for the **Meta Global AI Developer Hackathon** (submission **Wed Nov 4, 2026**).
An AI operations assistant for independent cafés, powered by **Muse Spark** (`muse-spark-1.3`) via the
OpenAI-compatible Meta Model API.

**Core principle: the model proposes, the code decides.** Muse writes plans and reads images; code
validates every step, does all arithmetic, and pauses every change for the owner's OK. Never let the
model type a quantity, price, time offset or ID that code can compute or look up.

## Working with Trupti

- Plain, short explanations. She runs commands herself on a MacBook (zsh, `.venv`, Cursor).
- Give exact commands with the folder they run in. Say when uvicorn must be restarted.
- Branch: `agent/planner` (PR #1 into `main` is open). Commit only when asked; never force-push.
- The demo video has no narration: everything must be readable on screen.
- Don't `pkill -f "uvicorn backend.main"` (it can match its own shell). Stop the server with Ctrl+C,
  or `lsof -ti :8001 | xargs kill`.

## Layout (everything lives in `business-core/`)

- `backend/` FastAPI + SQLAlchemy + Alembic, Postgres 17 (docker, port **5433**)
  - `modules/agent/`: planner (`planner.py`, prompt rules), `registry.py` (typed actions),
    `validator.py`, `executor.py` (stateful DAG runs, approvals, stable record IDs),
    `calculations.py` (`calculate_shortfall`), `demand.py` (`estimate_event_demand` from sales
    history), `events.py` (detected events), `summaries.py` / `acknowledgements.py` (plain-language
    step summaries and honest lifecycles), `replanning.py`, `status.py` (café status panel),
    `cafe_policies.py` (staffing targets, buffer, pack sizes, suppliers, opening hours)
  - `modules/purchasing/`: purchase orders (PO numbers from 1040), simulated suppliers, shift offers,
    `settle_due` reveals simulated replies
  - `modules/receiving/`: delivery check. `vision.py` (Muse reads packing-slip and delivery photos,
    images only), `service.py` (compare ordered / slip / seen / counted, confirm, credit request)
  - `modules/inventory`, `sales`, `staffing`, `events`
- `frontend/` React 19 + Vite + TypeScript. Proxy `/api` → `127.0.0.1:8001`
  - `pages/AgentPage.tsx` Ask CaféPilot (Genspark v5 design): events → plan tiles or dependency map →
    approval list → "What happened"; right pane `components/muse/CafeStatusPanel.tsx`
  - `components/muse/` (`PlanMap`, `ApprovalList`, `PlanReceipt`, `stages.ts`)
  - `pages/ReceivingPage.tsx` phone-sized delivery check
  - Styles: `styles.css`; Genspark tokens/classes are prefixed `--g-` / `mz-`
- `scripts/` seeds and checks; `tests/` pytest; `demo-assets/receiving/` slip + delivery photo

## Run

```bash
cd business-core
docker compose up -d
source .venv/bin/activate
./scripts/seed_all.sh                                   # first time: migrations + demo data
alembic upgrade head                                    # after pulling new migrations
PYTHONPATH=. python scripts/seed_event_surge.py --fresh # reset the market demo
PYTHONPATH=. python scripts/seed_event_surge.py --fresh --with-delivery  # + PO-1042 for receiving
uvicorn backend.main:app --port 8001 --reload
# second terminal
cd business-core/frontend && npm run dev                # http://localhost:5173
```

`.env` needs `POSTGRES_*`, `MODEL_API_KEY`, `PLANNER_MODEL` (optional `MODEL_API_BASE_URL`).
`PYTHONPATH=. python scripts/try_vision.py` checks Muse's image reading live.

## Test (keep both green; CI runs them on every push)

```bash
cd business-core && python -m pytest -q
cd business-core/frontend && npx tsc --noEmit && npx vitest run && npm run build
```

Backend tests use the real Postgres and roll back. Model calls in tests use fake clients
(`tests/test_agent_planner.py::FakeClient`); receiving tests replay Muse's real answers from
`tests/fixtures/receiving/vision_live.json`.

## Demo scenarios (all data is synthetic; "as of" Thu Oct 29 2026, 6 PM PT)

1. **Event surge (done).** Santa Clara Farmers Market, Sun Nov 1, 8 AM–2 PM. Muse finds it, estimates
   extra demand from the last 6 editions (typically +35 drinks), checks stock at event time (after
   normal use and standing deliveries), orders milk in whole cases, adds a barista (Sofia) for 2 of 4
   staffed hours. Bay Dairy refuses a 6 AM Sunday delivery → "Find an alternative" → Willow Ridge.
   Halloween (Sat) is a quieter event: plan should find little or nothing to do.
2. **Receiving (done, backend + phone page).** Packing slip PS-58213 for PO-1042 from Willow Ridge
   (2 cases whole, 1 case oat). Muse reads the slip, counts what it can see in the photo (whole milk
   crate may hide cartons → owner counts), only accepted quantities go into stock, shortfalls draft
   credit request CR-58213. A slip can only be received once.
3. **Equipment troubleshooting (next).** Owner records a faulty coffee machine with Meta glasses;
   frames (not video) go to Muse; it returns likely cause, safe steps, and when to stop and call a
   technician (hot water, steam, pressure: safety first).

Catering is dropped. Staffing math in code is paused.

## Conventions

- Every number shown to the owner comes from code; summaries are written by code, not the model.
- Honest states: Proposed → Approved → Sent → Confirmed / Rejected for orders; Offered → Accepted /
  Declined for shifts. Never say "ordered" when it was only sent.
- Times carry the café's UTC offset for that date (DST ends Nov 1).
- New migrations: add with Alembic, update `tests/test_database_schema.py` (REVISION + tables).
- UI copy: plain café language, no jargon; "Needs your OK", "Nothing changes until you approve".

## Next up

- Restyle Receiving in the Genspark look; rush order when a short delivery can't wait
- Equipment troubleshooting use case (glasses frames → Muse → safe steps)
- Demo prep: one-click reset, captions, record video, merge PR #1, README for judges
- Daily notes go in `development-log/` (`day-NN-YYYY-MM-DD.md`)
