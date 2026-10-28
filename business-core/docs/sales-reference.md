# CaféPilot Business Core — Sales Reference

## 1. Database schema

| Table | Columns |
|---|---|
| `menu_products` | `product_id` (PK), `cafe_id` (FK), `name`, `category`, `price_cents`, `active` |
| `sales` | `sale_id` (PK), `cafe_id` (FK), `source_id`, `sold_at`, `recorded_at`, `status`, `total_cents` |
| `sale_items` | `sale_item_id` (PK), `sale_id` (FK), `cafe_id`, `product_id` (FK), `quantity`, `unit_price_cents`, `line_total_cents` |

### How it works

- A café owns menu products and sales. Composite foreign keys ensure every sale item references a sale and product from the same café.
- Prices and totals use integer cents. Sale items retain the product price used when the completed sale was recorded.
- `(cafe_id, source_id)` prevents duplicate source sales. Completed sales are immutable through the MVP API.

## 2. Sales APIs

Base URL (local Business Core): `http://127.0.0.1:8001`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/sales/products` | List active menu products for a café |
| `POST` | `/api/v1/sales` | Record an internal/demo completed sale using server-side prices |
| `GET` | `/api/v1/sales` | List completed sales with optional date filters |
| `GET` | `/api/v1/sales/{sale_id}` | Get a completed sale and its historical line prices |
| `GET` | `/api/v1/sales/reports/summary` | Summarize revenue, sales, units, top products, and hourly demand |
