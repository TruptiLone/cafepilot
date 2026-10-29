import { describe, expect, it } from "vitest";
import type { AgentRun, RunStep } from "../../api/agent";
import { describeChange, evidenceFor } from "./changes";

function node(overrides: Partial<RunStep>): RunStep {
  return {
    node_id: "n",
    action: "list_employees",
    side_effect: "read",
    requires_approval: false,
    reason: "r",
    only_if: null,
    args: {},
    resolved_args: null,
    depends_on: [],
    level: 0,
    status: "done",
    result: null,
    error: null,
    replay_of: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function runWith(nodes: RunStep[]): AgentRun {
  return {
    run_id: "R",
    cafe_id: "CAFE001",
    query: "q",
    goal: "g",
    assumptions: [],
    status: "awaiting_approval",
    as_of: "2026-10-30T01:00:00Z",
    model: "m",
    attempts: 1,
    edges: [],
    levels: [],
    warnings: [],
    created_at: "2026-10-29T19:00:00Z",
    updated_at: "2026-10-29T19:00:00Z",
    nodes,
  };
}

const shift = node({
  node_id: "add",
  action: "schedule_shift",
  side_effect: "write",
  status: "awaiting_approval",
  resolved_args: {
    employee_id: "E4",
    start_at: "2026-11-01T07:30:00-08:00",
    end_at: "2026-11-01T14:30:00-08:00",
  },
});

describe("describeChange", () => {
  it("names the employee and the café-local time", () => {
    const run = runWith([
      node({ result: [{ employee_id: "E4", name: "Ethan Patel", role: "barista" }] }),
      shift,
    ]);
    expect(describeChange(shift, run)).toBe("Schedule Ethan Patel for Sun, Nov 1, 7:30 AM–2:30 PM");
  });

  it("describes a simulated supplier order in readable units", () => {
    const order = node({
      action: "propose_supplier_order",
      side_effect: "write",
      resolved_args: {
        lines: [{ ingredient_id: "OAT_MILK", quantity: "2000", unit: "mL" }],
        requested_delivery_at: "2026-11-01T06:00:00-08:00",
      },
    });
    const run = runWith([
      node({ action: "get_stock_estimates", result: [{ ingredient_id: "OAT_MILK", name: "Oat Milk", estimated_quantity: "4650.000", base_unit: "mL" }] }),
      node({ action: "project_recipe_usage", result: { ingredients: [{ ingredient_id: "OAT_MILK", quantity: "4800.000", unit: "mL" }] } }),
      order,
    ]);
    expect(describeChange(order, run)).toBe(
      "Order 2 L Oat Milk for delivery Sun, Nov 1 by 6 AM (simulated supplier)",
    );
    expect(evidenceFor(order, run)).toEqual(["Oat Milk: about 4.65 L on hand, 4.8 L needed for the event."]);
  });
});

describe("evidenceFor a shift", () => {
  const coverage = node({
    action: "coverage_report",
    result: {
      hours: [
        { hour_start: "2026-11-01T08:00:00-08:00", scheduled_employees: 0, required_staff: 2, coverage_status: "understaffed" },
        { hour_start: "2026-11-01T09:00:00-08:00", scheduled_employees: 1, required_staff: 2, coverage_status: "understaffed" },
        { hour_start: "2026-11-01T10:00:00-08:00", scheduled_employees: 2, required_staff: 2, coverage_status: "adequate" },
      ],
    },
  });

  it("summarizes coverage and confirms no overlapping shift", () => {
    const run = runWith([
      node({ result: [{ employee_id: "E4", name: "Ethan" }] }),
      node({ action: "list_shifts", result: [] }),
      coverage,
      shift,
    ]);
    expect(evidenceFor(shift, run)).toEqual([
      "2 of 3 hours are short: as few as 0 of 2 people scheduled.",
      "Ethan has no other shift at that time.",
    ]);
  });

  it("warns about an overlapping shift", () => {
    const run = runWith([
      node({ action: "list_shifts", result: [{ employee_id: "E4", employee_name: "Ethan", status: "scheduled", start_at: "2026-11-01T12:00:00-08:00", end_at: "2026-11-01T16:00:00-08:00" }] }),
      shift,
    ]);
    expect(evidenceFor(shift, run)).toEqual(["Ethan already works Sun, Nov 1, 12 PM–4 PM, so this would overlap."]);
  });
});

describe("evidence for an order computed in code", () => {
  it("shows need, buffer, stock and the order line by line", () => {
    const order = node({
      action: "propose_supplier_order",
      side_effect: "write",
      resolved_args: { lines: [{ ingredient_id: "WHOLE_MILK", quantity: "2560", unit: "mL" }] },
    });
    const run = runWith([
      node({
        action: "calculate_shortfall",
        result: {
          buffer_pct: "10",
          lines: [
            { ingredient_id: "WHOLE_MILK", name: "Whole Milk", unit: "mL", needed: "9600.000", buffer: "960.000",
              needed_with_buffer: "10560.000", on_hand: "8000.000", order_quantity: "2560", status: "short" },
            { ingredient_id: "ALMOND_MILK", name: "Almond Milk", unit: "mL", needed: "500.000", buffer: "50.000",
              needed_with_buffer: "550.000", on_hand: null, order_quantity: null, status: "unknown_stock" },
          ],
        },
      }),
      order,
    ]);
    expect(describeChange(order, run)).toBe("Order 2.56 L Whole Milk (simulated supplier)");
    expect(evidenceFor(order, run)).toEqual([
      "Whole Milk: need 9.6 L + 10% buffer 960 mL = 10.56 L; on hand 8 L → order 2.56 L.",
      "Stock unknown, not ordered: Almond Milk. Count these first.",
    ]);
  });
});
