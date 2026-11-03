import { describe, expect, it } from "vitest";
import type { AgentRun, DetectedEvent } from "../../api/agent";
import { buildStory, modelName } from "./story";

function step(overrides: Partial<AgentRun["nodes"][number]>): AgentRun["nodes"][number] {
  return {
    node_id: "stock",
    action: "get_stock_estimates",
    side_effect: "read",
    requires_approval: false,
    reason: "Check milk stock",
    only_if: null,
    args: { cafe_id: "CAFE001" },
    resolved_args: { cafe_id: "CAFE001" },
    depends_on: [],
    level: 0,
    status: "done",
    result: null,
    error: null,
    replay_of: null,
    started_at: "2026-11-05T18:00:00Z",
    finished_at: "2026-11-05T18:00:01Z",
    ...overrides,
  };
}

const marketEvent: DetectedEvent = {
  event_id: "EVT_1", title: "Santa Clara Farmers Market", category: "farmers_market",
  starts_at: "2026-11-08T16:00:00Z", ends_at: "2026-11-08T22:00:00Z",
  location: null, distance_m: 250, source: "Santa Clara community events (demo)", is_demo: true,
  muse_message: "", plan_state: "needs_ok", run_id: "RUN-1", latest_run_id: "RUN-1",
  expected: { confidence: "good", examples: 6, normal_drinks: 96, extra_drinks_low: 19,
    extra_drinks_typical: 35, extra_drinks_high: 53, extra_food_typical: 7, basis: "Compared the last 6 editions." },
};

const write = { side_effect: "write" as const, requires_approval: true, status: "awaiting_approval" as const, result: null };

/** The farmers market plan's shape: forecast feeds the shortfall; staffing checks stand alone. */
const marketRun: AgentRun = {
  run_id: "RUN-1",
  cafe_id: "CAFE001",
  query: "Prepare for the farmers market",
  goal: "Get stock and staff ready for Sunday",
  assumptions: [],
  status: "awaiting_approval",
  as_of: "2026-11-06T02:00:00Z",
  model: "muse-wonder-1.0",
  attempts: 1,
  edges: [],
  levels: [],
  warnings: [],
  created_at: "2026-11-05T18:37:00Z",
  updated_at: "2026-11-05T18:37:00Z",
  event: marketEvent,
  nodes: [
    step({ node_id: "demand", action: "estimate_event_demand", result: {
      drinks: { normal: 96, extra_typical: 35, extra_low: 19, extra_high: 53 }, food: { extra_typical: 7 },
      examples: [{}, {}, {}, {}, {}, {}], confidence: "good",
    } }),
    step({ node_id: "stock", action: "get_stock_estimates" }),
    step({ node_id: "shortfall", action: "calculate_shortfall", depends_on: ["demand", "stock"], result: {
      lines: [
        { ingredient_id: "OAT_MILK", name: "Oat Milk", unit: "mL", status: "short", shortfall: "4100", packs: 1, pack_name: "case of 6 × 1 L",
          needed_with_buffer: "9000", normal_use: "1000", on_hand: "5900", deliveries: "0" },
        { ingredient_id: "WHOLE_MILK", name: "Whole Milk", unit: "mL", status: "short", shortfall: "1200", packs: 1, pack_name: "case of 6 × 1 L",
          needed_with_buffer: "40000", normal_use: "4800", on_hand: "7600", deliveries: "36000" },
        { ingredient_id: "ALMOND_MILK", name: "Almond Milk", unit: "mL", status: "enough", shortfall: null, packs: null, pack_name: "case of 6 × 1 L",
          needed_with_buffer: "350", normal_use: null, on_hand: "5200", deliveries: "0" },
      ],
    } }),
    step({ node_id: "suppliers", action: "find_suppliers", depends_on: ["shortfall"] }),
    step({ node_id: "order", action: "propose_supplier_order", depends_on: ["shortfall", "suppliers"], ...write }),
    step({ node_id: "coverage", action: "coverage_report" }),
    step({ node_id: "people", action: "list_employees" }),
    step({ node_id: "add_barista", action: "schedule_shift", depends_on: ["coverage", "people"], ...write,
      resolved_args: { employee_id: "DEMO_EMP_004", start_at: "2026-11-08T08:00:00-08:00", end_at: "2026-11-08T10:00:00-08:00" } }),
  ],
};

