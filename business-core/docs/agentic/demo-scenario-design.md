# CaféPilot Agentic Demo — Scenario and Time Design

_Design status: AF-01, documentation only_

## 1. Canonical hero scenario

### Event Surge Preparation

- **Café:** `CAFE001`
- **Scenario observation time (`as_of`):** Thursday, October 29, 2026 at 6:00 PM `America/Los_Angeles` (`2026-10-29T18:00:00-07:00`, `2026-10-30T01:00:00Z`)
- **Upcoming event:** a synthetic farmers market on Sunday, November 1, 2026
- **Target service window:** 8:00 AM–2:00 PM PST (`2026-11-01T08:00:00-08:00` through `2026-11-01T14:00:00-08:00`)

All values below are **synthetic planning assumptions** for a repeatable demonstration. They are not a validated forecast, a supplier commitment, or evidence of actual customer demand.

### Synthetic incremental demand assumption

Assume the event adds **110 transactions** and the following **150 product units** above ordinary Sunday demand:

| Product mix assumption | Units | Recipe-derived ingredient impact |
|---|---:|---|
| Regular latte with whole milk | 40 | `720 g` coffee beans; `9,600 mL` whole milk |
| Regular latte with oat milk | 20 | `360 g` coffee beans; `4,800 mL` oat milk |
| Large brewed coffee | 30 | `780 g` drip coffee |
| Regular cold brew | 25 | `3,000 mL` cold-brew concentrate |
| Butter croissant | 20 | `20 each` butter croissants |
| Blueberry muffin | 15 | `15 each` blueberry muffins |

The readiness calculation applies a synthetic **10% event buffer** after recipe expansion. Rounded planning requirements are therefore `1,188 g` coffee beans, `10,560 mL` whole milk, `5,280 mL` oat milk, `858 g` drip coffee, `3,300 mL` cold-brew concentrate, `22` croissants, and `17` blueberry muffins.

### Inventory, staffing, and supplier impacts

- Compare each buffered requirement with the ingredient's stock estimate at `as_of`; an unknown or inconsistent estimate must remain unresolved rather than being treated as zero or available stock.
- Use recipe and modifier definitions effective at the target sale time. Oat milk replaces whole milk; it is not added on top of whole milk.
- Treat projected recipe usage as estimated demand, not an inventory movement. No `SALE_USAGE` movement exists until a qualifying POS sale is actually accepted.
- Use scheduled coverage for the event window. The synthetic staffing target is four people from 8:00 AM–12:00 PM and three from 12:00–2:00 PM, not a demand-optimization result.
- If the scenario fixture shows only `8,000 mL` whole milk and `4,000 mL` oat milk available at `as_of`, propose one synthetic supplier decision: order `4,000 mL` whole milk and `2,000 mL` oat milk. Request delivery for 6:00 AM PST and model a simulated supplier confirmation for 6:30 AM PST on November 1.

### Owner-facing final plan

The final plan should show:

1. The synthetic demand assumption and its 10% buffer.
2. Ingredient readiness as **ready**, **short**, **unknown**, or **inconsistent**, with the count anchor, included movements, and recipe basis visible.
3. A proposed milk order with requested and simulated confirmed delivery times, clearly pending owner approval before any external action.
4. Proposed additional coverage: one barista from 7:30 AM–2:30 PM PST and one cashier from 8:00 AM–1:00 PM PST, subject to overlap and availability checks.
5. Explicit cautions that stock is an accounting estimate, staffing is schedule coverage rather than optimization, and the demand assumption is not validated forecasting.

## 2. Time semantics

### Distinct time concepts

| Concept | Meaning | Rule |
|---|---|---|
| Actual system time | When CaféPilot is executing | Continues to drive audit timestamps, future-time validation, and operational writes. It is never replaced by the scenario clock. |
| Scenario observation time (`as_of`) | The decision-maker's knowledge cutoff | Passed explicitly to read-only scenario calculations. For this scenario it is October 29 at 6:00 PM PDT. |
| Business event time (`occurred_at`) | When an inventory movement physically happened | Determines whether a receipt, waste event, adjustment, or sale usage belongs before or after a stock anchor. |
| Sale time (`sold_at`) | When a sale occurred | Selects the recipe version and places the sale in event-time reports. |
| Physical observation time (`counted_at`) | When stock was physically counted | Selects the latest per-ingredient stock anchor. |
| Database recording time (`recorded_at`) | When a fact became known to the database | Prevents late-entered facts from appearing in a reconstruction of what was known at an earlier `as_of`. |
| Target event time | The future window being prepared for | Drives recipe versions, projected usage, and staffing coverage; it is not an audit timestamp. |
| Requested delivery time | The café's preferred arrival time | Remains a request until a supplier response exists. |
| Confirmed delivery time | The time returned by a supplier or simulator | Must retain its source and confirmation status; it must not overwrite the requested time. |

### Recommended scenario context

Use an explicit, immutable, read-only context such as:

```text
scenario_id = event-surge-2026-11-01
cafe_id = CAFE001
as_of = 2026-10-29T18:00:00-07:00
target_start = 2026-11-01T08:00:00-08:00
target_end = 2026-11-01T14:00:00-08:00
```

