# CaféPilot — Sales V2 Design (design only)

**Status:** Proposed, awaiting approval · **Date:** 2026-10-28 · **Deadline:** 2026-11-04 · **Implementation budget:** approximately two working days

**Scope:** Extend the existing Business Core Sales V1 in place. This document proposes schemas, APIs, migrations, tests, and importer contracts; it creates **no** migrations, models, routes, or application implementation. PostgreSQL target is **17**, not the PostgreSQL 18 used in the Perplexity example.

## 0. Evidence, assumptions, and non-goals

### Repository sources inspected

- **business-core/backend/modules/sales/{models,schemas,router,repository,service}.py**, **business-core/docs/sales-reference.md**, **business-core/alembic/versions/76fdeb7269e5_create_sales_schema.py**, **business-core/tests/test_sales_api.py**, **business-core/scripts/seed_sales.py**, **business-core/scripts/seed_demo_operations.py**.
- **business-core/backend/modules/inventory/{models,schemas,router,service}.py**, **business-core/docs/inventory-reference.md**, **business-core/backend/models/cafe.py**, **business-core/backend/db/session.py**, **business-core/alembic/env.py**.
- Repository-root **data/menu.json**, **data/recipes.json**, **data/inventory.json**; latest repo status documentation **business-core/docs/progress/phase-1-business-core.md**.

**Source limitation:** The three specified Perplexity SQL files were **not found in GitHub at the requested paths**, and a repository search did not locate them. Therefore §1 maps **every table, view, and rule named in the supplied Perplexity summary**, not an audited statement-by-statement comparison of those absent files. In particular, do not claim its unseen SQL has a precise trigger, index, tax formula, or column definition. The existing repository's recipes.json is the recipe authority; it uses **COFFEE_BEANS** and **WHOLE_MILK** (not alternate ingredient IDs in other research fixtures).

### Observed V1 baseline

- Tables: **menu_products** (18 seeded products), **sales** (completed|void, unique **(cafe_id,source_id)**), **sale_items** (positive quantity, price snapshot, line total, unique **(sale_id,product_id)**); all IDs TEXT, money INTEGER cents, timestamps TIMESTAMPTZ.
- API: **GET /api/v1/sales/products**, **POST /api/v1/sales**, **GET /api/v1/sales**, **GET /api/v1/sales/{sale_id}**, **GET /api/v1/sales/reports/summary**. V1 POST accepts product_id and quantity, computes prices server-side, returns 201 or 200 for an exact idempotent retry.
- Seed: CAFE001 contains **126 sales / 210 sale items** from **2025-01-06 through 2025-01-12**, created by seed_demo_operations.py, with source IDs prefixed DEMO_POS_. Its observed Sales baseline does **not** contain real payment, modifier, tax, or recipe-consumption observations.
- Inventory: stock estimate for an ingredient is **latest verified count for that ingredient by counted_at + eligible movements whose occurred_at is strictly after that count and at/before calculation time**. Without a verified count the stock quantity remains **unknown**; a negative result is **inconsistent**, never clamped. Inventory movement identity is unique **(ingredient_id,source_type,source_id)**. Ordinary inventory movement POST currently sets is_estimated=False: sale-usage posting must use the internal service/repository path, not that public POST.
- Café is already **cafes(cafe_id TEXT PK, name, timezone)**. Existing DB engine and Alembic engines do not explicitly configure session TimeZone.

### Boundary decisions

**Adopt:** product variations, milk/extras modifier groups, item snapshots, source labeling and import identity, cents-only subtotal/discount/tax arithmetic, recipe-driven estimated usage, café-local reports, synthetic history with source labeling.

**Reject in V2:** second/parallel POS schema; cafe_settings; payments; refunds; tips; open/paid lifecycle; staff_id; channel; PostgreSQL triggers; production Square integration. Retain **sales.status completed|void**. A later refund design must record **ingredients_consumed**, and refunds of prepared drinks must not reverse their ingredient usage by default.

**Definition of done (two-day core):** additive migrations + seeds/backfill + preserved V1 API tests + variation/modifier sale POST with correct pricing + atomic idempotent sale-usage posting for newly entered POS sales + basic usage/hourly/modifier reports. The 12-month importer and extended frontend can be postponed without invalidating the core.

## 1. Perplexity crosswalk — adopt / adapt / reject

This matrix exhausts the **named** twelve Perplexity tables, six views, and reported column/constraint/trigger groups in the prompt. Exact SQL details remain **unverified** until the files are supplied.