type Story = ReturnType<typeof buildStory>;

const edge = (story: Story, from: string, to: string) => story.edges.find((e) => e.from === from && e.to === to);

/** One read per owner box kind: ingredients, staff, sales and "other checks". */
const MIDDLE = ["get_stock_estimates", "coverage_report", "sales_summary", "check_weather"];

function expectNoOverlap(story: Story) {
  for (let level = 0; level < story.levels; level++) {
    const cols = story.boxes.filter((b) => b.level === level).map((b) => b.col).sort((a, b) => a - b);
    cols.slice(1).forEach((col, i) => expect(col - cols[i]).toBeGreaterThanOrEqual(2)); // a box spans two half-columns
  }
  expect(Math.min(...story.boxes.map((b) => b.col))).toBe(0);
}

describe("buildStory", () => {
  it("reads event, then the busier day, then what to get ready", () => {
    const story = buildStory(marketRun);
    expect(edge(story, "event", "demand")).toBeTruthy();
    expect(edge(story, "demand", "ingredients")).toBeTruthy();
    expect(edge(story, "event", "staff")).toBeUndefined();
    expect(story.edges.filter((e) => e.from === "event")).toEqual([expect.objectContaining({ to: "demand" })]);
  });

  it("draws the forecast-to-staff edge dashed and labelled, real dependencies solid", () => {
    const story = buildStory(marketRun);
    expect(edge(story, "demand", "staff")).toEqual(expect.objectContaining({ dashed: true, label: "event hours" }));
    expect(edge(story, "demand", "ingredients")?.dashed).toBe(false);
    expect(edge(story, "ingredients", "suppliers")?.dashed).toBe(false);
  });

  it("puts staff and ingredients side by side under the forecast, decisions on the bottom row", () => {
    const story = buildStory(marketRun);
    const box = (id: string) => story.boxes.find((b) => b.id === id)!;
    expect(box("demand").level).toBe(1);
    expect(box("staff").level).toBe(box("ingredients").level);
    expect(box("staff").lane).not.toBe(box("ingredients").lane);
    const decisions = story.boxes.filter((b) => b.id.startsWith("d:"));
    expect(new Set(decisions.map((d) => d.level)).size).toBe(1);
    expect(decisions[0].level).toBeGreaterThan(box("suppliers").level);
  });

  it("centres the event over the forecast and balances the diagram", () => {
    const story = buildStory(marketRun);
    const box = (id: string) => story.boxes.find((b) => b.id === id)!;
    expect(box("event").col).toBe(box("demand").col);
    expect(box("event").col + 1).toBe(story.cols / 2);
    expectNoOverlap(story);
  });

  it.each([2, 3, 4])("centres the forecast over %i boxes in the middle row", (n) => {
    const reads = MIDDLE.slice(0, n);
    const story = buildStory({
      ...marketRun,
      nodes: [
        step({ node_id: "demand", action: "estimate_event_demand" }),
        ...reads.map((action) => step({ node_id: action, action })),
        ...reads.map((action) => step({ node_id: `do_${action}`, action: "record_inventory_movement", depends_on: [action], ...write })),
      ],
    });
    const box = (id: string) => story.boxes.find((b) => b.id === id)!;
    const middle = story.boxes.filter((b) => b.level === 2);
    expect(middle).toHaveLength(n);
    expect(story.cols).toBe(n * 2);
    expect(box("event").col).toBe(box("demand").col);
    expect(box("demand").col + 1).toBe(story.cols / 2);
    expectNoOverlap(story);
  });

  it("names each item per case in the order line, from the shortfall lines", () => {
    const story = buildStory(marketRun);
    expect(story.summary[0]).toBe(
      "Short 4.1 L oat milk & 1.2 L whole milk. 1 case oat milk (6 × 1 L) and 1 case whole milk (6 × 1 L) cover it.",
    );
  });

  it("counts several cases, and falls back to the pack name as given", () => {
    const shortfall = marketRun.nodes.find((n) => n.node_id === "shortfall")!;
    const story = buildStory({
      ...marketRun,
      nodes: marketRun.nodes.map((n) => (n === shortfall ? { ...n, result: { lines: [
        { name: "Oat Milk", unit: "mL", status: "short", shortfall: "9000", packs: 2, pack_name: "case of 6 × 1 L", needed_with_buffer: "9000" },
        { name: "Espresso Beans", unit: "g", status: "short", shortfall: "500", packs: 1, pack_name: "bag", needed_with_buffer: "500" },
      ] } } : n)),
    });
    expect(story.summary[0]).toBe("Short 9 L oat milk & 0.5 kg espresso beans. 2 cases oat milk (6 × 1 L) and 1 bag espresso beans cover it.");
  });

  it("hangs boxes off the event when there is no forecast", () => {
    const staffOnly: AgentRun = {
      ...marketRun,
      nodes: marketRun.nodes.filter((n) => ["coverage", "people", "add_barista"].includes(n.node_id)),
    };
    const story = buildStory(staffOnly);
    expect(story.boxes.some((b) => b.kind === "demand")).toBe(false);
    expect(edge(story, "event", "staff")).toEqual(expect.objectContaining({ dashed: false }));
    expect(edge(story, "event", "staff")?.label).toBeUndefined();
  });
});

