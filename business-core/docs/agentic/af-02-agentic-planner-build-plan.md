# AF-02 — Agentic Planner: Build Plan (Thu Oct 29, 2026)

_Status: plan only. Builds on `demo-scenario-design.md` (AF-01) and the existing
Inventory / Sales / Staffing modules in `business-core/backend/modules/`._

## Goal for the day

The owner types a complex request in the web UI. CaféPilot:

1. asks the Meta model for a plan (a DAG of registered actions),
2. validates the plan against the action registry and superset DAG,
3. shows the plan as a graph in the UI,
4. runs read actions immediately,
5. pauses at every write action, shows an **Approve / Reject** button,
6. resumes execution after approval.

**Rule of the day:** the model proposes, the code decides. The model only writes
action names + arguments. Code validates, executes, and records everything.

**Working rule:** each task ends with a **Validate** step and a **Summary gate**.
Do not start the next task until the validation passes and a short summary of
changes so far has been written (append to `development-log/day-04-2026-10-29.md`).

---

## Where the code goes

```
business-core/backend/modules/agent/
  registry.py      Action dataclass + REGISTRY (wraps existing services)
  superset.py      produces/consumes edges + explicit safety `requires`
  planner.py       builds prompt from registry, calls Meta model, returns plan JSON
  validator.py     checks plan against registry + superset, derives edges from $refs
  executor.py      topological execution, pause at writes, resume on approval
  models.py        plan_runs, node_runs, approvals (SQLAlchemy)
  schemas.py       PlanNode, Plan, PlanRunResponse, ApprovalRequest (Pydantic)
  router.py        /api/v1/agent/*
business-core/alembic/versions/<new>_create_agent_runs.py
business-core/tests/test_agent_registry.py
business-core/tests/test_agent_validator.py
business-core/tests/test_agent_executor.py
business-core/tests/test_agent_api.py
business-core/frontend/src/api/agent.ts
business-core/frontend/src/pages/AgentPage.tsx
business-core/frontend/src/components/PlanGraph.tsx
business-core/frontend/src/components/ApprovalCard.tsx
```

Reuse `create_meta_client()` from `backend/agents/cafe_agent.py`
(base URL `https://api.meta.ai/v1`, `MODEL_API_KEY`) — move or import it into
`modules/agent/planner.py`; do not create a second client.

---

## Task 1 — Action registry + dependency graph (Pydantic)

**Build**

- `Action` dataclass: `name, description, handler, input_model, output_model,
  side_effect ("read" | "write"), consumes, produces, requires, approval`.
- Handlers call the **existing service classes directly** (no HTTP hop), opening a
  DB session per call: `InventoryService`, `SalesService`, `StaffingService`.
- `input_model` reuses existing Pydantic schemas where they exist
  (`StockCountCreate`, `InventoryMovementCreate`, `ShiftCreate`, …). Read actions get
  small new input models (e.g. `StockEstimateQuery(cafe_id, as_of, ingredient_ids)`).
- JSON schemas come from `model_json_schema()` — never hand-written.
- `superset.py`: data edges computed from `produces ∩ consumes`, plus explicit
  `requires` safety rules. Fail fast on cycles at import time (`graphlib`).

**Candidate actions from existing code**

| Action | Service method | R/W |
|---|---|---|
| `list_ingredients` | `InventoryService.list_items` | R |
| `get_stock_estimates` (supports `as_of`) | `InventoryService.list_stock_estimates` | R |
| `get_stock_estimate` | `InventoryService.get_stock_estimate` | R |
| `list_stock_counts` / `get_stock_count` | `list_counts` / `get_count` | R |
| `list_inventory_movements` | `list_movements` | R |
| `create_stock_count` | `create_count` | W |
| `verify_stock_count` | `verify_count` | W |
| `record_inventory_movement` | `create_movement` | W |
| `list_products` | `SalesService.list_products` | R |
| `list_sales` / `get_sale` | `list_sales` / `get_sale` | R |
| `get_sale_ingredient_usage` | `get_ingredient_usage` | R |
| `sales_summary` | `SalesService.report` | R |
| `list_employees` / `get_employee` | `StaffingService.list_employees` / `get_employee` | R |
| `list_shifts` | `list_shifts` | R |
| `coverage_report` | `coverage_report` | R |
| `schedule_shift` | `schedule_shift` | W |
| `cancel_shift` | `cancel_shift` | W |