| Perplexity element / named column group | Decision | V2 equivalent or one-line reason |
|---|---|---|
| cafe_settings (timezone, tax_rate) | **Reject table; adapt fields** | Existing cafes has timezone; add nullable tax_rate_bps there, no competing settings row. |
| product (id, name, category, active) | **Adapt** | Keep menu_products IDs and existing categories; never create a second catalog. |
| product_variation (product, size, price, active) | **Adopt** | New café-owned product_variations referencing menu_products. |
| modifier_group (Milk/Extras, requiredness and selection limits) | **Adopt** | New modifier_groups with single|multiple and min/max selection rules. |
| modifier (group, price, default) | **Adopt** | New modifiers with prices and one default Whole milk for Milk. |
| product_modifier_group (catalog applicability) | **Adopt** | New composite café-safe join table. |
| sales_order (status, source, timestamps, totals) | **Adapt** | Extend sales; retain completed|void and existing sale_id/source_id; add source, external_id, subtotal, discount, tax. |
| order_line (variation, quantity, historical price/name) | **Adapt** | Extend sale_items with variation link and item snapshots; do not create order_line. |
| order_line_modifier (modifier/price/quantity snapshots) | **Adopt under existing names** | New sale_item_modifiers. |
| order_discount (promos, comps, reason) | **Defer** | One order-level discount_cents only; promotion/comps accounting and reason table not required. |
| payment (method, paid amount/time) | **Reject** | No payment capture, settlement or tender reconciliation in this deadline. |
| refund (amount, tax/tip split, prepared flag) | **Defer** | Design note only: future refunds record revenue/tax splits and ingredients_consumed, never mutate original paid sale. |
| v_order_history | **Adapt to service query** | Existing sale list/detail APIs; no redundant view. |
| v_line_sales | **Adapt to query** | Café-scoped item, variation and modifier join with timezone conversion. |
| v_daily_revenue | **Adapt to query** | Aggregate subtotal, discount, tax, total; no refund column until refunds exist. |
| v_hourly_demand | **Adopt as query** | Group by café-local date/hour and variation, not server timezone. |
| v_modifier_mix | **Adopt as query** | Explicit and inferred/default milk selections reported separately. |
| v_order_integrity_issues | **Adopt as query / optional read-only view** | Compare stored subtotal with sum of line_total; do not hide mismatches. |
| Product active / hide-not-delete | **Adopt** | menu_products.active and product_variations.active; historical sale FK/snapshots retained. |
| Names and prices at checkout (immutable historical snapshots) | **Adopt** | Sale lines and modifiers retain sold-time product/variation/modifier descriptions and cents. |
| total=subtotal−discount+tax | **Adopt** | CHECK on sales plus service calculation; cross-table subtotal check remains an integrity query, not a CHECK. |
| Refund ≤ payment; refund tax/tip split | **Reject for now** | No payment/refund schema; avoid implying enforcement exists. |
| Duplicate import protection | **Adopt / strengthen** | Preserve (cafe_id,source_id) and add unique partial (cafe_id,source,external_id) for non-null external_id. |
| Comp reason required | **Defer** | No comp subtype in initial V2; discount constrained, attribution later. |
| Paid-order locking and prohibited deletion | **Adapt without triggers** | API remains append-only completed sales, no item-update/delete endpoints; DB permissions/security later. |
| Perplexity database locking/total/refund triggers | **Reject** | Service transactions and ordinary CHECK/UNIQUE/FK; no PL/pgSQL triggers. |
| DB-level wrong-total test (reported T3) | **Adopt** | CHECK of same-row sales totals and sale_items line totals; use integrity query for sum-of-lines. |
| Duplicate-import test (reported T5) | **Adopt** | Retry + conflicting payload + multi-source external_id uniqueness pytest. |
| Integrity-view test (reported T10) | **Adopt** | Service/query flags deliberately corrupted/mismatched header subtotal; never silently adjusts. |
| 12-month synthetic orders, event demand | **Adopt as deferred generator** | Reuse CAFE001, source=generated, no inventory ledger writes. |

**Known V1 constraint to replace:** sale_items currently enforces **line_total_cents = quantity × unit_price_cents** and unique **(sale_id,product_id)**. Both block modifier pricing and multiple variations of one product; change these in a deliberate migration. Keep the existing same-café composite FKs.

## 2. Schema & phased migrations

Notation: **TEXT**=PostgreSQL text, **INT**=INTEGER cents/count, **NUMERIC(18,3)** for ingredient quantities, **TZ**=TIMESTAMPTZ, **BOOL**=BOOLEAN. All new primary keys are TEXT and every new table has **cafe_id TEXT NOT NULL FK cafes(cafe_id)**. Every cross-table ownership relationship uses composite FK including cafe_id; referenced parents need unique (id,cafe_id). UUID-looking TEXT generated by service, deterministic TEXT IDs for seeded catalog. Names below are designs, not assigned Alembic revision hashes.

### M1 — Catalog and modifier vocabulary (new tables; seed and backfill)

| Table | Columns including constraints | Keys, indexes, ownership |
|---|---|---|
| **product_variations** | variation_id TEXT PK; cafe_id TEXT NOT NULL; product_id TEXT NOT NULL; code TEXT NOT NULL (REG, LRG); name TEXT NOT NULL (Regular 12 oz, Large 16 oz, Regular for bakery); size_oz INT NULL CHECK >0; price_cents INT NOT NULL CHECK >=0; active BOOL NOT NULL DEFAULT TRUE | FK (product_id,cafe_id)→menu_products(product_id,cafe_id); UNIQUE (variation_id,cafe_id); UNIQUE (cafe_id,product_id,code); INDEX (cafe_id,product_id,active) |
| **modifier_groups** | group_id TEXT PK; cafe_id TEXT NOT NULL; name TEXT NOT NULL; selection_type TEXT NOT NULL CHECK IN ('single','multiple'); min_select INT NOT NULL DEFAULT 0 CHECK >=0; max_select INT NOT NULL CHECK >=min_select (also max_select>=1); active BOOL NOT NULL DEFAULT TRUE | UNIQUE (group_id,cafe_id); UNIQUE (cafe_id,name); INDEX (cafe_id,active) |
| **modifiers** | modifier_id TEXT PK; cafe_id TEXT NOT NULL; group_id TEXT NOT NULL; name TEXT NOT NULL; price_cents INT NOT NULL CHECK >=0; is_default BOOL NOT NULL DEFAULT FALSE; active BOOL NOT NULL DEFAULT TRUE | FK (group_id,cafe_id)→modifier_groups; UNIQUE (modifier_id,cafe_id); UNIQUE (cafe_id,group_id,name); INDEX (cafe_id,group_id,active); optional partial UNIQUE (group_id) WHERE is_default for single-default policy, service validates |
| **product_modifier_groups** | product_modifier_group_id TEXT PK; cafe_id TEXT NOT NULL; product_id TEXT NOT NULL; group_id TEXT NOT NULL; active BOOL NOT NULL DEFAULT TRUE | Composite FKs to menu_products and modifier_groups; UNIQUE (cafe_id,product_id,group_id); INDEX (cafe_id,group_id) |

Catalog data: all 18 existing menu products keep IDs **COF001…COF008, DRK001…DRK004, BAK001…BAK006** and categories exactly as data/menu.json (coffee, other_drinks, bakery). For drinks: **_REG** price equals menu_products.price_cents; **_LRG** price is regular +75 cents per menu.json, except explicit opt-out if product has no large recipe. For bakery: only **_REG**, label "Regular" (recipe portions.each), no size_oz. Expected seed: **12 drinks × 2 + 6 bakery × 1 = 30 variations**. Keep menu_products.price_cents as legacy regular price and mirror REG prices when future catalog editing is introduced; V2 price authority is variation.price_cents.

