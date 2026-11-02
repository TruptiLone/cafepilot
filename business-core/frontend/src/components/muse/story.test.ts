import { describe, expect, it } from "vitest";
import type { AgentRun, DetectedEvent } from "../../api/agent";
import { buildStory } from "./story";

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
    started_at: "2026-10-29T18:00:00Z",
    finished_at: "2026-10-29T18:00:01Z",
    ...overrides,
  };
}

const marketEvent: DetectedEvent = {
  event_id: "EVT_1", title: "Santa Clara Farmers Market", category: "farmers_market",
  starts_at: "2026-11-01T16:00:00Z", ends_at: "2026-11-01T22:00:00Z",
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
  as_of: "2026-10-30T01:00:00Z",
  model: "muse-wonder-1.0",
  attempts: 1,
  edges: [],
  levels: [],
  warnings: [],
  created_at: "2026-10-29T18:37:00Z",
  updated_at: "2026-10-29T18:37:00Z",
  event: marketEvent,
  nodes: [
    step({ node_id: "demand", action: "estimate_event_demand" }),
    step({ node_id: "stock", action: "get_stock_estimates" }),
    step({ node_id: "shortfall", action: "calculate_shortfall", depends_on: ["demand", "stock"], result: {
      lines: [
        { name: "Oat Milk", unit: "mL", status: "short", shortfall: "4100", packs: 1, pack_name: "case of 6 × 1 L", needed_with_buffer: "9000" },
        { name: "Whole Milk", unit: "mL", status: "short", shortfall: "1200", packs: 1, pack_name: "case of 6 × 1 L", needed_with_buffer: "40000" },
        { name: "Almond Milk", unit: "mL", status: "enough", shortfall: null, packs: null, pack_name: "case of 6 × 1 L", needed_with_buffer: "350" },
      ],
    } }),
    step({ node_id: "suppliers", action: "find_suppliers", depends_on: ["shortfall"] }),
    step({ node_id: "order", action: "propose_supplier_order", depends_on: ["shortfall", "suppliers"], ...write }),
    step({ node_id: "coverage", action: "coverage_report" }),
    step({ node_id: "people", action: "list_employees" }),
    step({ node_id: "add_barista", action: "schedule_shift", depends_on: ["coverage", "people"], ...write,
      resolved_args: { employee_id: "DEMO_EMP_004", start_at: "2026-11-01T08:00:00-08:00", end_at: "2026-11-01T10:00:00-08:00" } }),
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
