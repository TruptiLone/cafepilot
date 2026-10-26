# Milestone 4A — Reliable Agent Orchestration and Intent Classification

**Date:** October 26, 2026  
**Project:** CaféPilot  
**Use Case:** UC2A — Catering Inventory Readiness

## Objective

Improve the reliability of CaféPilot's Meta Muse Spark agent by separating natural-language interpretation from deterministic business operations.

## Architecture

Owner Request → Muse Spark Structured Extraction → Pydantic Validation → Python Catering Readiness Calculation → Grounded Response

The application automatically executes the readiness calculation for valid orders rather than relying on the language model to choose a tool.

## Implementation

- Added `backend/agents/order_extractor.py` for structured order extraction.
- Added `backend/services/order_validator.py` for validation against the current menu and recipe definitions.
- Updated `backend/agents/cafe_agent.py` to orchestrate extraction, validation, calculation and response generation.
- Added three intent categories: `ready`, `needs_clarification` and `out_of_scope`.
- Added deterministic quantity formatting and error handling.
- Ensured unsupported or incomplete requests do not trigger inventory calculations.

## Automated Test Results

**Result: 31 tests passed (reported).**

Tests cover recipe calculations, inventory checks, API behavior, order extraction, order validation, orchestration, and out-of-scope handling.

These tests use mocked model responses for agent behavior and do not establish live-model classification accuracy.

## Live Integration Testing

### Scenario 1 — Complete Catering Order

**Request:** "Can we prepare 10 large oat-milk lattes?"

**Observed status:** `shortage`

**Observed result:**
- Product: `COF003` — Latte
- Quantity: 10
- Size: Large
- Milk: Oat
- Coffee beans: 180 g required; 1,000 g available
- Oat milk: 3,200 mL required; 2,000 mL available
- Oat milk shortage: 1,200 mL

**Outcome:** Passed. Muse Spark extracted the request and the Python orchestrator automatically executed the readiness calculation.

### Scenario 2 — Missing Quantity

**Request:** "Can we prepare some lattes?"

**Observed response:**

```json
{
  "status": "needs_clarification",
  "answer": "How many lattes should we prepare?",
  "tool_history": []
}
```

**Outcome:** Passed. The agent requested missing information without executing inventory calculations.

### Scenario 3 — Greeting / Out-of-Scope Request

**Request:** "Hi, how are you?"

**Initial observed behavior:** `needs_clarification`, asking "What would you like to order?"

**Improvement:** Added explicit `out_of_scope` intent classification.

**Expected response after the change:** `out_of_scope`, with empty `tool_history`.

**Live retest:** Pending confirmation.

### Scenario 4 — Greeting Combined With Catering Order

**Request:** "Hi! Can we prepare 10 large oat-milk lattes?"

**Expected:** Process as a valid catering request, with a 1,200 mL oat-milk shortage.

**Live test status:** Pending confirmation.

### Scenario 5 — Multiple Products

**Request:** "Can we prepare 5 regular oat-milk lattes and 3 regular Americanos?"

**Expected:** Aggregate ingredients across both products, validate inventory and return the calculated result.

**Live test status:** Pending confirmation.

### Scenario 6 — Unsupported Supplier Action

**Request:** "Please order 500 bags of coffee beans from a supplier."

**Expected:** `out_of_scope`; no supplier order, inventory change or readiness calculation.

**Live test status:** Pending confirmation.

## Key Engineering Decisions

1. The model interprets requests; Python validates and performs business calculations.
2. Ingredient calculations remain deterministic and testable.
3. Inventory data is synthetic and represents a static snapshot.
4. No inventory is reserved or deducted.
5. No supplier purchases are initiated.
6. Unsupported requests must not initiate business actions.

## Known Limitations

- Prompt-requested JSON is not equivalent