// ---------- models, code steps, ingredients detail, no "Share it?" ----------------

describe("buildStory labels", () => {
  it("has no 'Share it?' box", () => {
    const story = buildStory(marketRun);
    expect(story.boxes.some((b) => b.title === "Share it?" || (b.kind as string) === "share")).toBe(false);
  });

  it("names the planning model on the event box and marks checks as worked out in code", () => {
    const story = buildStory(marketRun);
    const box = (id: string) => story.boxes.find((b) => b.id === id)!;
    expect(box("event").model).toBe("muse-wonder-1.0");
    for (const id of ["demand", "ingredients", "suppliers", "staff"]) expect(box(id).byCode).toBe(true);
    expect(story.boxes.filter((b) => b.kind === "decision").every((b) => !b.byCode && !b.model)).toBe(true);
    expect(modelName("muse-wonder-1.0")).toBe("Muse Wonder 1.0");
    expect(modelName("muse-spark-1.3")).toBe("Muse Spark 1.3");
  });

  it("labels the forecast as an estimate from the last N events", () => {
    const demand = buildStory(marketRun).boxes.find((b) => b.id === "demand")!;
    expect(demand.subtitle).toBe("~35 extra drinks, 7 pastries · estimate from the last 6 events");
  });

  it("shows need, normal use, on hand and arriving for each ingredient, as the shortfall step returned them", () => {
    const rows = buildStory(marketRun).boxes.find((b) => b.id === "ingredients")!.detail!.rows;
    expect(rows.find((r) => r.label === "Oat Milk")!.value).toBe("need 9 L + 1 L normal use · on hand 5.9 L · arriving 0 L · short 4.1 L");
    expect(rows.find((r) => r.label === "Whole Milk")!.value).toBe("need 40 L + 4.8 L normal use · on hand 7.6 L · arriving 36 L · short 1.2 L");
    expect(rows.find((r) => r.label === "Almond Milk")!.value).toBe("need 0.4 L · on hand 5.2 L · arriving 0 L · ok");
  });
});

// ---------- "In short" from the plan and its replans ---------------------------------

const ack = (headline: string, details: string[], tone: "done" | "waiting" | "awaiting" | "problem", next_step: "find_alternative" | null = null) =>
  ({ headline, details, caveat: null, tone, next_step });

const order = marketRun.nodes.find((n) => n.node_id === "order")!;
const barista = marketRun.nodes.find((n) => n.node_id === "add_barista")!;