Initial groups: **Milk** (single, min=1, max=1; Whole 0 default, Skim 0, Oat +75, Almond +75), **Extras** (multiple, min=0, max=3; Extra shot +100, Vanilla +50, Caramel +50; syrup prices are a design choice, not stated in menu.json). Each modifier may be selected once per line in V2; extra-shot multiplicity is a later feature. Apply Milk to menu products whose recipes contain WHOLE_MILK (**COF003–COF007, DRK001–DRK003**); apply Extra shot to espresso products (**COF002–COF007**); Vanilla/Caramel extras only where recipe+applicability policy allows (recommend coffee milk beverages COF003–COF007, not bakery). To avoid offering inapplicable Extras to other products, use appropriate group applicability and validate modifier-specific restrictions in service; preferred simple default: Extras group applies COF002–COF007, but syrup modifiers are rejected for COF002 until catalog policy is refined. If that split complicates implementation, restrict Extras group to COF003–COF007 and make COF002 extra-shot unavailable in V2 initial UI. This tradeoff is explicit rather than silently permitting invalid combinations.

### M2 — Existing sales header/line extensions and modifier snapshots

| Existing/new table | Column and migration behavior | Constraints and indexes |
|---|---|---|
| **sales** | source TEXT NOT NULL DEFAULT 'pos' CHECK IN ('pos','generated','csv_import'); external_id TEXT NULL; subtotal_cents INT NOT NULL DEFAULT 0 (backfill from total_cents); discount_cents INT NOT NULL DEFAULT 0; tax_cents INT NOT NULL DEFAULT 0 | CHECK subtotal>=0, discount BETWEEN 0 AND subtotal, tax>=0, total_cents = subtotal_cents − discount_cents + tax_cents; unique partial INDEX (cafe_id,source,external_id) WHERE external_id IS NOT NULL; **retain** UNIQUE (cafe_id,source_id) |
| **sale_items** | variation_id TEXT NULL during backfill then NOT NULL; product_name TEXT NULL→NOT NULL; variation_name TEXT NULL→NOT NULL; modifier_unit_cents INT NOT NULL DEFAULT 0; retain quantity, product_id, unit_price_cents, line_total_cents | FK (variation_id,cafe_id)→product_variations; CHECK modifier_unit_cents>=0; **replace** old line-total CHECK with line_total_cents = quantity × (unit_price_cents + modifier_unit_cents); drop UNIQUE(sale_id,product_id), add INDEX(cafe_id,sale_id,variation_id); CHECK amount fits INT enforced in service |
| **sale_item_modifiers** (new) | sale_item_modifier_id TEXT PK; cafe_id TEXT NOT NULL; sale_item_id TEXT NOT NULL; modifier_id TEXT NOT NULL; modifier_name TEXT NOT NULL; price_cents INT NOT NULL CHECK >=0 (sold-time per-modifier price); quantity INT NOT NULL DEFAULT 1 CHECK >0 (per sold unit) | FK (sale_item_id,cafe_id)→sale_items(sale_item_id,cafe_id) **requires adding UNIQUE(sale_item_id,cafe_id)**; FK (modifier_id,cafe_id)→modifiers(modifier_id,cafe_id); UNIQUE(cafe_id,sale_item_id,modifier_id); INDEX(cafe_id,sale_item_id) |
| **cafes** | tax_rate_bps INT NULL, recommended DEFAULT 0 (0 means zero tax in demo); CHECK 0<=tax_rate_bps<=10000 | No new table; tax configured per café. Rate is snapshotted indirectly by stored tax_cents; future rate changes do not rewrite orders. |

Backfill in M2, transactionally:
1. Populate variations first (M1); for each existing sale_item set variation_id = product_id || '_REG', product_name from current menu product, variation_name = 'Regular' (or approved catalog name), modifier_unit_cents=0. Never change historical unit_price_cents or line_total_cents even if catalog prices differ.
2. Fill sales.subtotal_cents=total_cents, discount=tax=0. Tag **DEMO_SALE_** / **DEMO_POS_** historical fixtures as **source='generated'**, external_id=source_id. Other rows remain source='pos' and external_id=NULL unless independently evidenced; do not guess an import provenance.
3. **Whole milk is the recipe default, not a proven historical customer selection.** For historical applicable _REG sale lines, absence of explicit Milk modifier means inferred Whole milk when calculating *synthetic* usage. Do **not** fabricate observed sale_item_modifiers rows for old orders. Include **milk_choice_inferred=true** in read results where relevant.
4. Verify every sale_item backfilled before enabling NOT NULL/FK constraints. Existing source_id values and 126 sales / 210 lines must remain identical; verify row counts and total sales cents before/after.

Important subtlety: existing sale item names are retrieved via join to current menu_products. V2 detail should read **sale_items.product_name** and **variation_name** snapshots so renames cannot rewrite history. Existing GET /sales/{id} and old report response fields remain present, with only additive optional fields.

### M3 — Recipe references and modifier effects

| Table | Columns | Constraints, keys, indexes |
|---|---|---|
| **recipe_lines** | recipe_line_id TEXT PK; cafe_id TEXT NOT NULL; variation_id TEXT NOT NULL; ingredient_id TEXT NOT NULL; quantity NUMERIC(18,3) NOT NULL CHECK >0; unit TEXT NOT NULL CHECK IN ('g','mL','each'); effective_from TZ NOT NULL | Composite FK (variation_id,cafe_id)→product_variations; composite FK (ingredient_id,cafe_id)→inventory_items; UNIQUE (cafe_id,variation_id,effective_from,ingredient_id); INDEX (cafe_id,variation_id,effective_from DESC) |
| **modifier_recipe_effects** | modifier_recipe_effect_id TEXT PK; cafe_id TEXT NOT NULL; modifier_id TEXT NOT NULL; effect TEXT NOT NULL CHECK IN ('replace','add'); target_ingredient_id TEXT NULL; ingredient_id TEXT NOT NULL; quantity NUMERIC(18,3) NULL; unit TEXT NULL | FK (modifier_id,cafe_id)→modifiers; two composite ingredient FKs (target_ingredient_id,cafe_id) and (ingredient_id,cafe_id)→inventory_items; CHECK replace means target non-null and quantity/unit null; add means target null and quantity>0/unit non-null; UNIQUE (cafe_id,modifier_id,effect,target_ingredient_id,ingredient_id), noting nullable target requires service duplicate guard or separate partial unique indexes; INDEX (cafe_id,modifier_id) |

