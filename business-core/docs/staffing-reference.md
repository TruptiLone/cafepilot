# CaféPilot Business Core — Staffing Reference

## 1. Database schema

| Table | Columns |
|---|---|
| `employees` | `employee_id` (PK), `cafe_id` (FK), `name`, `role`, `active`, `created_at` |
| `shifts` | `shift_id` (PK), `cafe_id` (FK), `employee_id` (FK), `start_at`, `end_at`, `status`, `created_at` |

### How it works

- A café owns employees and shifts. A composite foreign key ensures every shift references an employee from the same café.
- Scheduled shifts use timezone-aware timestamps and must end after they start.
- A PostgreSQL exclusion constraint prevents overlapping scheduled shifts for one employee. Cancelled shifts remain as history and do not block scheduling or count toward coverage.

## 2. Staffing APIs

Base URL (local Business Core): `http://127.0.0.1:8001`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/staffing/employees` | Create an internal/demo employee |
| `GET` | `/api/v1/staffing/employees` | List café employees |
| `GET` | `/api/v1/staffing/employees/{employee_id}` | Get an employee |
| `POST` | `/api/v1/staffing/shifts` | Schedule a non-overlapping employee shift |
| `GET` | `/api/v1/staffing/shifts` | List shifts overlapping a date range |
| `POST` | `/api/v1/staffing/shifts/{shift_id}/cancel` | Cancel a scheduled shift without deleting history |
| `GET` | `/api/v1/staffing/reports/coverage` | Report hourly staffing coverage in café-local time |
