# CaféPilot Business Management UI

The frontend provides the responsive CaféPilot application shell. Inventory is
connected to the Business Core API; Dashboard, Sales, and Staffing remain
placeholder routes for later UI milestones.

Start Business Core on port `8001`, then run:

```bash
npm install
npm run dev
```

The development server runs at `http://127.0.0.1:5173` and proxies `/api`
requests to `http://127.0.0.1:8001`.

```bash
npm test
npm run build
```