The context belongs to the planning request, not global process state. Existing write APIs must continue using actual system time and current timestamp validation. A deterministic opt-in seed may later create explicitly synthetic fixtures even when their dates differ from the runtime clock; that is not a reason to relax operational API validation.

For responses, retain both `as_of` and the actual evaluation timestamp. This distinguishes the business cutoff from when the calculation was run.

### Historical `as_of` behavior

A knowledge-at-time query should include a fact only when both conditions hold:

1. Its business event or observation time is at or before `as_of`.
2. Its `recorded_at` is at or before `as_of`.

For inventory, choose the latest eligible verified count line per ingredient by `counted_at`, using the existing deterministic tie-breakers. Include movements only when `counted_at < occurred_at <= as_of` and `recorded_at <= as_of`.

- A count observed before `as_of` but recorded afterward is excluded until a later `as_of`.
- A sale made before `as_of` but recorded afterward is excluded from a knowledge-at-time report until it was recorded. Ordinary event-time sales reports may continue to filter by `sold_at` only; the two report meanings must be named distinctly.
- A receipt that occurred before `as_of` but was entered afterward is excluded from the earlier stock reconstruction and appears in later runs without changing `occurred_at`.
- A late-recorded event before a newer physical count remains excluded from movements after that newer count, even after it becomes visible.

The current schema does not record when a draft count became verified, when a shift was cancelled, or when another mutable status changed. Exact historical reconstruction across those transitions is therefore unavailable. The deterministic scenario must avoid post-`as_of` status changes to seeded facts. Add status-history fields only if a later requirement justifies a migration.

### November 1 daylight-saving transition

Los Angeles changes from PDT (`UTC-07:00`) to PST (`UTC-08:00`) on November 1, 2026. Local times from 1:00 AM through 1:59 AM occur twice:

- `2026-11-01T01:30:00-07:00` is `08:30Z`.
- `2026-11-01T01:30:00-08:00` is `09:30Z`.

Every scenario timestamp must use an explicit offset, and user-entered ambiguous times must require a choice. The event and proposed delivery occur after the transition and use `-08:00`. Staffing coverage should continue iterating absolute UTC hours and render café-local labels with offsets, so both repeated hours remain distinct when a requested range spans the transition.

## 3. Scenario data strategy

### Options

| Strategy | Advantages | Risks |
|---|---|---|
| Preserve January 2025 and add a namespaced October/November 2026 dataset in `CAFE001` | Matches the hero café, preserves existing screenshots and historical reports, and supports one integrated demonstration | Latest-stock views will intentionally move to the scenario period; reset discipline and clear labels are required |
| Replace the January 2025 demo | Keeps one timeline | Breaks existing tests, documentation, screenshots, and known demo counts; removes a useful regression fixture |
| Use another café or database | Strongest runtime isolation | Duplicates catalog setup, weakens the canonical `CAFE001` story, and increases demo setup; best reserved for automated or destructive tests |

### Recommendation

Preserve the January 2025 data and add a separately identified, opt-in scenario dataset to `CAFE001`. Namespace every deterministic identifier and source identity with `AF_EVENT_SURGE_20261101_`. Automated tests should still use rollback-isolated test cafés or a disposable database.

Do not derive physical quantities from `data/inventory.json`; its quantity-like source values are not verified observations. Future scenario counts must be clearly fictional, explicit, synthetic observations.

### Minimum Event Surge dataset

- One scenario definition containing `scenario_id`, `as_of`, target window, demand mix, buffer, and staffing thresholds.
- Verified synthetic count lines for only the ingredients needed by the demand mix, plus one intentionally unknown ingredient and one inconsistent example if the owner experience needs it.
- A small set of deterministic receipts, waste, adjustments, and qualifying POS sales around the cutoff, including one late-recorded fact for `as_of` tests.
- Existing product variations, recipes, modifier effects, inventory catalog, and café timezone reused unchanged.
- Existing employees reused where practical, with only the minimum event-day shifts needed to demonstrate adequate and understaffed hours.
- One simulated supplier availability response and one proposed order identity; no real external call and no order dispatch without explicit approval.

Large synthetic sales volumes are unnecessary because incremental demand is a declared scenario assumption. Add measured sales volume only for a separately scoped forecasting experiment with evaluation criteria.

### Smaller Catering readiness flow

Reuse the same catalog and readiness calculation with one synthetic request, for example 25 regular vanilla lattes with 10 oat substitutions for a specified pickup time. It needs only the request mix, relevant stock anchors, current movements, recipe projection, and coverage for the preparation window. It does not require persisted sales, a second week of synthetic history, or a supplier order unless the readiness result shows a shortage.

## 4. API impact

### Reuse unchanged