**Gaps (not in code yet, needed for the hero scenario)**

- `project_recipe_usage` (read): hypothetical product mix → ingredient quantities
  (AF-01 Task 3). Needed to turn "150 extra drinks" into milk/beans.
- `propose_supplier_order` (write, **simulated**): no supplier module exists yet.

**Open dependency questions for Trupti (answer before coding the superset)**

1. Should the agent be allowed to call `create_sale` and `create_employee` at all,
   or are those excluded (POS-only / HR-only)?
2. `schedule_shift`: must it require `coverage_report` **and** `list_shifts`
   (overlap) **and** `list_employees` (eligibility) first?
3. `record_inventory_movement` (receipt / waste / adjustment): must it require
   `get_stock_estimate` for that ingredient first?
4. `cancel_shift`: require `list_shifts` first?
5. `verify_stock_count`: require the count to exist (`create_stock_count` or
   `get_stock_count`) — confirm.
6. Should any write be auto-approved (e.g. creating a **draft** stock count), or do
   all writes need owner approval for the demo? (Recommendation: all writes need
   approval today; policy auto-approval later.)
7. Add `project_recipe_usage` and a simulated `propose_supplier_order` today, or
   keep today strictly to existing endpoints?

**Validate**

- `pytest tests/test_agent_registry.py`: every action has a handler, schema
  generates, `side_effect` set; superset has no cycles; data edges as expected;
  `requires` edges present.
- Print the registry as the JSON the planner will see; read it once by eye.

**Summary gate** → log, then Task 2.

---

## Task 2 — Planner: complex query → runtime DAG

**Build**

- `POST /api/v1/agent/plans` body: `{ query, cafe_id, as_of? }`.
- `planner.build_prompt(registry)`: action list, schemas, dependency rules, output
  format. Built from the registry on **every** call (model is stateless).
- Call the Meta model (Muse Spark 1.3 today; Muse Wonder if available on your key)
  with **structured output** for this plan schema:

```json
{ "goal": "…",
  "nodes": [ { "id": "s1", "action": "get_stock_estimates",
               "args": { "cafe_id": "CAFE001", "as_of": "2026-10-29T18:00:00-07:00" },
               "reason": "Need current milk stock" } ] }
```

- Arguments may reference other nodes: `"$s1"` or `"$s1.items[0].quantity"`.
- Log every model call (model, prompt, response, tokens, latency) to an
  `llm_calls` table or a JSONL file — enough for debugging today.

**Validate**

- Unit test with a **mocked** client returning a fixed plan (no API cost).
- One live call with the hero query:
  _"Farmers market this Sunday Nov 1, 8 AM–2 PM. Check milk and bean stock as of
  Thursday 6 PM, check Sunday staff coverage, and add a barista 7:30 AM–2:30 PM if
  we're short."_ Save the raw response as a fixture for later tests.

**Summary gate** → log, then Task 3.

---

## Task 3 — Validator

**Build** — reject or repair the plan before anything runs:

1. every `action` exists in the registry (no invented actions);
2. `args` validate against `input_model` (unresolved `$refs` checked by type later);
3. edges derived from `$refs` in args (the model does not list edges);
4. every referenced node id exists;
5. every superset `requires` predecessor is present upstream — if missing, **reject
   with a clear reason** (no auto-insert today);
6. no cycles (`graphlib.TopologicalSorter.prepare()`);
7. return `ValidatedPlan` with nodes, edges, levels, and per-node `side_effect`.

On rejection: return reasons to the UI; optional single retry where the reasons are
sent back to the model ("fix these problems").

**Validate** — `tests/test_agent_validator.py`, at minimum:

