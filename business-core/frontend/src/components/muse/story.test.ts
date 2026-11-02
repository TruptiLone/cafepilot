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
    step({ node_id: "shortfall", action: "calculate_shortfall", depends_on: ["demand", "stock"] }),
    step({ node_id: "suppliers", action: "find_suppliers", depends_on: ["shortfall"] }),
    step({ node_id: "order", action: "propose_supplier_order", depends_on: ["shortfall", "suppliers"], ...write }),
    step({ node_id: "coverage", action: "coverage_report" }),
    step({ node_id: "people", action: "list_employees" }),
    step({ node_id: "add_barista", action: "schedule_shift", depends_on: ["coverage", "people"], ...write,
      resolved_args: { employee_id: "DEMO_EMP_004", start_at: "2026-11-01T08:00:00-08:00", end_at: "2026-11-01T10:00:00-08:00" } }),
  ],
};

const edge = (story: ReturnType<typeof buildStory>, from: string, to: string) =>
  story.edges.find((e) => e.from === from && e.to === to);

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
