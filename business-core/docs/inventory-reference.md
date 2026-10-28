# CaféPilot Business Core — Inventory Reference

> Living reference for Inventory V1. Update as features are reviewed and committed.

## 1. Database schema

| Table | Columns |
|---|---|
| `cafes` | `cafe_id` (PK), `name`, `timezone` |
| `inventory_items` | `ingredient_id` (PK), `cafe_id` (FK), `name`, `category`, `base_unit`, `reorder_point`, `par_level`, `is_perishable`, `active` |
| `stock_counts` | `count_id` (PK), `cafe_id` (FK), `counted_at`, `recorded_at`, `counted_by`, `status`, `is_synthetic` |
| `stock_count_lines` | `count_line_id` (PK), `count_id` (FK), `cafe_id`, `ingredient_id` (FK), `counted_quantity` |
| `inventory_movements` | `movement_id` (PK), `cafe_id` (FK), `ingredient_id` (FK), `movement_type`, `quantity_delta`, `is_estimated`, `source_type`, `source_id`, `occurred_at`, `recorded_at`, `recorded_by`, `reason` |

### How it works

- A **café** owns its ingredient catalog, physical stock counts, and stock movements. Composite foreign keys ensure count lines and movements reference ingredients from the same café.
- A **stock count** is an observed snapshot: `stock_counts` records the count session; `stock_count_lines` records quantities for individual ingredients. Only verified counts with a known `counted_at` can anchor stock estimates. Missing count lines do **not** mean zero stock.
- An **inventory movement** records a signed change: `RECEIPT` (+), `WASTE` (−), `SALE_USAGE` (−), or `ADJUSTMENT` (+/−). `(ingredient_id, source_type, source_id)` prevents duplicate source events per ingredient. Counts do not create movements.
- **Stock-estimate calculation:** latest eligible verified count for each ingredient + eligible movements after that observation. Without a valid count, quantity is **unknown**, not zero. `inventory_items` stores definitions, **not** current balances.
- Stock endpoints accept an optional timezone-aware `as_of`. When present, counts require both `counted_at <= as_of` and `recorded_at <= as_of`; movements require both `occurred_at <= as_of` and `recorded_at <= as_of`. This excludes facts that were not yet known at the requested time.
- Responses return the effective `as_of` separately from `calculated_at`, the actual evaluation time. Omitting `as_of` preserves the latest-known estimate using the current system time.
- Historical estimates use the count's current `verified` status because the schema does not store a separate verification timestamp. They are accounting reconstructions, not proof that stock was physically usable or reserved at that time.

## 2. Inventory APIs

Base URL (local Business Core): `http://127.0.0.1:8001`

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/v1/inventory/health` | Check Inventory module health | Completed |
| `GET` | `/api/v1/inventory/items` | List active inventory items; filter by café or category, or include inactive items | Completed (INV-01) |
| `GET` | `/api/v1/inventory/items/{ingredient_id}` | Get a single ingredient definition | Completed (INV-01) |
| `POST` | `/api/v1/inventory/counts` | Create a draft or verified physical stock count | Completed (INV-02) |
| `GET` | `/api/v1/inventory/counts` | List physical stock-count sessions | Completed (INV-02) |
| `GET` | `/api/v1/inventory/counts/{count_id}` | Get a count and its ingredient lines | Completed (INV-02) |
| `POST` | `/api/v1/inventory/counts/{count_id}/verify` | Verify a draft count with an explicit observation time | Completed (INV-02) |
| `POST` | `/api/v1/inventory/movements` | Record an accepted delivery, waste, or adjustment | Completed (INV-03) |
| `GET` | `/api/v1/inventory/movements` | List inventory movements | Completed (INV-03) |
| `GET` | `/api/v1/inventory/stock` | List current or historical read-only estimates; optional timezone-aware `as_of` | Completed (AF-02) |
| `GET` | `/api/v1/inventory/stock/{ingredient_id}` | Get one current or historical estimate; optional timezone-aware `as_of` | Completed (AF-02) |

**Next update:** Add future inventory APIs as milestones are designed and reviewed.
