# CaféPilot Phase 1 — Business Core Progress

_Status recorded: October 28, 2026_

## Project overview

CaféPilot Business Core is the operational management application within the existing CaféPilot repository. It provides independent Inventory, Sales, and Staffing backends plus a React management interface for café operators. The current local workspace uses café `CAFE001` and intentionally keeps Business Core separate from Application 1 in the repository root.

## Architecture

- **Backend:** A standalone FastAPI application on port `8001` with versioned Inventory, Sales, and Staffing routers.
- **Modules:** Each business area separates SQLAlchemy models, PostgreSQL repositories, business services, Pydantic schemas, and FastAPI routes.
- **Data:** PostgreSQL is the source of truth. SQLAlchemy uses synchronous sessions, Alembic owns schema migrations, and the local Docker database is exposed on port `5433`.
- **Frontend:** A Vite React/TypeScript application on port `5173` uses React Router, reusable shell and modal components, café-time utilities, and a development proxy from `/api` to Business Core.
- **Isolation:** Application 1 remains independent; Business Core code and infrastructure live under `business-core/`.

## Completion status

| Area | Backend | Frontend |
|---|---|---|
| Inventory | Completed: catalog, physical counts, append-only movements, and read-only stock estimates | Completed MVP: four data views, movement entry, draft count creation, and count verification |
| Sales | Completed: products, completed sales, sale history, and summary reporting | Placeholder only |
| Staffing | Completed: employees, non-overlapping shifts, cancellation, and hourly coverage | Placeholder only |
| Dashboard | Backend data is available through module APIs | Application shell placeholder only |

The Sales and Staffing rows describe completed backend functionality, not completed operator screens.

## Inventory frontend workflows

The Inventory screen reads live Business Core API responses for `CAFE001` and provides:

- Ingredient catalog, stock-count history and details, stock movements, and current-stock estimates.
- Manual Receipt, Waste, and Adjustment entry with backend validation and stable retry identity.
- Multi-ingredient physical counts saved as synthetic drafts without invented quantities or timestamps.
- Explicit verification of draft counts using an operator-provided, timezone-aware observation time.
- Loading, empty, API-error, validation, success, and refresh-warning states.

### Application shell

![Dashboard application shell](screenshots/dashboard-shell.jpg)

### Inventory data views

![Inventory ingredients](screenshots/inventory-ingredients.jpg)

![Inventory stock counts](screenshots/inventory-stock-counts.jpg)

![Inventory stock movements](screenshots/inventory-stock-movements.jpg)

![Inventory current stock](screenshots/inventory-current-stock.jpg)

### Inventory entry forms

![New inventory movement form](screenshots/inventory-new-movement.jpg)

![New physical stock count form](screenshots/inventory-new-stock-count.jpg)

All screenshots show the actual local Vite application. No operational record was submitted to capture them.

## Synthetic demo dataset

The opt-in demo dataset represents operations for `CAFE001` from **January 6–12, 2025** in `America/Los_Angeles`. It is historical synthetic data, not today's activity.

| Table | Current rows |
|---|---:|
| `cafes` | 1 |
| `inventory_items` | 19 |
| `stock_counts` | 2 |
| `stock_count_lines` | 23 |
| `inventory_movements` | 12 |
| `menu_products` | 18 |
| `sales` | 126 |
| `sale_items` | 210 |
| `employees` | 7 |
| `shifts` | 35 |

The dataset includes a partial count, an ingredient with unknown stock, realistic sales variation, and staffing coverage gaps. Sales do not automatically deduct inventory because that integration has not been implemented.

## Technical stack

- Python, FastAPI, Pydantic, SQLAlchemy 2.x, Psycopg 3, and Alembic.
- PostgreSQL 17 in Docker Compose.
- React 19, TypeScript 5.9, React Router 7, Vite 7, and Vitest.
- Pytest, HTTPX, Testing Library, and JSDOM.

## Verification

The following checks were **rerun for UI-02D** against the local repository and development database:

| Check | Result |
|---|---|
| Business Core tests | `114 passed` with one Starlette `TestClient` deprecation warning |
| Frontend tests | `17 passed` across 2 test files |
| Frontend production build | Passed; TypeScript check and Vite build completed successfully |
| Alembic current revision | `3411712f6af3 (head)` |
| Alembic drift check | `No new upgrade operations detected.` |
| Demo row-count query | Returned the ten table counts listed above |
| Screenshot review | Seven real local views captured; no records submitted |

## Known limitations

- Authentication and authorization are not implemented; write workflows are internal/local-demo only.
- Sales, Staffing, and Dashboard frontend screens remain placeholders.
- Inventory estimates are accounting estimates, not proof that stock is physically usable or reserved.
- Sales-to-Inventory deduction, demand forecasting, and automated staffing optimization are not implemented.
- Draft stock-count creation has no client-provided idempotency key. If a network response is uncertain, the backend may have saved the draft even though the browser did not receive confirmation. The UI now warns operators to check Stock Counts history before retrying; a future backend contract can provide stronger request-level idempotency.
- AI and MCP integrations have not started.

## Next milestones

1. Sales UI.
2. Staffing UI.
3. Dashboard.
4. AI integration.

## References

- [Inventory schema and API reference](../inventory-reference.md)
- [Sales schema and API reference](../sales-reference.md)
- [Staffing schema and API reference](../staffing-reference.md)
