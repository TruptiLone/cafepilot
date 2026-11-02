import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatAnswer, ChatRow } from "../../api/agent";
import ChatPanel, { EXAMPLES } from "./ChatPanel";

const AS_OF = "2026-10-29T18:00:00-07:00";
const SPARK_REPLY = "Yes, we have 9 banana nut muffins left, under the reorder point of 12. Want me to plan an order?";

function reply(overrides: Partial<ChatAnswer> = {}): ChatAnswer {
  return {
    intent: "answer",
    message: "q",
    channel: "text",
    model: "muse-spark-1.3",
    latency_ms: 7140,
    as_of: AS_OF,
    lines: ["Below reorder point: Banana Nut Muffin (9 left, reorder at 12)."],
    rows: [{ name: "Banana Nut Muffin", quantity: "9", unit: "each", as_of: AS_OF, estimated: false, detail: "below reorder point 12" }],
    lookups: [{ action: "get_stock_estimates", summary: "x", error: null }],
    plan_request: null,
    notes: [],
    reply: SPARK_REPLY,
    reply_by: "muse",
    follow_ups: [],
    ...overrides,
  };
}

function stock(name: string): ChatRow {
  return { name, quantity: "10", unit: "each", as_of: AS_OF, estimated: false, detail: null };
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

async function askTyped(question: string) {
  await userEvent.type(screen.getByLabelText("Ask Muse a question"), `${question}{enter}`);
}

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
    expect(await screen.findByText(SPARK_REPLY)).toBeTruthy();
  });

  it("leads with the reply, then the rows, then model and latency", async () => {
    respond(reply());
    render(<ChatPanel onPlan={() => undefined} />);
    await askTyped("do you have banana nut muffins");
    const first = await screen.findByText(SPARK_REPLY);
    const card = first.closest(".mz-a") as HTMLElement;
    expect(card.firstElementChild).toBe(first);
    expect(within(card).getByRole("rowheader", { name: "Banana Nut Muffin" })).toBeTruthy();
    expect(within(card).getByText("Muse Spark 1.3 · 7.1 s")).toBeTruthy();
    expect(card.closest(".mz-a__wrap")!.querySelector(".mz-a__icon")).toBeTruthy();  // Muse's icon beside the reply
  });

  it("says when code wrote the answer because Spark's numbers didn't check out", async () => {
    respond(reply({ reply: "Below reorder point: Banana Nut Muffin (9 left, reorder at 12).", reply_by: "code" }));
    render(<ChatPanel onPlan={() => undefined} />);
    await askTyped("do you have banana nut muffins");
    expect(await screen.findByText("Muse Spark 1.3 · 7.1 s · answer written by code")).toBeTruthy();
  });

  it("always shows up to five rows, and Show all only past five", async () => {
    respond(reply({ rows: [stock("A"), stock("B"), stock("C")] }), reply({ rows: Array.from({ length: 19 }, (_, i) => stock(`Item ${i}`)) }));
    render(<ChatPanel onPlan={() => undefined} />);
    await askTyped("three things");
    await screen.findAllByText(SPARK_REPLY);
    expect(screen.getAllByRole("rowheader")).toHaveLength(3);
    expect(screen.queryByText(/Show all/)).toBeNull();
    await askTyped("everything");
    await screen.findByText("Show all 19");
    expect(screen.getAllByRole("rowheader")).toHaveLength(3 + 5);
    await userEvent.click(screen.getByText("Show all 19"));
    expect(screen.getAllByRole("rowheader")).toHaveLength(3 + 19);
  });

  it("puts notes in a small line after the rows", async () => {
    respond(reply({ notes: ["Not sure about Tea Bags."] }));
    render(<ChatPanel onPlan={() => undefined} />);
    await askTyped("are we low on anything");
    const note = await screen.findByText("Not sure about Tea Bags.");
    expect(note.className).toBe("mz-a__note");
    const table = (note.closest(".mz-a") as HTMLElement).querySelector("table")!;
    expect(table.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("follow-ups: an ask chip asks again, a plan chip hands off to the plan flow", async () => {
    const fetchMock = respond(
      reply({ follow_ups: [
        { label: "Plan an order for banana nut muffin", kind: "plan", text: "Order more banana nut muffin before we run out" },
        { label: "Check Sunday stock", kind: "ask", text: "Do we have enough stock for Sunday?" },
      ] }),
      reply(),
    );
    const onPlan = vi.fn();
    render(<ChatPanel onPlan={onPlan} />);
    await askTyped("are we low on anything");
    const next = within(await screen.findByLabelText("Next steps"));
    await userEvent.click(next.getByRole("button", { name: "Plan an order for banana nut muffin" }));
    expect(onPlan).toHaveBeenCalledWith("Order more banana nut muffin before we run out");
    await userEvent.click(next.getByRole("button", { name: "Check Sunday stock" }));
    expect(sent(fetchMock, 1).message).toBe("Do we have enough stock for Sunday?");
  });

  it("sends a change to the plan flow with Plan it", async () => {
    respond(reply({ intent: "needs_plan", lines: [], rows: [], lookups: [], reply: null, plan_request: "order more oat milk" }));
    const onPlan = vi.fn();
    render(<ChatPanel onPlan={onPlan} />);
    await askTyped("order more oat milk");
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
    await askTyped("who's working Sunday");
    expect(screen.getByText("Muse is looking it up…")).toBeTruthy();
    await act(async () => finish(new Response(JSON.stringify({ detail: "Muse didn't answer within 10 seconds. Try again." }), { status: 504 })));
    expect(await screen.findByText("Muse took more than 10 seconds to answer.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(sent(fetchMock, 1).message).toBe("who's working Sunday");
    expect(await screen.findByText(SPARK_REPLY)).toBeTruthy();
  });

  it("shows an out-of-scope reply without rows", async () => {
    const text = "That's not something I can help with here. Ask about stock, sales, staff, orders or events.";
    respond(reply({ intent: "out_of_scope", rows: [], lookups: [], lines: [text], reply: text, reply_by: "code" }));
    render(<ChatPanel onPlan={() => undefined} />);
    await askTyped("capital of France");
    expect(await screen.findByText(text)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText(/answer written by code/)).toBeNull();
  });

  it("the speaker toggle reads only the reply aloud, not the table", async () => {
    const speakSpy = vi.fn();
    vi.stubGlobal("speechSynthesis", { speak: speakSpy, cancel: vi.fn() });
    vi.stubGlobal("SpeechSynthesisUtterance", class { lang = ""; constructor(public text: string) {} });
    respond(reply({ rows: [stock("A"), stock("B")] }));
    render(<ChatPanel onPlan={() => undefined} />);
    const speaker = screen.getByRole("button", { name: "Speak answers" });
    expect(speaker.getAttribute("title")).toBe("Speak answers, spoken by your browser");
    expect(speaker.getAttribute("aria-pressed")).toBe("false");
    await userEvent.click(speaker);
    expect(speaker.getAttribute("aria-pressed")).toBe("true");
    expect(window.localStorage.getItem("cafepilot-chat-speak")).toBe("1");
    await askTyped("banana nut muffins");
    await screen.findByText(SPARK_REPLY);
    expect(speakSpy).toHaveBeenCalledTimes(1);
    expect(speakSpy.mock.calls[0][0].text).toBe(SPARK_REPLY);
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
