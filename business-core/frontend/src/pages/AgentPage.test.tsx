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


afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

const decisionBox = async (title: string) => {
  const head = await screen.findByRole("button", { name: new RegExp(title) });
  return head.closest(".sg-box") as HTMLElement;
};

describe("AgentPage", () => {
  it("lists events with what Muse watches for, and plans one", async () => {
    const fetchMock = stubFetch((url, init) => {
      if (url === "/api/v1/agent/events/EVT_1/runs" && init?.method === "POST") return jsonResponse({ planning: planning(), run });
      return jsonResponse(run);
    }, [marketEvent]);
    renderPage();

    const events = await screen.findByRole("region", { name: "Events Muse found" });
    expect(within(events).getByText(/Muse watches what's happening near the café/)).toBeTruthy();
    expect(within(events).getByText(/0.2 miles away · usually much busier/)).toBeTruthy();
    await userEvent.click(within(events).getByRole("button", { name: "Let's plan ✨" }));

    expect(await screen.findByRole("heading", { name: /Muse's plan/ })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/v1/agent/events/EVT_1/runs" && init?.method === "POST")).toBe(true);
  });

  it("opens the existing plan for an event that was already checked", async () => {
    stubFetch(() => jsonResponse(run), [{ ...marketEvent, plan_state: "needs_ok", run_id: "RUN-1", latest_run_id: "RUN-1" }]);
    renderPage();
    const events = await screen.findByRole("region", { name: "Events Muse found" });
    expect(within(events).getByText("Needs your OK")).toBeTruthy();
    await userEvent.click(within(events).getByRole("button", { name: "Open plan" }));
    expect(await screen.findByRole("heading", { name: /Muse's plan/ })).toBeTruthy();
  });

  it("groups the steps into owner-friendly boxes with the decision inside the graph", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    expect(await screen.findByRole("heading", { name: /Muse's plan/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ingredients" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Staff" })).toBeTruthy();
    const add = await decisionBox("Add Ethan");
    expect(within(add).getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(add.getAttribute("data-s")).toBe("wait");
    expect(screen.getByText("3 of 3 checks done · 1 decision waiting · hover a step for details")).toBeTruthy();
  });

  it("shows the details behind a box when it is clicked", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(await screen.findByRole("button", { name: "Staff" }));
    const tip = screen.getByRole("tooltip");
    expect(within(tip).getByText("Staff for the event hours")).toBeTruthy();
    expect(within(tip).getByText(/coverage_report/)).toBeTruthy();
  });

  it("explains why Muse suggests a change", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(await screen.findByRole("button", { name: /Add Ethan/ }));
    const tip = screen.getByRole("tooltip");
    expect(within(tip).getByText("Add a barista for the rush")).toBeTruthy();
    expect(within(tip).getByText(/coverage is below 2 people/)).toBeTruthy();
  });

  it("approves a change inside the graph and shows what happened", async () => {
    const fetchMock = mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(within(await decisionBox("Add Ethan")).getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Ethan Patel is on the schedule")).toBeTruthy();
    expect((await decisionBox("Add Ethan")).getAttribute("data-s")).toBe("done");
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/nodes/add_barista/decision"));
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ decision: "approved", channel: "web" });
  });

  it("declines a change", async () => {
    const declined: AgentRun = {
      ...run,
      status: "completed_with_issues",
      nodes: run.nodes.map((node) => (node.node_id === "add_barista" ? { ...node, status: "rejected", error: "rejected by owner" } : node)),
    };
    const fetchMock = mockApi(undefined, declined);
    renderPage("/agent?run=RUN-1");
    await userEvent.click(within(await decisionBox("Add Ethan")).getByRole("button", { name: "Decline" }));
    await waitFor(async () => expect((await decisionBox("Add Ethan")).getAttribute("data-s")).toBe("declined"));
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
          resolved_args: { lines: [{ ingredient_id: "OAT_MILK", quantity: "2000", unit: "mL" }], requested_delivery_at: "2026-11-01T06:00:00-08:00" },
        },
      ],
    };
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/RUN-1/decision") && init?.method === "POST") return jsonResponse(completedRun);
      return jsonResponse(two);
    });
    renderPage("/agent?run=RUN-1");
    expect(await decisionBox("Order milk")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Approve all" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Approve all" })).toBeNull());
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/RUN-1/decision"))).toBe(true);
  });

  it("keeps the change waiting and explains when a decision fails", async () => {
    mockApi(undefined, { status: 409, detail: "add_barista is done, not waiting for approval" });
    renderPage("/agent?run=RUN-1");
    await userEvent.click(within(await decisionBox("Add Ethan")).getByRole("button", { name: "Approve" }));
    expect((await screen.findByRole("alert")).textContent).toContain("not waiting for approval");
    expect(within(await decisionBox("Add Ethan")).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("changes the person and start time before approving a shift", async () => {
    const fetchMock = mockApi();
    renderPage("/agent?run=RUN-1");
    const box = await decisionBox("Add Ethan");
    await userEvent.click(within(box).getByRole("button", { name: "Change" }));
    const form = within(box).getByRole("form", { name: "Change the shift" });
    const start = within(form).getByLabelText("Starts") as HTMLInputElement;
    expect(start.value).toBe("07:30");
    await userEvent.clear(start);
    await userEvent.type(start, "08:00");
    await userEvent.click(within(form).getByRole("button", { name: "Approve with changes" }));

    await screen.findByText("Ethan Patel is on the schedule");
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/nodes/add_barista/decision"));
    expect(JSON.parse(String(post?.[1]?.body)).edited_args).toEqual({
      employee_id: "DEMO_EMP_004",
      start_at: "2026-11-01T08:00:00-08:00",
      end_at: "2026-11-01T14:30:00-08:00",
    });
  });

  it("switches to the technical view and remembers the choice", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await screen.findByRole("heading", { name: /Muse's plan/ });
    await userEvent.click(screen.getByRole("button", { name: "Technical view" }));
    expect(document.querySelector(".mz-map svg.mz-wires")).toBeTruthy();
    expect(window.localStorage.getItem("cafepilot-plan-view")).toBe("technical");
  });

  it("sends a typed request and shows the new plan", async () => {
    const fetchMock = mockApi({ planning: planning(), run });
    renderPage();
    await userEvent.type(screen.getByLabelText("Ask Muse"), "Farmers market Sunday, order milk");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("heading", { name: /Muse's plan/ })).toBeTruthy();
    const post = fetchMock.mock.calls.find(([url, init]) => url === "/api/v1/agent/runs" && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ cafe_id: "CAFE001", query: "Farmers market Sunday, order milk" });
  });

  it("explains a plan that failed the safety checks", async () => {
    mockApi({
      planning: planning({}, [{ code: "missing_prerequisite", node_id: "add", message: "schedule_shift must come after coverage_report" }] as never),
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

  it("shows the café panel and folds it into badges", async () => {
    mockApi();
    renderPage();
    const panel = await screen.findByRole("region", { name: "Café status" });
    expect(await within(panel).findByText("Short 1.2 L")).toBeTruthy();
    await userEvent.click(within(panel).getByRole("button", { name: "Hide café status" }));
    const mini = screen.getByRole("region", { name: "Café status" });
    expect(within(mini).getByText("2/4")).toBeTruthy();
    expect(window.localStorage.getItem("cafepilot-status-folded")).toBe("1");
  });

  it("when a supplier says no, Muse finds another way and the new order grows under it", async () => {
    const rejectedNode = (replannedIn: string | null) => ({
      ...run.nodes[3],
      action: "propose_supplier_order",
      status: "done" as const,
      result: { order_id: "po_1" },
      resolved_args: { lines: [{ ingredient_id: "OAT_MILK", quantity: "6000", unit: "mL" }], requested_delivery_at: "2026-11-01T06:00:00-08:00" },
      replanned_in: replannedIn,
      lifecycle: [
        { stage: "Proposed", state: "done" as const, at: null, note: null },
        { stage: "Sent to Bay Dairy Co.", state: "done" as const, at: null, note: null },
        { stage: "Rejected by supplier", state: "failed" as const, at: null, note: null },
      ],
      acknowledgement: {
        headline: "Bay Dairy Co. can't take this order",
        details: ["Bay Dairy Co. is out of oat milk until Mon, Nov 2."],
        caveat: null,
        tone: "problem" as const,
        next_step: "find_alternative" as const,
      },
    });
    const rejected = (id: string | null): AgentRun => ({ ...run, status: "completed", waiting_on: [], nodes: [...run.nodes.slice(0, 3), rejectedNode(id)] });
    const alternative: AgentRun = {
      ...run,
      run_id: "RUN-2",
      goal: "Get the milk from Willow Ridge",
      parent_run_id: "RUN-1",
      parent_node_id: "add_barista",
      nodes: [{ ...run.nodes[3], action: "propose_supplier_order", resolved_args: { lines: [{ ingredient_id: "OAT_MILK", quantity: "6000", unit: "mL" }], requested_delivery_at: "2026-11-01T06:00:00-08:00" }, summary: "Order 6 L oat milk for Sun Nov 1 6 AM from Willow Ridge Dairy Supply (simulated)" }],
    };
    let replanned = false;
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/nodes/add_barista/replan") && init?.method === "POST") {
        replanned = true;
        return jsonResponse({ planning: planning(), run: alternative });
      }
      if (url.startsWith("/api/v1/agent/runs/RUN-2")) return jsonResponse(alternative);
      return jsonResponse(rejected(replanned ? "RUN-2" : null));
    });
    renderPage("/agent?run=RUN-1");

    expect(await screen.findByText("Rejected by supplier")).toBeTruthy();
    expect(await screen.findByText(/from Willow Ridge Dairy Supply/)).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/nodes/add_barista/replan"))).toHaveLength(1);
    const boxes = document.querySelectorAll(".sg-box--decision");
    const waiting = Array.from(boxes).find((b) => b.getAttribute("data-s") === "wait") as HTMLElement;
    expect(within(waiting).getByRole("button", { name: "Approve" })).toBeTruthy();
  });
});
