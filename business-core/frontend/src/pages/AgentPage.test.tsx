import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentRun, StartRunResponse } from "../api/agent";
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

function mockApi(
  start?: StartRunResponse | { status: number; detail: string },
  decision: AgentRun | { status: number; detail: string } = completedRun,
) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/v1/agent/runs?")) return jsonResponse([summary]);
    if (url === "/api/v1/agent/runs" && init?.method === "POST") {
      if (start && "detail" in start) return jsonResponse({ detail: start.detail }, start.status);
      return jsonResponse(start);
    }
    if (url.includes("/decision") && init?.method === "POST") {
      if ("detail" in decision) return jsonResponse({ detail: decision.detail }, decision.status);
      return jsonResponse(decision);
    }
    if (url.startsWith("/api/v1/agent/runs/RUN-1")) return jsonResponse(run);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
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
});

describe("AgentPage", () => {
  it("shows a saved run as stages of tickets with plain-language names", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");

    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
    const stages = screen.getAllByRole("listitem", { name: /Stage/ });
    expect(stages).toHaveLength(2);
    expect(within(stages[0]).getByText("Check staff coverage")).toBeTruthy();
    expect(within(stages[0]).getByText("List staff")).toBeTruthy();
    expect(within(stages[0]).getByText("Check stock")).toBeTruthy();
    expect(within(stages[1]).getByText("Changes for your OK")).toBeTruthy();
    expect(within(stages[1]).getByText("Needs your OK")).toBeTruthy();
    expect(screen.getByText("Waiting for you: 1 change")).toBeTruthy();
    expect(screen.getByText("40 whole-milk and 20 oat-milk lattes")).toBeTruthy();
    expect(screen.getByText(/1 change waiting for you/)).toBeTruthy();
  });

  it("opens step details with the condition and what it will do", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    await userEvent.click(await screen.findByRole("button", { name: /Add a shift/ }));

    const detail = screen.getByRole("complementary", { name: "Details for Add a shift" });
    expect(within(detail).getByText("coverage is below 2 people")).toBeTruthy();
    expect(within(detail).getByText(/Waits for: coverage/)).toBeTruthy();
    expect(within(detail).getByText(/Action: schedule_shift/)).toBeTruthy();
    await userEvent.click(within(detail).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary", { name: /Details for/ })).toBeNull();
  });

  it("sends the request and shows the new run", async () => {
    const fetchMock = mockApi({ planning: planning(), run });
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Use the farmers market example" }));
    await userEvent.click(screen.getByRole("button", { name: "Make a plan" }));

    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ cafe_id: "CAFE001" });
    expect(JSON.parse(String(post?.[1]?.body)).query).toContain("Farmers market");
  });

  it("explains a plan that failed the safety checks", async () => {
    mockApi({
      planning: planning({}, [
        { code: "missing_prerequisite", node_id: "add", message: "schedule_shift must come after coverage_report" },
      ] as never),
      run: null,
    });
    renderPage();
    await userEvent.type(screen.getByLabelText("What do you need?"), "add a barista");
    await userEvent.click(screen.getByRole("button", { name: "Make a plan" }));

    expect(await screen.findByText("The plan didn't pass CaféPilot's safety checks")).toBeTruthy();
    expect(screen.getByText("schedule_shift must come after coverage_report")).toBeTruthy();
  });

  it("shows the planner's clarification question", async () => {
    mockApi({ planning: planning({ status: "needs_clarification", question: "Which day is the event?" }), run: null });
    renderPage();
    await userEvent.type(screen.getByLabelText("What do you need?"), "prep for the event");
    await userEvent.click(screen.getByRole("button", { name: "Make a plan" }));
    expect(await screen.findByText("Which day is the event?")).toBeTruthy();
  });

  it("shows the server's reason when planning is unavailable", async () => {
    mockApi({ status: 503, detail: "MODEL_API_KEY is not set in business-core/.env" });
    renderPage();
    await userEvent.type(screen.getByLabelText("What do you need?"), "anything");
    await userEvent.click(screen.getByRole("button", { name: "Make a plan" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("MODEL_API_KEY"));
  });

  it("shows the change in plain words with the evidence to decide", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
    expect(within(panel).getByText("Schedule Ethan Patel for Sun, Nov 1, 7:30 AM–2:30 PM")).toBeTruthy();
    expect(within(panel).getByText("coverage is below 2 people")).toBeTruthy();
    expect(within(panel).getByText("1 of 1 hours are short: as few as 0 of 2 people scheduled.")).toBeTruthy();
    expect(within(panel).queryByRole("button", { name: "Approve all" })).toBeNull();
  });

  it("approves a change and shows what happened", async () => {
    const fetchMock = mockApi();
    renderPage("/agent?run=RUN-1");
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
    await userEvent.click(within(panel).getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("region", { name: "What happened" })).toBeTruthy();
    expect(screen.getByText("Ethan Patel is on the schedule")).toBeTruthy();
    expect(screen.getByRole("link", { name: "See it in Staffing" }).getAttribute("href")).toBe("/staffing");
    expect(screen.queryByRole("region", { name: /needs your OK/ })).toBeNull();
    expect(screen.getByText("Done", { selector: ".run-status" })).toBeTruthy();
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
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
    await userEvent.click(within(panel).getByRole("button", { name: "Decline" }));

    expect(await screen.findByText("You declined:")).toBeTruthy();
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
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/v1/agent/runs?")) return jsonResponse([]);
      if (url.endsWith("/RUN-1/decision") && init?.method === "POST") return jsonResponse(completedRun);
      return jsonResponse(two);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage("/agent?run=RUN-1");

    const panel = await screen.findByRole("region", { name: "2 changes need your OK" });
    expect(within(panel).getByText(/Order 2 L/)).toBeTruthy();
    await userEvent.click(within(panel).getByRole("button", { name: "Approve all" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: /need your OK/ })).toBeNull());
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/RUN-1/decision"))).toBe(true);
  });

  it("keeps the change waiting and explains when a decision fails", async () => {
    mockApi(undefined, { status: 409, detail: "add_barista is done, not waiting for approval" });
    renderPage("/agent?run=RUN-1");
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
    await userEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("not waiting for approval");
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("changes the person and start time before approving a shift", async () => {
    const fetchMock = mockApi();
    renderPage("/agent?run=RUN-1");
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
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
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
    await userEvent.click(within(panel).getByRole("button", { name: "Change" }));
    await userEvent.click(within(panel).getByRole("button", { name: "Approve with changes" }));

    expect((await within(panel).findByRole("alert")).textContent).toContain("start_at must be before end_at");
    expect(within(panel).getByRole("form", { name: "Change the shift" })).toBeTruthy();
    await userEvent.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("says no changes were made when everything was declined", async () => {
    const declined: AgentRun = {
      ...run,
      status: "completed_with_issues",
      nodes: run.nodes.map((node) =>
        node.side_effect === "write" ? { ...node, status: "rejected", error: "rejected by owner" } : node,
      ),
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) =>
      String(input).startsWith("/api/v1/agent/runs?") ? jsonResponse([]) : jsonResponse(declined),
    ));
    renderPage("/agent?run=RUN-1");
    expect(await screen.findByText("No changes made", { selector: ".run-status" })).toBeTruthy();
  });

  it("acknowledges an approved change with details and Muse's closing line", async () => {
    mockApi();
    renderPage("/agent?run=RUN-1");
    const panel = await screen.findByRole("region", { name: "One change needs your OK" });
    await userEvent.click(within(panel).getByRole("button", { name: "Approve" }));

    const outcome = await screen.findByRole("region", { name: "What happened" });
    expect(within(outcome).getByText("Ethan Patel is on the schedule")).toBeTruthy();
    expect(within(outcome).getByText("Sun Nov 1 staffing now: 8 AM–12 PM: 3 of 4")).toBeTruthy();
    expect(within(outcome).getByText(/hasn't been notified yet/)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Done. Ethan Patel is on the schedule");
  });

  it("starts a plan from a detected event", async () => {
    const event = {
      event_id: "EVT_1", title: "Market", category: "farmers_market", starts_at: "", ends_at: "",
      location: null, distance_m: 250, source: "Santa Clara community events (demo)", is_demo: true,
      muse_message: "Santa Clara Farmers Market: Sun Nov 1. The last 6 editions brought +19 to +53 extra drinks.",
      expected: { confidence: "good", examples: 6, normal_drinks: 96, extra_drinks_low: 19,
        extra_drinks_typical: 35, extra_drinks_high: 53, extra_food_typical: 7, basis: "Compared the last 6 editions." },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/v1/agent/events?")) return jsonResponse([event]);
      if (url === "/api/v1/agent/events/EVT_1/runs" && init?.method === "POST")
        return jsonResponse({ planning: planning(), run });
      if (url.startsWith("/api/v1/agent/runs?")) return jsonResponse([]);
      return jsonResponse(run);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const strip = await screen.findByRole("region", { name: "Muse found 1 event coming up" });
    expect(within(strip).getByText(/The last 6 editions brought/)).toBeTruthy();
    expect(within(strip).getByText(/Source: Santa Clara community events \(demo\)/)).toBeTruthy();
    await userEvent.click(within(strip).getByRole("button", { name: "Yes, check" }));
    expect(await screen.findByRole("heading", { name: run.goal })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/v1/agent/events/EVT_1/runs" && init?.method === "POST")).toBe(true);
  });
});
