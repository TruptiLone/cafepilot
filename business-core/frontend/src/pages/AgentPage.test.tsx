import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentRun, CafeStatus, DetectedEvent, StartRunResponse } from "../api/agent";
import AgentPage from "./AgentPage";

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
    result: [{ ingredient_id: "OAT_MILK", estimated_quantity: "4650.000" }],
    error: null,
    replay_of: null,
    started_at: "2026-10-29T18:00:00Z",
    finished_at: "2026-10-29T18:00:01Z",
    ...overrides,
  };
}

const run: AgentRun = {
  run_id: "RUN-1",
  cafe_id: "CAFE001",
  query: "Farmers market Sunday",
  goal: "Prep milk and staffing for Sunday",
  assumptions: ["40 whole-milk and 20 oat-milk lattes"],
  status: "awaiting_approval",
  as_of: "2026-10-30T01:00:00Z",
  model: "muse-spark-1.3",
  attempts: 1,
  edges: [{ source: "coverage", target: "add_barista", kind: "safety" }],
  levels: [["coverage", "staff", "stock"], ["add_barista"]],
  warnings: [],
  created_at: "2026-10-29T18:37:00Z",
  updated_at: "2026-10-29T18:37:00Z",
  nodes: [
    step({
      node_id: "coverage",
      action: "coverage_report",
      reason: "Check Sunday coverage",
      result: {
        hours: [
          { hour_start: "2026-11-01T08:00:00-08:00", scheduled_employees: 0, required_staff: 2, coverage_status: "understaffed" },
        ],
      },
    }),
    step({}),
    step({
      node_id: "staff",
      action: "list_employees",
      reason: "Find baristas",
      result: [{ employee_id: "DEMO_EMP_004", name: "Ethan Patel", role: "barista" }],
    }),
    step({
      node_id: "add_barista",
      action: "schedule_shift",
      side_effect: "write",
      requires_approval: true,
      reason: "Add a barista for the rush",
      only_if: "Approve only if coverage is below 2 people",
      resolved_args: {
        cafe_id: "CAFE001",
        employee_id: "DEMO_EMP_004",
        start_at: "2026-11-01T07:30:00-08:00",
        end_at: "2026-11-01T14:30:00-08:00",
      },
      depends_on: ["coverage"],
      level: 1,
      status: "awaiting_approval",
      result: null,
    }),
  ],
};

