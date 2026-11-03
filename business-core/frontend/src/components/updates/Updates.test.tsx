import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AICallRow, CafeUpdate } from "../../api/updates";
import UpdatesBell from "./UpdatesBell";
import { UpdatesProvider } from "./UpdatesProvider";

function update(overrides: Partial<CafeUpdate>): CafeUpdate {
  return { id: "u", kind: "order_sent", group: "orders", at: "2026-11-02T15:51:08-08:00", title: "t", detail: null,
    simulated: false, run_id: "RUN-1", model: null, ...overrides };
}

const SENT = update({ id: "order:1:sent", title: "Order PO-1042 sent to Bay Dairy Co.", detail: "1 case oat milk (6 × 1 L)", simulated: true });
const DECLINED = update({ id: "order:1:rejected", kind: "order_rejected", at: "2026-11-02T15:51:16-08:00",
  title: "Bay Dairy Co. declined PO-1042", detail: "Bay Dairy Co. is out of oat milk until Mon, Nov 9.", simulated: true });
const SHIFT = update({ id: "shift:s:accepted", kind: "shift_confirmed", group: "staff", at: "2026-11-02T15:50:57-08:00",
  title: "Sofia Ramirez confirmed Sun Nov 8, 8 AM–2 PM", simulated: true });
const REPLAN = update({ id: "replan:r:finished", kind: "replan_finished", group: "plans", at: "2026-11-02T15:51:06-08:00",
  title: "Muse found another way", model: "muse-wonder-1.0" });
const CALL: AICallRow = { id: "AIC-1", created_at: "2026-11-02T23:51:06Z", purpose: "replan", model: "muse-wonder-1.0",
  latency_ms: 17_200, attempts: 2, outcome: "retried", run_id: "RUN-2",
  request_summary: "Replan: Bay Dairy Co. declined, find another milk supplier", error: null };

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

/** /updates answers each poll from the list in turn (then nothing new); /ai-calls answers `calls`. */
function serve(polls: CafeUpdate[][], calls: AICallRow[] = []) {
  let poll = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/v1/agent/updates")) return json(polls[poll++] ?? []);
    if (url.startsWith("/api/v1/agent/ai-calls")) return json(calls);
    return Promise.resolve(new Response("{}", { status: 404 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderBell(toastMs = 60_000) {
  return render(
    <UpdatesProvider pollMs={15} toastMs={toastMs}>
      <UpdatesBell />
    </UpdatesProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Updates bell", () => {
  it("history on load is not news; a new change toasts, badges, and is asked for with since", async () => {
    const fetchMock = serve([[SENT], [DECLINED]]);
    renderBell();
    const toast = await screen.findByRole("status");
    expect(within(toast).getByText("Bay Dairy Co. declined PO-1042")).toBeTruthy();
    expect(within(toast).getByText("simulated reply")).toBeTruthy();
    expect(screen.queryAllByRole("status")).toHaveLength(1);  // the earlier "sent" is history: no toast
    expect(screen.getByRole("button", { name: "Updates, 1 new" })).toBeTruthy();
    const second = String(fetchMock.mock.calls[1][0]);
    expect(new URL(second, "http://x").searchParams.get("since")).toBe(SENT.at);
  });

  it("the panel groups updates and clears the badge", async () => {
    serve([[], [DECLINED, REPLAN, SHIFT]]);
    renderBell();
    await userEvent.click(await screen.findByRole("button", { name: "Updates, 3 new" }));
    const panel = screen.getByRole("dialog", { name: "Updates" });
    expect(within(panel).getAllByRole("heading").map((h) => h.textContent)).toEqual(["Staff covered", "Orders placed", "Muse's plans"]);
    expect(within(within(panel).getByRole("region", { name: "Staff covered" })).getByText(SHIFT.title)).toBeTruthy();
    expect(within(panel).getByText(/Muse Wonder 1\.0/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Updates" })).toBeTruthy();  // badge cleared
  });

  it("AI activity lists model calls in plain words", async () => {
    serve([[]], [CALL]);
    renderBell();
    await userEvent.click(screen.getByRole("button", { name: "Updates" }));
    await userEvent.click(screen.getByRole("tab", { name: "AI activity" }));
    expect(await screen.findByText("Replan: Bay Dairy Co. declined, find another milk supplier")).toBeTruthy();
    expect(screen.getByText(/Muse Wonder 1\.0 · 17\.2 s · 2 tries/)).toBeTruthy();
    expect(screen.getByText("retried")).toBeTruthy();
  });

  it("a development double mount still treats the first load as history", async () => {
    serve([[SENT], [SENT]]);  // both mounts' first polls return the same history
    render(
      <StrictMode>
        <UpdatesProvider pollMs={15}>
          <UpdatesBell />
        </UpdatesProvider>
      </StrictMode>,
    );
    await new Promise((r) => setTimeout(r, 120));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Updates" })).toBeTruthy();
  });

  it("toasts step aside while the panel is open, and say simulated supplier for a sent order", async () => {
    serve([[], [SENT]]);
    renderBell();
    expect(within(await screen.findByRole("status")).getByText("simulated supplier")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Updates, 1 new" }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(within(screen.getByRole("dialog", { name: "Updates" })).getByText("simulated supplier")).toBeTruthy();
  });

  it("toasts hide by themselves", async () => {
    serve([[], [DECLINED]]);
    renderBell(80);
    expect(await screen.findByRole("status")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });
});