- unknown action → rejected
- bad arg type → rejected
- `$ref` to missing node → rejected
- `schedule_shift` without required predecessors → rejected (demo moment)
- cycle → rejected
- the saved live fixture from Task 2 → accepted

**Summary gate** → log, then Task 4.

---

## Task 4 — Executor

**Build**

- Tables (Alembic migration):
  - `plan_runs(run_id, cafe_id, query, goal, status, as_of, created_at, updated_at)`
  - `node_runs(node_id, run_id, action, args, resolved_args, side_effect, status,
    result, error, idempotency_key, started_at, finished_at)`
  - `approvals(approval_id, node_id, decision, edited_args, decided_by, decided_at,
    channel)`
- Node status: `pending → running → done | failed`, writes:
  `pending → awaiting_approval → (approved) running → done | rejected → skipped`.
  Dependents of a skipped/failed node become `skipped`.
- `execute(run_id)`: load run → for each ready node in topological order → resolve
  `$refs` from upstream results → reads run immediately → writes stop at
  `awaiting_approval` → save → **return** (nothing waits in memory).
- Idempotency key per node = hash(run_id, action, resolved_args). Write handlers
  pass it where the service supports idempotent identities.
- Plan run status: `running | awaiting_approval | completed | failed`.

**Validate** — `tests/test_agent_executor.py` (rollback-isolated test café):

- read-only plan runs to `completed`
- plan with a write stops at `awaiting_approval`; the write has **not** happened
- failed node → dependents skipped
- calling `execute` twice performs no duplicate work

**Summary gate** → log, then Task 5.

---

## Task 5 — Web UI: show the DAG

**Build**

- New nav item + `AgentPage.tsx`: query box, "Make plan" button, run status.
- `PlanGraph.tsx`: render nodes in **columns by topological level** (plain React +
  CSS, no new dependency). Each node card: action name, reason, R/W badge, status
  colour (pending / running / done / awaiting approval / skipped / failed).
  React Flow (`@xyflow/react`) only if time allows.
- Click a node → side panel with args, resolved args, result JSON, error
  (seed of the "Why" panel).
- API: `GET /api/v1/agent/runs/{run_id}` returns run + nodes + edges + levels.
  Poll every 1–2 s while status is `running` (WebSocket later).
- Show validator rejections clearly when a plan is refused.

**Validate**

- Vitest: renders levels and statuses from a fixture response.
- Manual: run the hero query, watch nodes turn green; screenshot to
  `docs/progress/screenshots/agent-plan-graph.jpg`.

**Summary gate** → log, then Task 6.

---

## Task 6 — Approval and resume

**Build**

- `ApprovalCard.tsx`: for each `awaiting_approval` node show what will happen in
  plain words (e.g. "Schedule Noor, barista, Sun Nov 1 7:30 AM–2:30 PM PST"),
  plus **Approve** / **Reject** (and "Approve all" when several writes wait).
- `POST /api/v1/agent/nodes/{node_id}/approval` body
  `{ decision: "approve" | "reject", edited_args? }`:
  - ignore if node is not `awaiting_approval` (double-click safe);
  - if `edited_args`, re-validate schema;
  - record in `approvals`;
  - call `execute(run_id)` to resume.
- Reject → node `skipped`, dependents `skipped`, run continues where possible.

**Validate**

- API test: plan with `schedule_shift` → approve → shift exists exactly once;
  approve again → no second shift; reject path → no shift, dependents skipped.
- Manual end-to-end in the UI: query → graph → approve → node turns green →
  shift visible on the Staffing page.

**Summary gate** → final day summary + screenshots in the development log.

---

## Order of priority if time runs short

1. Tasks 1, 3, 4 (registry, validator, executor) — the core, fully testable offline.
2. Task 2 with a mocked model first, live call second.
3. Task 6 approval API before Task 5 polish.
4. Task 5 graph as columns; fancy graph library last.

Out of scope today: policy auto-approval, replanning on failure, supplier module,
voice, WebSocket, LangGraph/LangChain/LangSmith.