const summary = {
  run_id: "RUN-1",
  goal: run.goal,
  status: run.status,
  created_at: run.created_at,
  steps: 3,
  awaiting_approval: 1,
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

const completedRun: AgentRun = {
  ...run,
  status: "completed",
  closing_message: "Done. Ethan Patel is on the schedule. Sun Nov 1 staffing now: 8 AM–12 PM: 3 of 4.",
  nodes: run.nodes.map((node) =>
    node.node_id === "add_barista"
      ? {
          ...node,
          status: "done",
          result: { shift_id: "S9", employee_id: "DEMO_EMP_004" },
          acknowledgement: {
            headline: "Ethan Patel is on the schedule",
            details: ["Sun Nov 1, 7:30 AM–2:30 PM", "Sun Nov 1 staffing now: 8 AM–12 PM: 3 of 4"],
            caveat: "Ethan hasn't been notified yet: staff messaging isn't connected.",
          },
        }
      : node,
  ),
};

const cafeStatus: CafeStatus = {
  cafe_id: "CAFE001",
  cafe_name: "CafePilot Demo Cafe",
  as_of: "2026-10-30T01:00:00Z",
  greeting: "Good evening, CafePilot Demo Cafe.",
  date_label: "Thursday, October 29",
  focus_event_id: "EVT_1",
  focus_event_name: "Santa Clara Farmers Market",
  kpis: {
    stock: { label: "Short for Sunday", value: "2", of: null, note: "Whole milk & oat milk", tone: "warn" },
    coverage: { label: "Sunday event coverage", value: "2", of: "/ 4", note: "2 short", tone: "warn" },
    approvals: { label: "Pending approvals", value: "1", of: null, note: "From Muse", tone: "warn" },
    sales: { label: "Today's sales", value: "$776", of: null, note: "+1% vs last Thu", tone: "good" },
  },
  inventory: [
    { ingredient_id: "WHOLE_MILK", name: "Whole Milk", unit: "mL", on_hand: "3000", arriving: "36000", on_order: "0",
      on_order_status: null, needed: "40218", shortfall: "1218", status: "short", note: "Needs 40.2 L through Sun · 36 L arriving" },
  ],
  coverage: [
    { date: "2026-11-01", label: "Sun, Nov 1", shifts: 3, is_event_day: true,
      halves: [{ label: "AM", staffed: 2, required: 4 }, { label: "PM", staffed: 3, required: 4 }] },
  ],
  events: [
    { event_id: "EVT_1", name: "Santa Clara Farmers Market", starts_at: "2026-11-01T16:00:00Z", ends_at: "2026-11-01T22:00:00Z",
      note: "8 AM–2 PM · usually +35 drinks", plan_state: "new", run_id: null, latest_run_id: null },
  ],
};

const marketEvent: DetectedEvent = {
  event_id: "EVT_1", title: "Santa Clara Farmers Market", category: "farmers_market",
  starts_at: "2026-11-01T16:00:00Z", ends_at: "2026-11-01T22:00:00Z",
  location: null, distance_m: 250, source: "Santa Clara community events (demo)", is_demo: true,
  muse_message: "Santa Clara Farmers Market: Sun Nov 1. The last 6 editions brought +19 to +53 extra drinks.",
  plan_state: "new", run_id: null, latest_run_id: null,
  expected: { confidence: "good", examples: 6, normal_drinks: 96, extra_drinks_low: 19,
    extra_drinks_typical: 35, extra_drinks_high: 53, extra_food_typical: 7, basis: "Compared the last 6 editions." },
};

type Route = (url: string, init?: RequestInit) => Promise<Response> | undefined;

/** Fetch stub: café status and events always answer; `route` handles the rest. */
function stubFetch(route: Route, events: DetectedEvent[] = []) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/v1/agent/status")) return jsonResponse(cafeStatus);
    if (url.startsWith("/api/v1/agent/events?")) return jsonResponse(events);
    return route(url, init) ?? jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockApi(
  start?: StartRunResponse | { status: number; detail: string },
  decision: AgentRun | { status: number; detail: string } = completedRun,
) {
  return stubFetch((url, init) => {
    if (url === "/api/v1/agent/runs" && init?.method === "POST") {
      if (start && "detail" in start) return jsonResponse({ detail: start.detail }, start.status);
      return jsonResponse(start);
    }
    if (url.includes("/decision") && init?.method === "POST") {
      if ("detail" in decision) return jsonResponse({ detail: decision.detail }, decision.status);
      return jsonResponse(decision);
    }
    if (url.startsWith("/api/v1/agent/runs/RUN-1")) return jsonResponse(run);
    return undefined;
  });
}

function renderPage(path = "/agent") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AgentPage />
    </MemoryRouter>,
  );
}

const planning = (overrides: Partial<StartRunResponse["planning"]["plan"]> = {}, issues = []) => ({
  plan: { status: "plan" as const, goal: "g", assumptions: [], question: null, ...overrides },
  validation: { valid: issues.length === 0, issues, warnings: [] },
  attempts: 1,
  model: "muse-spark-1.3",
  latency_ms: 1000,
});