**Recipe version rule:** every version of one variation comprises the *complete* ingredient set sharing one effective_from. Choose the greatest effective_from <= sale.sold_at and read only lines for that version. No eligible version = **unmapped**, never zero consumption. Seed one initial effective date **2025-01-01 00:00:00+00:00** for all current menu recipes so the January 2025 demo and Nov 2025–Oct 2026 history have a version. Match each recipe unit to inventory_items.base_unit. Keep recipe lines immutable after used for any completed sale; add later effective_from versions rather than editing old ones.

For current demo, modifier effects are constant/immutable after use (no history versions): **Whole** no effect; **Oat/Almond/Skim** REPLACE WHOLE_MILK with selected milk, using the *same exact quantity* from the base variation; **Extra shot** ADD COFFEE_BEANS 9 g (from repository data/recipes.json); **Vanilla** ADD VANILLA_SYRUP 15 mL; **Caramel** ADD CARAMEL_SYRUP 15 mL (syrup effect quantities are *proposed*, not supplied by recipe_rules; validate before release). The 15 mL extra is per sale unit, including for a large latte; any size scaling is deferred. Extra shot follows the base recipe even if it already includes coffee beans. Replacement with no matching WHOLE_MILK line must fail for a V2 POS sale.

**Do not add SALE_USAGE by changing Inventory's public POST endpoint**; insert InventoryMovement internally under the same DB transaction as the Sales service. Units stay in the inventory base unit. A future fourth migration is unnecessary if M3 creates no Inventory schema changes.

### Minimal rollout / rollback safety

M1 can be upgraded and rolled back without touching V1 sales. M2 is additive/backfilled in stages; its rollback must not drop or destroy previously existing sales. M3 seeds recipes after schema creation. Backfills/seeds are **idempotent**, explicit commands or guarded migration data transforms, and run before new app code requires NOT NULLs. Run Alembic upgrade + downgrade on a disposable DB snapshot, verify no drift, and preserve user data. The current Alembic head is **3411712f6af3**; M1 descends from that revision, M2 from M1, M3 from M2.

## 3. Compatibility and source semantics

### Existing POST stays valid

~~~json
{
  "cafe_id": "CAFE001",
  "source_id": "POS_LEGACY_001",
  "sold_at": "2026-10-28T09:15:00-07:00",
  "items": [{"product_id": "COF003", "quantity": 2}]
}
~~~

Interpret old-style POST as completed **source=pos**, **variation COF003_REG**, implicit Whole milk for a milk-applicable item, no paid modifiers, discount 0 and tax 0 for strict backward pricing compatibility. It must still return 201 on create, 200 on equal retry, 409 if source_id matches with changed sold_at/items, and remain unchanged to existing callers. The V1 response keys (sale_id, product_id, product_name, quantity, unit_price_cents, line_total_cents, total_cents) stay intact. A V1 source_id must not be silently re-keyed to a different source or external_id.

Existing sale_items unique(sale_id,product_id) is removed for V2 so one sale can contain two different sizes/configurations. For V1 requests, retain explicit duplicate product_id rejection by schema/service (matching existing tests). For V2, two entries of the same variation with different modifiers are permitted; each gets its own sale_item_id.

**Unmapped legacy catalog product:** existing tests create arbitrary products without recipes. To protect the V1 contract, a legacy POST is allowed to complete even with no recipe mapping, but must emit **no fabricated usage** and must show a machine-readable usage status **unmapped** in the new ingredient-usage endpoint. V2-form POS sale should reject unmapped variation recipes rather than silently undercount. For mapped legacy POS sales, post usage in the same transaction; all new POS sale usage uses is_estimated=true. This compatibility exception requires explicit tests.

### Source / import identity

Keep **sales.source_id** as the existing required café-wide idempotency key. It already prevents duplicate V1 submissions and must not be dropped. Add **source** for provenance, and **external_id** for the original generated/imported record key, with UNIQUE(cafe_id,source,external_id) WHERE external_id IS NOT NULL. For new generated/imported rows, derive source_id as a deterministic namespaced string (e.g. generated:20261101:000123), and use the raw stable generator key in external_id. Thus both constraints are consistent. For existing synthetic rows, preserve source_id and copy it into external_id after labeling generated.

For idempotent retries compare canonical request content **including source, external_id, timestamp, product/variation, quantity, modifier selection, and discount**; do not rely only on product_id+quantity. Initial request must be validated and priced before returning an existing row if configuration would conflict. Unknown historical prices are not recomputed from today's menu on a retry; compare the stored item snapshots and original semantics. Use 409 for conflicting same identities.

Historical/CSV source semantics:
- **pos**: new live/demo completed sale; when recipe mapping exists, posts estimated SALE_USAGE transactionally.
- **generated**: synthetic history (including existing Jan 2025 126 sales and new 12-month set), **no inventory movements**; computed on read for historical demand.
- **csv_import**: imported external history, default **no inventory movements** until a separately reviewed trusted live-import policy exists. Prevent backdated imports from rewriting stock.

Do not silently relabel an existing historical sale as a new POS event or back-post months of consumption.

## 4. API design: additive endpoints + examples

All endpoints are under **/api/v1/sales**. Every endpoint accepts/derives cafe_id, validates access to that café, and returns 404 when an object belongs to another café. Date bounds are aware ISO-8601 instants, inclusive **start_at**, exclusive **end_at**. Invalid ranges return 400 or 422. Existing endpoints keep their current paths and response fields. Example IDs/prices below are proposed seed constants.

### GET /catalog?cafe_id=CAFE001 (new; preserves existing GET /products)

