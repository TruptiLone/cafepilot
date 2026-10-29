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
  levels: [["coverage", "stock"], ["add_barista"]],
  warnings: [],
  created_at: "2026-10-29T18:37:00Z",
  updated_at: "2026-10-29T18:37:00Z",
  nodes: [
    step({ node_id: "coverage", action: "coverage_report", reason: "Check Sunday coverage" }),
    step({}),
    step({
      node_id: "add_barista",
      action: "schedule_shift",
      side_effect: "write",
      requires_approval: true,
      reason: "Add a barista for the rush",
      only_if: "Approve only if coverage is below 2 people",
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

function mockApi(start?: StartRunResponse | { status: number; detail: string }) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/v1/agent/runs?")) return jsonResponse([summary]);
    if (url === "/api/v1/agent/runs" && init?.method === "POST") {
      if (start && "detail" in start) return jsonResponse({ detail: start.detail }, start.status);
      return jsonResponse(start);
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
});