- `GET /api/v1/inventory/items` for the active ingredient catalog; use `include_inactive=true` only when explicitly needed.
- `GET /api/v1/inventory/counts` and count detail for source evidence.
- `GET /api/v1/inventory/movements` for event history.
- `GET /api/v1/sales/products`, `GET /api/v1/sales`, and `GET /api/v1/sales/reports/summary` for catalog and event-time history.
- `GET /api/v1/sales/{sale_id}/ingredient-usage` for persisted sale usage and whether movements were posted.
- `GET /api/v1/staffing/employees`, `GET /api/v1/staffing/shifts`, and `GET /api/v1/staffing/reports/coverage` for current schedule and café-local hourly coverage.

### Necessary additions

1. Add an optional timezone-aware `as_of` parameter to both Inventory stock-estimate endpoints. Omission preserves today's behavior. When supplied, it controls count and movement eligibility and applies the `recorded_at` knowledge cutoff.
2. Add a read-only recipe projection operation for hypothetical product variations and modifiers at a target time. A calculation-style `POST` is acceptable if it performs no write and returns ingredient quantities, recipe effective times, missing mappings, and estimated-data labels.
3. Compose inventory readiness and staffing coverage in a deterministic planning service. It may call existing domain services; a dashboard-specific database table is not required.

### Future enhancements, not prerequisites

- Historical status audit for stock-count verification, shift cancellation, and sale voiding.
- Supplier catalog, lead-time, order, and confirmation persistence.
- Forecast training, confidence intervals, optimization, and AI-generated recommendations.
- Global simulated time or changes to current write validation and audit fields.

## 5. Safety and repeatability

- **Reset:** Provide a dry-run-first reset that deletes only exact IDs owned by `AF_EVENT_SURGE_20261101_`, in foreign-key-safe order. It must refuse unrecognized or non-scenario records and never touch the January 2025 namespace.
- **Determinism:** Fix IDs, timestamps with offsets, Decimal quantities, product mix, staffing thresholds, and simulated supplier responses in version-controlled fixture data.
- **Isolation:** Validate all prerequisites before writing and use one transaction. Tests use rollback-isolated cafés; the opt-in local fixture is never loaded at application startup.
- **Idempotency:** A second seed or write with the same identity and data is a no-op or replay. Reusing the identity with different business data is a conflict, never an overwrite.
- **External safety:** Default to an explicitly labeled supplier simulator. Real integrations require separate credentials, an allowlisted environment, owner approval, and an idempotency key. A simulated confirmation must never be represented as a real commitment.
- **Repeatable demonstration:** Start from the preserved January dataset, seed the namespaced scenario once, request the plan with the fixed `as_of`, verify the expected readiness and coverage outputs, demonstrate the pending approval boundary, then optionally run the scoped reset and confirm the second seed is identical.

## 6. Implementation breakdown

### Task 1 — Time-aware Inventory reads

- **Likely files:** Inventory `repository.py`, `service.py`, `schemas.py`, `router.py`, and stock-estimate tests.
- **Migration:** No.
- **Tests and acceptance:** Default requests remain unchanged; explicit `as_of` handles event and recording cutoffs, late records, per-ingredient anchors, deterministic ties, unknown/inconsistent quantities, and the DST offset boundary.
- **Compatibility and preservation:** Optional parameters only; no writes, no record changes, and existing current-stock clients continue working.

### Task 2 — Deterministic scenario fixture and scoped reset

- **Likely files:** a new opt-in script and focused tests under `business-core/scripts/` and `business-core/tests/`, plus the scenario reference if values change during review.
- **Migration:** No.
- **Tests and acceptance:** Validate all inputs before one transaction; first seed creates exact expected rows; second seed creates zero; conflicts roll back; reset affects only the scenario namespace; January 2025 counts remain unchanged.
- **Compatibility and preservation:** Reuse current models and catalog. Do not run on startup, replace existing history, or infer counts from source JSON.

### Task 3 — Deterministic Event Surge readiness service

- **Likely files:** a small planning module, recipe-projection reuse in Sales, API schemas/routes, and cross-domain tests.
- **Migration:** No for an ephemeral read-only plan and simulated supplier response.
- **Tests and acceptance:** Recipe quantities and modifiers aggregate with Decimal math; unknown/inconsistent stock blocks a ready result; coverage uses offset-aware hours; supplier output is simulated and pending approval; repeated requests are deterministic and write nothing.
- **Compatibility and preservation:** Keep Inventory, Sales, and Staffing contracts intact except for explicitly additive read-only operations. AI orchestration and real supplier actions remain later milestones.

## 7. Current documentation corrections

- Qualifying new `source="pos"` sales with complete recipe mappings now create estimated `SALE_USAGE` movements atomically with the sale. The January 2025 generated demo sales were not retroactively deducted.
- The Dashboard Ingredient Catalog KPI calls `GET /api/v1/inventory/items` without `include_inactive=true`; it therefore counts active ingredients returned by the default API behavior, not active and inactive records together.

## Open risks

- The demand and supplier values are synthetic assumptions, not a validated forecast or commercial commitment.
- Historical status transitions cannot be reconstructed exactly with the current count, shift, and sale status fields.
- Seeding the scenario into `CAFE001` intentionally changes its latest-stock timeline until the scoped reset runs.
- No supplier-order model or real integration exists; the first workflow must remain simulation and approval only.
- The repeated 1:00 AM hour on November 1 is ambiguous without an explicit offset.