~~~json
{
  "cafe_id": "CAFE001",
  "products": [{
    "product_id": "COF003", "name": "Latte", "category": "coffee", "active": true,
    "variations": [
      {"variation_id": "COF003_REG", "name": "Regular 12 oz", "size_oz": 12, "price_cents": 500, "active": true},
      {"variation_id": "COF003_LRG", "name": "Large 16 oz", "size_oz": 16, "price_cents": 575, "active": true}
    ],
    "modifier_groups": [
      {"group_id": "MILK", "selection_type": "single", "min_select": 1, "max_select": 1, "modifiers": [
        {"modifier_id": "MILK_WHOLE", "name": "Whole milk", "price_cents": 0, "is_default": true},
        {"modifier_id": "MILK_OAT", "name": "Oat milk", "price_cents": 75, "is_default": false}
      ]},
      {"group_id": "EXTRAS", "selection_type": "multiple", "min_select": 0, "max_select": 3, "modifiers": [
        {"modifier_id": "EXTRA_SHOT", "name": "Extra shot", "price_cents": 100, "is_default": false}
      ]}
    ]
  }]
}
~~~

Catalog example abridges options; real response includes Almond, Skim, Vanilla, and Caramel where allowed. Inactive catalog objects are omitted by default, optionally exposed to admin read-only history.

### POST /api/v1/sales — new V2 shape (same route as V1)

~~~json
{
  "cafe_id": "CAFE001",
  "source_id": "pos:1f9a37c2",
  "source": "pos",
  "sold_at": "2026-10-28T09:15:00-07:00",
  "discount_cents": 100,
  "items": [{
    "variation_id": "COF003_LRG",
    "quantity": 2,
    "modifiers": [
      {"modifier_id": "MILK_OAT", "quantity": 1},
      {"modifier_id": "EXTRA_SHOT", "quantity": 1}
    ]
  }]
}
~~~

Server selects cafe-scoped active variation/product/modifiers and price; client does **not** send authoritative prices or totals. V2 validation: quantity positive integer; active variation; modifier in a group assigned to this product; no repeated modifier IDs; all required group min/max counts satisfied; single group chooses exactly one; multiple group selects at most max_select; each initially quantity=1; all recipe units/ingredients valid. A default Whole milk may be injected by service only when no selection is supplied **and** the catalog marks it default (make this explicit in returned selection snapshot); no free implicit selection for groups with no default. Return 422 on invalid options/prices, 404 for cross-café/unavailable object, 409 for idempotency conflict.

~~~json
{
  "sale_id": "sale_abc123",
  "cafe_id": "CAFE001",
  "source_id": "pos:1f9a37c2",
  "source": "pos",
  "status": "completed",
  "sold_at": "2026-10-28T16:15:00Z",
  "subtotal_cents": 1500,
  "discount_cents": 100,
  "tax_cents": 123,
  "total_cents": 1523,
  "items": [{
    "sale_item_id": "sale_item_def456",
    "product_id": "COF003",
    "product_name": "Latte",
    "variation_id": "COF003_LRG",
    "variation_name": "Large 16 oz",
    "quantity": 2,
    "unit_price_cents": 575,
    "modifier_unit_cents": 175,
    "line_total_cents": 1500,
    "modifiers": [
      {"modifier_id": "MILK_OAT", "modifier_name": "Oat milk", "price_cents": 75, "quantity": 1},
      {"modifier_id": "EXTRA_SHOT", "modifier_name": "Extra shot", "price_cents": 100, "quantity": 1}
    ]
  }]
}
~~~

**Tax compatibility rule:** V1 payload always computes zero discount and zero tax, independent of cafe tax setting, preserving old totals. V2 uses current cafe.tax_rate_bps unless a separately approved exemption policy exists. No payment method or tips.

### GET /{sale_id}/ingredient-usage?cafe_id=CAFE001 (new)

~~~json
{
  "sale_id": "sale_abc123", "source": "pos", "usage_status": "computed",
  "movement_posted": true, "is_estimated": true,
  "ingredients": [
    {"ingredient_id": "COFFEE_BEANS", "quantity": "54.000", "unit": "g"},
    {"ingredient_id": "OAT_MILK", "quantity": "640.000", "unit": "mL"}
  ],
  "missing_recipe_variations": []
}
~~~

For old/unmapped rows, return status **unmapped/partial** and missing variation IDs; do not fabricate zero. For generated history, usage_status=computed with movement_posted=false. Recipe lookup for historical rows uses sale timestamp and immutable recipe version, not latest recipe.

### GET /reports/ingredient-usage?cafe_id=CAFE001&start_at=...&end_at=... (new)

~~~json
{
  "cafe_id": "CAFE001",
  "source_filter": ["pos", "generated", "csv_import"],
  "total_sales": 126,
  "mapped_sales": 120,
  "unmapped_sales": 6,
  "estimated_usage": [
    {"ingredient_id": "COFFEE_BEANS", "quantity": "850.000", "unit": "g"}
  ],
  "ledger_posted_sales": 0,
  "note": "Historical usage is an estimate and is not a stock movement."
}
~~~

Numbers are **illustrative**, not claims about the existing demo. Aggregate **once** per sale item across sources; do not add posted movements on top of computed usage, which would double-count.

### GET /reports/hourly-demand?cafe_id=...&start_at=...&end_at=... (new)

~~~json
{
  "cafe_id": "CAFE001",
  "timezone": "America/Los_Angeles",
  "rows": [{
    "local_date": "2026-11-01",
    "local_hour": 8,
    "utc_offset_minutes": -480,
    "variation_id": "COF003_LRG",
    "orders": 4,
    "units_sold": 6
  }]
}
~~~

Group by local **date + hour + variation**; include UTC offset to disambiguate the repeated DST fall-back hour. Existing sales/reports/summary remains untouched.

### GET /reports/modifier-mix?cafe_id=...&start_at=...&end_at=... (new)

~~~json
{
  "cafe_id": "CAFE001",
  "product_id": "COF003",
  "product_units": 100,
  "explicit_milk_choice_units": 85,
  "inferred_default_units": 15,
  "modifiers": [
    {"modifier_id": "MILK_OAT", "units": 30, "share_of_product_units_pct": "30.00"}
  ]
}
~~~