const approvals = () => screen.findByRole("region", { name: "1 change needs your OK" });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("AgentPage", () => {
  it("shows a saved plan as steps grouped into look up, work out and needs your OK", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");

    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
    const lookUp = screen.getByRole("region", { name: "Look up" });
    expect(within(lookUp).getByText("Check the team")).toBeTruthy();
    expect(within(lookUp).getByText("Check stock")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Work out" })).getByText("Who's missing")).toBeTruthy();
    const ok = screen.getByRole("region", { name: "Needs your OK" });
    expect(within(ok).getByRole("button", { name: "Review: Add a shift" })).toBeTruthy();
    expect(screen.getByText("3 of 3 checks done · 1 waiting for you")).toBeTruthy();
    expect(screen.getByText("Waiting for you")).toBeTruthy();
  });

  it("switches to the dependency map and remembers the choice", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await screen.findByRole("heading", { name: run.goal });
    await userEvent.click(screen.getByRole("button", { name: "Dependencies" }));

    expect(screen.getByRole("button", { name: "Dependencies" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("region", { name: "Look up" })).toBeNull();
    expect(document.querySelector(".mz-map svg.mz-wires")).toBeTruthy();
    expect(window.localStorage.getItem("cafepilot-plan-view")).toBe("graph");
  });

  it("opens a finished step's details", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(await screen.findByRole("button", { name: "Details: Who's missing" }));
    const detail = screen.getByRole("complementary", { name: "Details for Check staff coverage" });
    expect(within(detail).getByText(/Action: coverage_report/)).toBeTruthy();
    await userEvent.click(within(detail).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary", { name: /Details for/ })).toBeNull();
  });

  it("shows the café panel beside the plan", async () => {
    mockApi();
    renderPage();
    const panel = await screen.findByRole("region", { name: "Café status" });
    expect(await within(panel).findByText("Good evening, CafePilot Demo Cafe.")).toBeTruthy();
    expect(within(panel).getByText("Short 1.2 L")).toBeTruthy();
    expect(within(panel).getByText("Sunday event coverage")).toBeTruthy();
  });

  it("sends a typed request and shows the new plan", async () => {
    const fetchMock = mockApi({ planning: planning(), run });
    renderPage();
    await userEvent.type(screen.getByLabelText("Ask Muse"), "Farmers market Sunday, order milk");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
    const post = fetchMock.mock.calls.find(([url, init]) => url === "/api/v1/agent/runs" && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ cafe_id: "CAFE001", query: "Farmers market Sunday, order milk" });
  });

  it("explains a plan that failed the safety checks", async () => {
    mockApi({
      planning: planning({}, [
        { code: "missing_prerequisite", node_id: "add", message: "schedule_shift must come after coverage_report" },
      ] as never),
      run: null,
    });
    renderPage();
    await userEvent.type(screen.getByLabelText("Ask Muse"), "add a barista");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("The plan didn't pass CaféPilot's safety checks")).toBeTruthy();
    expect(screen.getByText("schedule_shift must come after coverage_report")).toBeTruthy();
  });

  it("shows the planner's clarification question", async () => {
    mockApi({ planning: planning({ status: "needs_clarification", question: "Which day is the event?" }), run: null });
    renderPage();
    await userEvent.type(screen.getByLabelText("Ask Muse"), "prep for the event");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Which day is the event?")).toBeTruthy();
  });

  it("shows the server's reason when planning is unavailable", async () => {
    mockApi({ status: 503, detail: "MODEL_API_KEY is not set in business-core/.env" });
    renderPage();
    await userEvent.type(screen.getByLabelText("Ask Muse"), "anything");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("MODEL_API_KEY"));
  });

  it("lists the change in plain words with the reasons behind it", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    const panel = await approvals();
    expect(within(panel).getByText("Schedule Ethan Patel for Sun, Nov 1, 7:30 AM–2:30 PM")).toBeTruthy();
    expect(within(panel).getByText("coverage is below 2 people")).toBeTruthy();
    expect(within(panel).getByText("1 of 1 hours are short: as few as 0 of 2 people scheduled.")).toBeTruthy();
    expect(within(panel).queryByRole("button", { name: "Approve all" })).toBeNull();
  });

  it("clicking a waiting step highlights its approval card", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(await screen.findByRole("button", { name: "Review: Add a shift" }));
    expect(screen.getByRole("article", { name: "Change to approve: Add a shift" }).className).toContain("is-sel");
  });

  it("approves a change and shows what happened", async () => {
    const fetchMock = mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(within(await approvals()).getByRole("button", { name: "Approve" }));

    const outcome = await screen.findByRole("region", { name: "What happened" });
    expect(within(outcome).getByText("Ethan Patel is on the schedule")).toBeTruthy();
    expect(within(outcome).getByText(/hasn't been notified yet/)).toBeTruthy();
    expect(within(outcome).getByRole("link", { name: "View in Staffing" }).getAttribute("href")).toBe("/staffing");
    expect(screen.queryByRole("region", { name: /needs your OK/ })).toBeNull();
    expect(screen.getByText(/Done. Ethan Patel is on the schedule/)).toBeTruthy();
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/nodes/add_barista/decision"));
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ decision: "approved", channel: "web" });
  });

  it("declines a change", async () => {
    const declined: AgentRun = {
      ...run,
      status: "completed_with_issues",
      nodes: run.nodes.map((node) =>
        node.node_id === "add_barista" ? { ...node, status: "rejected", error: "rejected by owner" } : node,
      ),
    };
    const fetchMock = mockApi(undefined, declined);
    renderPage("/agent?run=RUN-1");
    await userEvent.click(within(await approvals()).getByRole("button", { name: "Decline" }));

    const outcome = await screen.findByRole("region", { name: "What happened" });
    expect(within(outcome).getByText("Declined")).toBeTruthy();
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/decision"));
    expect(JSON.parse(String(post?.[1]?.body)).decision).toBe("rejected");
  });

  it("offers Approve all when several changes wait", async () => {
    const two: AgentRun = {
      ...run,
      nodes: [
        ...run.nodes,
        {
          ...run.nodes[run.nodes.length - 1],
          node_id: "order",
          action: "propose_supplier_order",
          only_if: null,
          resolved_args: {
            lines: [{ ingredient_id: "OAT_MILK", quantity: "2000", unit: "mL" }],
            requested_delivery_at: "2026-11-01T06:00:00-08:00",
          },
        },
      ],
    };
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/RUN-1/decision") && init?.method === "POST") return jsonResponse(completedRun);
      return jsonResponse(two);
    });
    renderPage("/agent?run=RUN-1");

    const panel = await screen.findByRole("region", { name: "2 changes need your OK" });
    expect(within(panel).getByText(/Order 2 L/)).toBeTruthy();
    expect(within(panel).getByText("Simulated supplier")).toBeTruthy();
    await userEvent.click(within(panel).getByRole("button", { name: "Approve all" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: /need your OK/ })).toBeNull());
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/RUN-1/decision"))).toBe(true);
  });

  it("keeps the change waiting and explains when a decision fails", async () => {
    mockApi(undefined, { status: 409, detail: "add_barista is done, not waiting for approval" });
    renderPage("/agent?run=RUN-1");
    const panel = await approvals();
    await userEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("not waiting for approval");
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("changes the person and start time before approving a shift", async () => {
    const fetchMock = mockApi();
    renderPage("/agent?run=RUN-1");
    const panel = await approvals();
    await userEvent.click(within(panel).getByRole("button", { name: "Change" }));

    const form = within(panel).getByRole("form", { name: "Change the shift" });
    expect((within(form).getByLabelText("Who") as HTMLSelectElement).value).toBe("DEMO_EMP_004");
    const start = within(form).getByLabelText("Starts") as HTMLInputElement;
    expect(start.value).toBe("07:30");
    await userEvent.clear(start);
    await userEvent.type(start, "08:00");
    await userEvent.click(within(form).getByRole("button", { name: "Approve with changes" }));

    await screen.findByRole("region", { name: "What happened" });
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/nodes/add_barista/decision"));
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      decision: "approved",
      channel: "web",
      edited_args: {
        employee_id: "DEMO_EMP_004",
        start_at: "2026-11-01T08:00:00-08:00",
        end_at: "2026-11-01T14:30:00-08:00",
      },
    });
  });

  it("keeps the change form open and explains when a change is not valid", async () => {
    mockApi(undefined, { status: 422, detail: "Your changes are not valid: start_at must be before end_at" });
    renderPage("/agent?run=RUN-1");
    const panel = await approvals();
    await userEvent.click(within(panel).getByRole("button", { name: "Change" }));
    await userEvent.click(within(panel).getByRole("button", { name: "Approve with changes" }));

    expect((await within(panel).findByRole("alert")).textContent).toContain("start_at must be before end_at");
    expect(within(panel).getByRole("form", { name: "Change the shift" })).toBeTruthy();
    await userEvent.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("starts a plan from a detected event", async () => {
    const fetchMock = stubFetch((url, init) => {
      if (url === "/api/v1/agent/events/EVT_1/runs" && init?.method === "POST") return jsonResponse({ planning: planning(), run });
      return jsonResponse(run);
    }, [marketEvent]);
    renderPage();

    const events = await screen.findByRole("region", { name: "Events Muse found" });
    await userEvent.click(within(events).getByRole("button", { name: "Open Santa Clara Farmers Market" }));
    expect(within(events).getByText(/The last 6 editions brought/)).toBeTruthy();
    await userEvent.click(within(events).getByRole("button", { name: "Check if we're ready" }));
    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/v1/agent/events/EVT_1/runs" && init?.method === "POST")).toBe(true);
  });

  it("opens the existing plan for an event that was already checked", async () => {
    stubFetch(() => jsonResponse(run), [{ ...marketEvent, plan_state: "needs_ok", run_id: "RUN-1", latest_run_id: "RUN-1" }]);
    renderPage();
    const events = await screen.findByRole("region", { name: "Events Muse found" });
    expect(within(events).getByText("Needs OK")).toBeTruthy();
    await userEvent.click(within(events).getByRole("button", { name: "Open plan" }));
    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
  });

  it("shows a rejected order honestly and asks Muse for an alternative", async () => {
    const rejected: AgentRun = {
      ...run,
      status: "completed",
      waiting_on: [],
      closing_message: "Here's where things stand: Bay Dairy Co. can't take this order. Something needs another plan.",
      nodes: run.nodes.map((node) =>
        node.node_id === "add_barista"
          ? {
              ...node,
              action: "propose_supplier_order",
              status: "done",
              result: { order_id: "po_1" },
              lifecycle: [
                { stage: "Proposed", state: "done", at: null, note: null },
                { stage: "Approved", state: "done", at: null, note: null },
                { stage: "Sent to Bay Dairy Co.", state: "done", at: null, note: null },
                { stage: "Rejected by supplier", state: "failed", at: null, note: null },
              ],
              acknowledgement: {
                headline: "Bay Dairy Co. can't take this order",
                details: ["Bay Dairy Co. can't deliver on Sundays before 9 AM (requested 6 AM).", "The milk shortfall is still open."],
                caveat: "Simulated supplier: no real order was sent.",
                tone: "problem",
                next_step: "find_alternative",
              },
            }
          : node,
      ),
    };
    const replanned: AgentRun = { ...run, run_id: "RUN-2", goal: "Get the milk from Willow Ridge", parent_run_id: "RUN-1", parent_node_id: "add_barista" };
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/nodes/add_barista/replan") && init?.method === "POST") return jsonResponse({ planning: planning(), run: replanned });
      if (url.startsWith("/api/v1/agent/runs/RUN-2")) return jsonResponse(replanned);
      return jsonResponse(rejected);
    });
    renderPage("/agent?run=RUN-1");

    const outcome = await screen.findByRole("region", { name: "What happened" });
    expect(within(outcome).getByText("Needs another plan")).toBeTruthy();
    expect(within(outcome).getByText("Rejected by supplier")).toBeTruthy();
    expect(within(outcome).getByText(/before 9 AM/)).toBeTruthy();
    await userEvent.click(within(outcome).getByRole("button", { name: "Find an alternative" }));

    expect(await screen.findByRole("heading", { name: "Get the milk from Willow Ridge" })).toBeTruthy();
    expect(screen.getByText(/a change in an earlier plan fell through/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/nodes/add_barista/replan"))).toBe(true);
  });
});