/** Bay Dairy refused; Willow Ridge took the order in a replan; the barista shift was added. */
function afterReplan(willow: "delivered" | "confirmed" | "sent"): [AgentRun, Record<string, AgentRun>] {
  const parent: AgentRun = {
    ...marketRun,
    status: "completed",
    nodes: marketRun.nodes.map((n) =>
      n === order
        ? { ...n, status: "done", replanned_in: "RUN-2", result: { supplier_name: "Bay Dairy Co.", lines: [] },
            acknowledgement: ack("Bay Dairy Co. can't take this order", ["Bay Dairy Co. is out of oat milk until Mon, Nov 9."], "problem", "find_alternative") }
        : n === barista
          ? { ...n, status: "done", result: { shift_id: "S9" },
              acknowledgement: ack("Ethan Patel is on the schedule", ["", "Sun, Nov 8 staffing now: 8 AM–2 PM: 4 of 4"], "done") }
          : n,
    ),
  };
  const child: AgentRun = {
    ...marketRun,
    run_id: "RUN-2",
    model: "muse-wonder-1.0",
    parent_run_id: "RUN-1",
    nodes: [{
      ...order, node_id: "resend", status: "done", external_status: willow === "sent" ? "sent" : willow === "delivered" ? "received" : "confirmed",
      result: { supplier_name: "Willow Ridge Dairy Supply", status: "sent", lines: [
        { ingredient_id: "OAT_MILK", quantity: "6000", unit: "mL", packs: 1, pack_name: "case of 6 × 1 L" },
        { ingredient_id: "WHOLE_MILK", quantity: "12000", unit: "mL", packs: 2, pack_name: "case of 6 × 1 L" },
      ] },
      acknowledgement:
        willow === "delivered"
          ? ack("Willow Ridge Dairy Supply delivered the order", ["Order PO-1042", "Checked in, slip PS-58214: everything matched"], "done")
          : willow === "confirmed"
            ? ack("Willow Ridge Dairy Supply confirmed – awaiting delivery", ["Order PO-1042", "Total $77.40", "Arriving Sun, Nov 8 by 6 AM"], "awaiting")
            : ack("Order sent to Willow Ridge Dairy Supply (simulated)", ["Order PO-1042"], "waiting"),
    }],
  };
  return [parent, { "RUN-2": child }];
}

describe("In short", () => {
  it("reads from the checks while the changes wait for the owner", () => {
    const story = buildStory(marketRun);
    expect(story.summaryTitle).toBe("In short");
    expect(story.allSet).toBe(false);
  });

  it("follows the replan: the new supplier's order and the approved shift, then 'All set for Sunday'", () => {
    const [parent, children] = afterReplan("confirmed");
    const story = buildStory(parent, children);
    expect(story.summary).toEqual([
      "1 case oat milk (6 × 1 L) and 2 cases whole milk (6 × 1 L) from Willow Ridge Dairy Supply: confirmed, awaiting delivery Sun, Nov 8 by 6 AM.",
      "Ethan Patel is on the schedule (Sun, Nov 8, 8 AM–10 AM).",
      "Sun, Nov 8 staffing now: 8 AM–2 PM: 4 of 4.",
    ]);
    expect(story.summary.join(" ")).not.toContain("Bay Dairy");
    expect(story.allSet).toBe(true);
    expect(story.summaryTitle).toBe("All set for Sunday");
    expect(story.boxes.find((b) => b.id === "c:RUN-2:resend")!.model).toBe("muse-wonder-1.0");
    // Confirmed but not delivered yet: in progress (yellow), not a green tick.
    expect(story.boxes.find((b) => b.id === "c:RUN-2:resend")!.state).toBe("awaiting");
  });

  it("turns the order green once the delivery check confirms it", () => {
    const [parent, children] = afterReplan("delivered");
    const story = buildStory(parent, children);
    expect(story.summary[0]).toBe("1 case oat milk (6 × 1 L) and 2 cases whole milk (6 × 1 L) from Willow Ridge Dairy Supply: delivered.");
    expect(story.boxes.find((b) => b.id === "c:RUN-2:resend")!.state).toBe("done");
    expect(story.summaryTitle).toBe("All set for Sunday");
  });

  it("is not all set while the new supplier hasn't confirmed", () => {
    const [parent, children] = afterReplan("sent");
    const story = buildStory(parent, children);
    expect(story.summary[0]).toBe(
      "1 case oat milk (6 × 1 L) and 2 cases whole milk (6 × 1 L) sent to Willow Ridge Dairy Supply; waiting for them to confirm.",
    );
    expect(story.allSet).toBe(false);
    expect(story.summaryTitle).toBe("In short");
  });

  it("keeps the staffing check line until the shift is decided", () => {
    const [parent, children] = afterReplan("confirmed");
    const waiting = { ...parent, nodes: parent.nodes.map((n) => (n.node_id === "add_barista" ? barista : n)) };
    const story = buildStory(waiting, children);
    expect(story.summary.some((l) => l.startsWith("Ethan Patel"))).toBe(false);
    expect(story.allSet).toBe(false);
  });
});