Numerators use **sold unit quantity**, not rows. Denominator is applicable product units; expose implicit default count separately so estimated historical defaults are not presented as observed preference.

**Integrity query** (not necessarily an API in initial core): per completed sale, compare sales.subtotal_cents to SUM(sale_items.line_total_cents), grouped by cafe_id and sale_id; do not compare sales.total to sum directly because discount/tax exist. Optionally expose as a read-only diagnostic endpoint after core is tested.

## 5. Recipe and estimated usage algorithm

Inputs: committed sale + its immutable line/modifier snapshots, variation_id, sold_at, café-specific recipe version, and catalog effect mappings. Use Python Decimal, not float.

1. Resolve complete recipe version with maximum effective_from <= sold_at for that variation/café. Missing version or ingredient/unit mismatch = explicit **unmapped**.
2. Initialize a per-unit map **ingredient_id → Decimal quantity** from recipe_lines, retaining base_unit.
3. For each selected modifier: REPLACE matching target ingredient quantity at the same volume (remove whole milk; add oat/skim/almond); ADD fixed extra ingredient quantity; reject duplicate replacements and inapplicable modifications. Whole default is a no-op.
4. Aggregate same-ingredient deltas within one sale item, then multiply by sale_items.quantity; reject invalid or negative computed usage. The recipe's set of portions is all-inclusive and must not be double-applied.
5. For **new source=pos completed** sale, create one InventoryMovement per **(sale_item_id, ingredient_id)** with movement_type='SALE_USAGE', negative quantity_delta, is_estimated=true, source_type='sale_item', source_id=sale_item_id, occurred_at=sale.sold_at, recorded_at=UTC now, recorded_by=NULL, reason='Estimated recipe consumption'. Do this **in the same database transaction** as sale + sale items + snapshots. Existing unique (ingredient_id,source_type,source_id) makes a repeated attempt nonduplicating. Re-check existing movement values on a retry; mismatched ones are conflicts.
6. For **generated** and **csv_import** historical sales, compute ingredient usage from snapshots/versions **on read only**. Do not create InventoryMovement rows, even when timestamp is after some count.
7. Inventory's stock estimator remains unchanged: new POS SALE_USAGE is included **only when it occurs strictly after that ingredient's latest verified count**. A backdated POS event before an anchor is retained in the event ledger but correctly excluded from present stock; a generated event never enters the ledger. Without a verified anchor, stock remains unknown despite a consumption event. Counts do not themselves generate movement entries.

**Worked example grounded in repository recipes.json:**
- COF003_LRG = Latte 16 oz, recipe **COFFEE_BEANS 18 g + WHOLE_MILK 320 mL** per drink.
- MILK_OAT replacement removes **WHOLE_MILK 320 mL** and adds **OAT_MILK 320 mL** (same volume).
- EXTRA_SHOT adds **COFFEE_BEANS 9 g**, per recipes.json recipe_rules.extra_espresso_shot.
- One modified latte therefore uses **27 g COFFEE_BEANS + 320 mL OAT_MILK + 0 mL WHOLE_MILK**.
- Line **quantity=2**: exact usage **54.000 g COFFEE_BEANS**, **640.000 mL OAT_MILK**, **no WHOLE_MILK usage**.
- Persist two negative movements for this sale_item_id, not four: COFFEE_BEANS **-54.000**, OAT_MILK **-640.000**.

**Caution:** an item sold with an inventory-unknown ingredient is still an estimated recipe usage, **not evidence of sufficient physical stock**. No auto-reservation and no overselling prohibition in this MVP.

## 6. Pricing and rounding example (integer cents)

**Authoritative inputs:** menu.json lists Latte regular=**500 cents**, large surcharge=**75**, Oat=**75**, Extra shot=**100**. Hence large base=575. The example assumes a discretionary order discount **100 cents** and configured illustrative **tax_rate_bps=875 (8.75%)**; the repository currently has **no such tax setting**, so 875 is an explicit example, not a reported café tax rate.

| Calculation | Integer cents |
|---|---:|
| One COF003_LRG base | 575 |
| Oat modifier per unit | +75 |
| Extra shot per unit | +100 |
| One configured latte | 750 |
| Quantity 2 → sale_item.line_total_cents | 1,500 |
| sales.subtotal_cents | 1,500 |
| sales.discount_cents | -100 |
| Taxable discounted subtotal | 1,400 |
| Tax: 1,400 × 875 / 10,000 = 122.5 → **123** | +123 |
| sales.total_cents | **1,523** |

Rule: use **integer arithmetic** for money. Compute tax **once per order** on max(0, subtotal - discount), with **ROUND_HALF_UP to nearest cent** (e.g. floor((taxable_cents × bps + 5000) / 10000) for nonnegative values). Do not sum per-line rounded taxes. Do not allow discount > subtotal, negative prices, or totals beyond PostgreSQL INT; guard potential overflow in service before insert. Rates and discounts on older orders are already snapshotted by their stored cents. No tips, no partial line discounts in V2.

DB checks: line_total = quantity × (unit_price + modifier_unit); sales.total = subtotal − discount + tax. Cross-row sales.subtotal = SUM(line_total) is guaranteed by the service transaction and audited by the integrity report, not an impossible same-row CHECK or a new trigger.

## 7. Synthetic history contract: 12 months, ~55,000 sales

**Window:** café-local **2025-11-01 00:00 through 2026-10-31 23:59:59** (end-exclusive Nov 1, 2026). Sales source='generated', carefully labeled synthetic, product/variation IDs from the seeded catalog, historical recipe version available before 2025-11-01. Do **not** use Perplexity's sample products. Mark event days as generator inputs/sidecar scenario metadata, not as a prohibited sales.channel or a fabricated observation. The Nov 2025–Oct 2026 generator is **not implemented by this design**.

Exactly **three UTF-8 CSV files with header rows**, FK-safe load order:

**sales.csv**
~~~csv
sale_id,cafe_id,source_id,source,external_id,sold_at,recorded_at,status,subtotal_cents,discount_cents,tax_cents,total_cents
gen_sale_20251101_000001,CAFE001,generated:20251101:000001,generated,20251101:000001,2025-11-01T15:14:00Z,2025-11-01T15:15:00Z,completed,750,0,0,750
~~~

