import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatAnswer, ChatRow } from "../../api/agent";
import ChatPanel, { EXAMPLES, rowsThatMatter } from "./ChatPanel";

const AS_OF = "2026-10-29T18:00:00-07:00";

function reply(overrides: Partial<ChatAnswer> = {}): ChatAnswer {
  return {
    intent: "answer",
    message: "q",
    channel: "text",
    model: "muse-spark-1.3",
    latency_ms: 2140,
    as_of: AS_OF,
    lines: ["Banana Nut Muffin 9 on hand. Below reorder point: Banana Nut Muffin (9 left, reorder at 12)."],
    rows: [{ name: "Banana Nut Muffin", quantity: "9", unit: "each", as_of: AS_OF, estimated: false, detail: "below reorder point 12" }],
    lookups: [{ action: "get_stock_estimates", summary: "x", error: null }],
    plan_request: null,
    ...overrides,
  };
}

function stock(name: string, low = false): ChatRow {
  return { name, quantity: "10", unit: "each", as_of: AS_OF, estimated: false, detail: low ? "below reorder point 12" : null };
}

function respond(...bodies: (ChatAnswer | { status: number; detail: string })[]) {
  const fetchMock = vi.fn();
  for (const body of bodies) {
    const status = "status" in body ? body.status : 200;
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(new Response(JSON.stringify(status === 200 ? body : { detail: (body as { detail: string }).detail }),
        { status, headers: { "Content-Type": "application/json" } })),
    );
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const sent = (fetchMock: ReturnType<typeof vi.fn>, call = 0) => JSON.parse(String(fetchMock.mock.calls[call][1]?.body));

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChatPanel", () => {
  it("offers four example questions and asks one when tapped", async () => {
    const fetchMock = respond(reply());
    render(<ChatPanel asOf={AS_OF} onPlan={() => undefined} />);
    const examples = within(screen.getByLabelText("Example questions")).getAllByRole("button");
    expect(examples.map((b) => b.textContent)).toEqual(EXAMPLES);
    await userEvent.click(examples[0]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/agent/chat");
    expect(sent(fetchMock)).toEqual({ message: EXAMPLES[0], channel: "text", cafe_id: "CAFE001", as_of: AS_OF });
    expect(await screen.findByText(/Banana Nut Muffin 9 on hand/)).toBeTruthy();
  });

  it("shows the answer sentence first, then the rows, then model and latency", async () => {
    respond(reply());
    render(<ChatPanel asOf={AS_OF} onPlan={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Ask Muse a question"), "do you have banana nut muffins{enter}");
    const line = await screen.findByText(/Banana Nut Muffin 9 on hand/);
    const card = line.closest(".mz-a") as HTMLElement;
    expect(card.firstElementChild).toBe(line);
    expect(within(card).getByRole("rowheader", { name: "Banana Nut Muffin" })).toBeTruthy();
    expect(within(card).getByText("Muse Spark 1.3 · 2.1 s")).toBeTruthy();
  });

  it("shows only the rows that matter, with a way to see all", async () => {
    const rows = [stock("Oat Milk", true), ...Array.from({ length: 17 }, (_, i) => stock(`Item ${i}`)), stock("Banana Nut Muffin", true)];
    respond(reply({ rows }));
    render(<ChatPanel onPlan={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Ask Muse a question"), "are we low on anything{enter}");
    await screen.findByText("Show all 19");
    expect(screen.getAllByRole("rowheader").map((r) => r.textContent)).toEqual(["Oat Milk", "Banana Nut Muffin"]);
    await userEvent.click(screen.getByText("Show all 19"));
    expect(screen.getAllByRole("rowheader")).toHaveLength(19);
  });

  it("keeps the first five rows when nothing is low", () => {
    const { shown, hidden } = rowsThatMatter(Array.from({ length: 8 }, (_, i) => stock(`Item ${i}`)));
    expect([shown.length, hidden]).toEqual([5, 3]);
  });

  it("sends a change to the plan flow with Plan it", async () => {
    respond(reply({ intent: "needs_plan", lines: [], rows: [], lookups: [], plan_request: "order more oat milk" }));
    const onPlan = vi.fn();
    render(<ChatPanel onPlan={onPlan} />);
    await userEvent.type(screen.getByLabelText("Ask Muse a question"), "order more oat milk{enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Plan it" }));
    expect(onPlan).toHaveBeenCalledWith("order more oat milk");
    expect(screen.getByText(/needs a plan you approve/)).toBeTruthy();
  });

  it("says when Muse is looking, and explains a timeout with a way to try again", async () => {
    let finish: (r: Response) => void = () => undefined;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => (finish = resolve)))
      .mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(reply()), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    render(<ChatPanel onPlan={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Ask Muse a question"), "who's working Sunday{enter}");
    expect(screen.getByText("Muse is looking it up…")).toBeTruthy();
    await act(async () => finish(new Response(JSON.stringify({ detail: "Muse didn't answer within 10 seconds. Try again." }), { status: 504 })));
    expect(await screen.findByText("Muse took more than 10 seconds to answer.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(sent(fetchMock, 1).message).toBe("who's working Sunday");
    expect(await screen.findByText(/Banana Nut Muffin 9 on hand/)).toBeTruthy();
  });

  it("shows an out-of-scope reply without rows", async () => {
    respond(reply({ intent: "out_of_scope", rows: [], lookups: [],
      lines: ["That's not something I can help with here. Ask about stock, sales, staff, orders or events."] }));
    render(<ChatPanel onPlan={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Ask Muse a question"), "capital of France{enter}");
    expect(await screen.findByText(/not something I can help with/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("reads answers aloud with the browser's voice when switched on", async () => {
    const speakSpy = vi.fn();
    vi.stubGlobal("speechSynthesis", { speak: speakSpy, cancel: vi.fn() });
    vi.stubGlobal("SpeechSynthesisUtterance", class { lang = ""; constructor(public text: string) {} });
    respond(reply());
    render(<ChatPanel onPlan={() => undefined} />);
    expect(screen.getByText("· Spoken by your browser")).toBeTruthy();
    await userEvent.click(screen.getByLabelText(/Speak answers/));
    expect(window.localStorage.getItem("cafepilot-chat-speak")).toBe("1");
    await userEvent.type(screen.getByLabelText("Ask Muse a question"), "banana nut muffins{enter}");
    await screen.findByText(/Banana Nut Muffin 9 on hand/);
    expect(speakSpy).toHaveBeenCalledTimes(1);
    expect(speakSpy.mock.calls[0][0].text).toBe(reply().lines[0]);
  });

  it("asks by voice: what was heard is sent with channel voice", async () => {
    let recognizer: { onresult: ((e: unknown) => void) | null; onend: (() => void) | null } | null = null;
    vi.stubGlobal("SpeechRecognition", class {
      lang = ""; interimResults = false; onresult = null; onend = null;
      constructor() { recognizer = this as never; }
      start() {} stop() {}
    });
    const fetchMock = respond(reply());
    render(<ChatPanel onPlan={() => undefined} />);
    await userEvent.click(screen.getByRole("button", { name: "Ask by voice" }));
    await act(async () => {
      recognizer!.onresult!({ results: [[{ transcript: "do we have banana nut muffins" }]] });
      recognizer!.onend!();
    });
    expect(sent(fetchMock)).toMatchObject({ message: "do we have banana nut muffins", channel: "voice" });
    expect(await screen.findByText("🎤")).toBeTruthy();
  });
});
