# Milestone 4 — Meta AI Agent Integration

**Project:** CaféPilot  
**Date:** October 26, 2026  
**Status:** Initial agent workflow successfully tested  
**Use Case:** UC2A — Catering Inventory Readiness

## 1. Objective

Integrate Meta Muse Spark with CaféPilot's existing recipe, inventory, and catering services to enable natural-language inventory readiness checks.

The objective was to demonstrate a working AI agent that interprets an owner's request, uses existing Python business tools, and explains the results.

## 2. Components Implemented

| Component | Purpose |
|---|---|
| `backend/agents/cafe_agent.py` | Meta model integration and tool-call handling |
| `backend/services/menu_service.py` | Dynamically retrieves the café menu |
| `backend/services/recipe_service.py` | Calculates ingredient requirements |
| `backend/services/inventory_service.py` | Compares requirements with available inventory |
| `backend/services/catering_service.py` | Coordinates catering readiness calculations |
| `POST /agent/chat` | Exposes agent functionality through FastAPI |
| `tests/test_cafe_agent.py` | Mocked agent integration tests |

### Architecture

```text
Owner's natural-language request
             |
             v
FastAPI: POST /agent/chat
             |
             v
Python automatically loads menu.json
             |
             v
Meta Muse Spark 1.3
             |
             v
check_catering_readiness (model-requested tool)
             |
             v
Recipe Service + Inventory Service
             |
             v
Validated readiness report
             |
             v
Meta Muse Spark explains results
```

The menu is loaded from JSON rather than hardcoded into the agent prompt.

All ingredient and shortage calculations are performed in Python.

## 3. Automated Testing

Executed:

```bash
python -m pytest tests/ -v
```

**Observed result:**

```text
13 passed in 0.40s
```

The passing suite included:

- Recipe calculation tests
- Inventory sufficiency and shortage tests
- Missing inventory and unit mismatch tests
- Catering API tests
- Mocked agent tool-calling tests
- Agent clarification and input validation tests

These tests use synthetic fixture data. The mocked agent tests do not verify the model's performance on all possible natural-language requests.

## 4. Live Meta Model API Test

**Model:** `muse-spark-1.3`  
**API:** Meta Model API  
**SDK:** OpenAI-compatible Python SDK

### Sample request

Endpoint: `POST /agent/chat`

```json
{
  "message": "Can we prepare 10 large oat-milk lattes?"
}
```

### Observed execution

1. Python retrieved the current café menu through `get_menu`.
2. Muse Spark identified the Latte product (`COF003`), large size, quantity 10, and oat milk.
3. Muse Spark requested the `check_catering_readiness` tool.
4. Python executed the recipe and inventory services.
5. Muse Spark generated a natural-language explanation using the returned results.

### Verified calculation

| Ingredient | Required | Available | Shortage |
|---|---:|---:|---:|
| Espresso beans | 180 g | 1,000 g | 0 g |
| Oat milk | 3,200 mL | 2,000 mL | 1,200 mL |

### Sample response

The agent returned:

> No — we cannot fully prepare 10 large oat-milk lattes with the current snapshot.
>
> Espresso Coffee Beans: 180 g required, 1,000 g available — sufficient.
>
> Oat Milk: 3,200 mL required, 2,000 mL available — shortage of 1,200 mL.
>
> This is based on the synthetic inventory snapshot from 2026-10-26, not a guarantee of availability at pickup time. No inventory has been reserved or deducted.

### Tool execution evidence

The API response's `tool_history` contained:

```json
[
  {
    "tool": "get_menu"
  },
  {
    "tool": "check_catering_readiness"
  }
]
```

This is a shortened record of the observed execution, not the complete API response.

`get_menu` was executed automatically by Python. `check_catering_readiness` was selected by Muse Spark using function calling.

**No API credentials or secret values are included in this log.**

## 5. Engineering Decisions

- Keep menu data in JSON instead of hardcoding products in the agent prompt.
- Separate AI interpretation from deterministic business calculations.
- Validate tool arguments in Python.
- Keep inventory readiness checks read-only.
- Distinguish synthetic stock snapshots from real stock observations.
- Use mocked tests to validate integration without requiring live API calls for every test.

## 6. Limitations

- The agent currently relies on discretionary tool calling (`tool_choice="auto"`).
- A future request might be answered without invoking the readiness tool.
- The inventory snapshot is synthetic and static.
- Intervening sales, incoming deliveries, stock reservations, and packaging are not included.
- Live success was observed for one example request; it does not establish reliability across all menu items or natural-language variations.
- No supplier purchase or inventory mutation is performed.

## 7. Next Milestone

**Milestone 4A — Reliable Structured Agent Orchestration**

Planned workflow:

1. Load the current menu automatically.
2. Use Muse Spark to extract a structured catering request or request clarification.
3. Validate product IDs, quantities, and modifiers in Python.
4. Automatically execute the catering readiness service for valid requests.
5. Generate a grounded explanation using the computed results.
6. Add tests for ambiguous requests, unsupported modifiers, and invalid model output.

This will improve reliability and prepare CaféPilot for additional use cases.

## 8. Milestone Outcome

CaféPilot successfully demonstrated a live Meta-powered agent workflow combining dynamic menu retrieval, model-requested tool calling, deterministic recipe calculations, inventory readiness assessment, and an owner-facing explanation.

**Evidence:** 13 automated tests passed and one live agent request returned the expected 1,200 mL oat-milk shortage.