**sale_items.csv**
~~~csv
sale_item_id,sale_id,cafe_id,product_id,variation_id,product_name,variation_name,quantity,unit_price_cents,modifier_unit_cents,line_total_cents
gen_item_20251101_000001_01,gen_sale_20251101_000001,CAFE001,COF003,COF003_LRG,Latte,Large 16 oz,1,575,175,750
~~~

**sale_item_modifiers.csv**
~~~csv
sale_item_modifier_id,sale_item_id,cafe_id,modifier_id,modifier_name,price_cents,quantity
gen_mod_20251101_000001_01_01,gen_item_20251101_000001_01,CAFE001,MILK_OAT,Oat milk,75,1
gen_mod_20251101_000001_01_02,gen_item_20251101_000001_01,CAFE001,EXTRA_SHOT,Extra shot,100,1
~~~

**Exact column semantics:** timestamps ISO-8601 UTC with Z; IDs and names TEXT (RFC-4180 escaping for commas/quotes); money INTEGER whole cents, no currency symbols; quantity positive whole number; status completed|void (recommend generated history completed only); modifier.quantity is per sold unit, not order-wide; line_total already includes modifiers and quantity; every sale_item/variation and modifier must belong to the same cafe. Missing optional external_id is empty CSV cell / SQL NULL; generated history **requires** external_id. For discount/tax use zeros by default; if generator emits nonzero, it must use exactly the service tax rounding policy and sum checks. Select product/variation and milk choices from seeded allowed catalog; include Whole milk explicitly for newly generated sales when applicable. Maintain deterministic record IDs and selected timestamps across generator reruns. Do not give synthetic orders a live POS source.

**Bulk-import plan:**
1. Generate deterministic seed-controlled rows (including variation/milk/extras choices and event-day multipliers) to local CSVs; validate all FK references and cents arithmetic before loading.
2. In one *disposable/demo* database transaction or bounded staged batches, COPY UTF-8 CSVs into TEMP staging tables (or batch inserts if COPY complexity is not worth it); compare existing keys to avoid duplicates, and **reject conflicts rather than overwriting**. Insert in order sales, sale_items, sale_item_modifiers; validate café ownership and subtotal integrity before COMMIT. Use one café-scoped transaction if feasible; for retries derive the same IDs and compare existing payloads/checksums. Do not use ON CONFLICT DO NOTHING without a comparison.
3. Verify expected row counts (~55k sales, dependent line/modifier counts), query plan/indexes, revenue checksums, sample customer selections, exact boundary dates, and a representative event-vs-normal day trend.
4. Explicitly **never** create SALE_USAGE for source=generated/csv_import. Existing Jan 2025 seed remains and is not overwritten. Avoid double counting historical and new data in dashboard date filters.

**Runtime expectation, not a measured benchmark:** ~55k sales plus approximately 1–3 items/sale and modifiers is a reasonable PostgreSQL 17 laptop dataset. Target **under 2 minutes for database COPY + validation** with tuned staging, and **2–5 minutes end-to-end** including deterministic generation and index checks on an ordinary Docker development setup; measure it before demo. Batched ORM creates may take much longer, so COPY/staging is preferable for this volume. If the deadline bites, seed **2–4 weeks** with a realistic event day and document that it is a reduced history, rather than misrepresenting 55k.

## 8. Pytest plan (PostgreSQL 17, dedicated cafés)

Tests should construct their own catalog, café, inventory and recipe prerequisites; no dependence on CAFE001 being pre-seeded. Prefer transaction-rollback fixtures with separate TEST_* café IDs and seeded regular/large variation fixtures.

1. **test_v2_catalog_preserves_product_ids_categories_and_regular_prices** — 18 products, 30 variations, 6 bakery REG-only.
2. **test_modifier_groups_enforce_single_multiple_min_max_default** — Milk exactly one, Extras zero to three, no duplicate modifier IDs.
3. **test_cross_cafe_variant_modifier_recipe_fks_rejected** — every café-scoped FK, test same names in different cafés.
4. **test_inactive_variation_or_inapplicable_modifier_rejected** — 404/422; no partial sale.
5. **test_legacy_post_unchanged_status_prices_and_response** — existing V1 test payload, 201/200/409 and 0 tax even with café bps configured.
6. **test_existing_126_210_backfilled_without_changing_amounts** — migration fixture checksum of sale IDs, item IDs, total_cents, source_id; count remains exact.
7. **test_two_variations_of_same_product_per_sale** — demonstrates removal of old unique (sale_id,product_id).
8. **test_price_snapshots_survive_catalog_changes** — names, variation price, modifier price unchanged on historical sale detail.
9. **test_pricing_line_and_order_checks_with_half_up_tax** — 575+75+100, qty2, discount100, 875 bps = 1523; invalid negatives and integer overflow rejected.
10. **test_T3_wrong_total_rejected_and_subtotal_integrity_detected** — DB CHECK rejects wrong sales.total; separate integrity query catches header subtotal mismatching sum of lines.
11. **test_usage_large_latte_oat_extra_shot_qty2** — 54.000 g beans, 640.000 mL oat, 0 whole; units exactly match recipe JSON.
12. **test_missing_recipe_or_milk_target_not_falsely_zero** — V2 rejects unmapped, legacy remains accepted with explicit unmapped report.
13. **test_sale_and_sale_usage_commit_atomically** — forced movement failure rolls back sale/items/modifiers; all movement rows is_estimated=true.
14. **test_sale_retry_does_not_duplicate_sale_usage** — same source_id returns same sale, no duplicate movement by (ingredient_id,sale_item_id); conflicting payload 409.
15. **test_T5_duplicate_import_source_external_id** — same external_id per café/source idempotent; conflict rejected; distinct source can reuse external_id.
16. **test_generated_and_csv_import_never_write_stock_ledger** — usage analytics available on read but no movements.
17. **test_stock_anchor_cutoff_includes_only_after_count** — later POS movement included; older one ignored; unknown remains unknown.
18. **test_hourly_variation_demand_scoped_by_cafe_timezone** — uses specific café timezone, not DB/server UTC; other café excluded.
19. **test_dst_2026_11_01_0814_minus0800** — sale at **2026-11-01T08:14:00-08:00** is **2026-11-01 local date, local hour 8, offset -480** in America/Los_Angeles; also test fall-back 01:30 with both offsets as separate buckets.
20. **test_modifier_mix_denominator_quantity_and_inferred_defaults** — oat share vs total product units; old records labeled inferred.
21. **test_reports_exclude_void_and_respect_half_open_time_bounds** — consistent top products and revenue aggregation; exclude source replays.
22. **test_T10_integrity_query_flags_bad_subtotal** — intentionally bad cross-row aggregate flagged; correct zero-result baseline.
23. **test_demo_seed_on_fresh_db_creates_own_prerequisites** — seed_inventory/seed_sales or explicit fixture prerequisites inside isolated transaction, idempotent second run; no reliance on local existing DB rows.
24. **test_all_connection_paths_use_utc_timezone** — SELECT current_setting('TimeZone')='UTC' for application pool, Alembic connection, and test sessions, with timezone-aware roundtrip.

**Housekeeping in first implementation commit:** current **test_seed_demo_operations.py** expects CAFE001 and seed prerequisites; make tests initialize those records themselves in an isolated test transaction or dedicated disposable database. Tests must never delete actual developer demo rows as preparation. Force PostgreSQL connection TimeZone to **UTC** across pooled engine connections (e.g. connection setup hook/PG connection setting) and Alembic engine; verify psycopg returns TIMESTAMPTZ with UTC offset independent of database server configuration. UTC on the connection does **not** replace explicit local-zone conversion for reports. Check existing V1 tests and Inventory tests still pass.

## 9. Two-day implementation sequence (small commits, each independently testable)

| Order | Commit scope | Acceptance gate |
|---|---|---|
| **0** | **test/db hygiene**: independent seed fixtures + UTC DB session timezone | Existing 114 backend and 17 frontend tests still pass; fresh empty DB scenario tested |
| **1 (M1)** | **Catalog schema + idempotent variation/modifier seed** | Product IDs/categories unchanged, 30 variations, option validation, Alembic upgrade/downgrade |
| **2 (M2)** | **Sale snapshots + source/discount/tax migration/backfill** | 126/210 preserved, total cents unchanged, V1 POST compatibility tests pass, no drift |
| **3 (M3)** | **Recipe lines/effects + priced V2 POST + transactional SALE_USAGE** | 54g/640mL example, rollback/idempotency, stock anchoring, legacy unmapped behavior |
| **4** | **Minimal V2 catalog and 3 read reports + integrity query** | Café-zone/DST, modifier-mix, generated history usage, no live movement for generated |
| **5 optional** | **Deterministic CSV generator + staged import and benchmark** | 55k import idempotent with no collisions; synthetic sources do not change stock |

Each commit updates tests and docs for its own scope. **Do not start UI screens until backend/API checks and V1 compatibility are green.** Preserve Sales V1 API throughout. If 48 hours is tight, finish 0–3 first; commit 4 in simplified service-query form; move 5 after demo. Estimate is a prioritization, not a promise of runtime.

## 10. Risks, explicit cuts, and decision log

| Risk | Mitigation; first cut under deadline |
|---|---|
| Perplexity SQL isn't in repo; exact triggers/constraints unknown | Treat prompt decisions as authoritative; **do not install its SQL**. Re-review source only if files arrive. |
| Schema migration breaks 126 synthetic orders or V1 POST | Staged nullable→backfill→NOT NULL; test IDs, sums, V1 responses; rollback on disposable copy. |
| Cross-café data leakage | Composite café FKs, cafe_id filters on every service query, tests using two cafés. |
| Duplicated inventory usage | Same transaction, source_type=sale_item/source_id=sale_item_id, unique constraint, retry comparison; skip generated/imported. |
| Wrong historical recipe interpretation | Version by effective_from; historical milk default labeled inferred; never claim actual customer preference. |
| Incomplete recipe or unknown physical stock | Explicit unmapped/unknown, not zero; never imply sale proves stock availability. |
| Discount/tax/revenue discrepancy | Integer cents, one order-level half-up tax rounding, service sum checks, integrity diagnostic. |
| Imported historical sale floods memory or takes too long | COPY staged inserts, bounded validations, indexes; start smaller and measure. |
| Mixed UTC and café local time / fall-back collisions | UTC session, aware timestamp API, report by café ZoneInfo with offset, DST tests. |
| No auth or sale cancellation workflow | Internal demo only; no public exposure of write endpoints. |
| Too many features for two working days | **Cut first:** CSV import volume → optional integrity view (keep query) → full modifier mix UI/report polish → configurable tax UI → syrup extras. **Do not cut:** V1 compatibility, café FK isolation, correct cents, atomic inventory usage, idempotency. |

**Deferred design note—refunds:** A future refund record should reference the original sale/line and separate merchandise, tax, and tip (if ever introduced); include **ingredients_consumed BOOL NOT NULL DEFAULT TRUE**. A refund does not put prepared milk/beans back in Inventory. No refund schema is part of V2.

## 11. Open questions (recommended defaults)

1. **Syrup extra recipes/prices:** Per menu.json the extra syrup prices are unspecified. **Default:** Vanilla and Caramel each +50 cents, +15 mL per drink; require explicit confirmation before implementing effects.
2. **Tax setting for demo:** Existing cafes has no tax rate and law/location-specific sales tax isn't modeled. **Default:** tax_rate_bps=0 for CAFE001; 875 bps only in automated calculation tests, no claim of applicable real tax.
3. **Extra-shot applicability versus a single shared Extras group:** Recipe rules allow extra shot for COF002–COF007, while milk/syrup beverages differ. **Default:** initial Extras group only COF003–COF007; COF002 extra shot can be added after a per-modifier applicability enhancement.
4. **Historical dataset placement:** Should the 55k generated sales load into CAFE001 alongside January 2025 history? **Default:** yes, clearly labeled source=generated, with date-window filters and no SALE_USAGE postings.

Once approved, implementation may proceed in the above order. **No implementation has been performed as part of this document.**
