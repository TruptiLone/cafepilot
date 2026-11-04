import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceivingPage from "./ReceivingPage";
import { lineView, tally } from "./receivingView";

const order = {
  order_id: "PO1", po_number: "PO-1042", supplier_name: "Willow Ridge Dairy Supply", status: "confirmed",
  requested_delivery_at: "2026-11-08T13:30:00Z", confirmed_delivery_at: "2026-11-08T13:30:00Z", simulated: true,
  lines: [{ line_no: 1, ingredient_id: "WHOLE_MILK", supplier_item_number: "WR-1001", description: "Whole Milk, 1 L carton",
    quantity: "12000", unit: "mL", packs: 2, pack_name: "case of 6 × 1 L" }],
};

const line = {
  line_no: 1, ingredient_id: "WHOLE_MILK", supplier_item_number: "WR-1001", description: "Whole Milk, 1 L carton",
  unit: "mL", unit_size: "1000", ordered_quantity: "12000", slip_shipped_quantity: "12000", slip_backordered_quantity: "0",
  seen_quantity: "3000", seen_may_hide_more: true, counted_quantity: null, rejected_quantity: "0", rejected_reason: null,
  accepted_quantity: null, status: "needs_count",
  message: "Muse can see 3 cartons (3 L) of whole milk, but the slip says 12 cartons (12 L). Please count it.",
  display: { ordered: "12 cartons (12 L)", shipped: "12 cartons (12 L)", seen: "3 cartons (3 L)" },
};

const receipt = {
  receipt_id: "RCV1", cafe_id: "CAFE001", status: "draft", order, slip_number: "PS-58213",
  slip_supplier: "Willow Ridge Dairy Supply", slip_po_number: "PO-1042", slip_delivery: null, has_slip_image: false,
  has_photo_image: true, photo_notes: null, model: "muse-spark-1.3", lines: [line],
  summary: { lines: 1, match: 0, needs_count: 1, problems: 0 }, credit_request: null, confirmed_at: null,
  confirmed_by: null, muse_message: line.message,
};

const reply = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ReceivingPage", () => {
  it("lists the expected delivery and asks the owner to count what Muse can't see", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).endsWith("/orders?cafe_id=CAFE001") ? reply([order]) : String(input).includes("/received?") ? reply([]) : reply(receipt),
    );
    render(<MemoryRouter><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("Willow Ridge Dairy Supply")).toBeTruthy();
    fireEvent.click(screen.getByText("Check this delivery"));

    expect(await screen.findByText("Save count")).toBeTruthy();
    expect(screen.getAllByText(/Muse can see 3 cartons/).length).toBeGreaterThan(0);
    expect((screen.getByText("Confirm what arrived") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByLabelText("One more Whole Milk"));
    fireEvent.click(screen.getByText("Save count"));
    const [, options] = fetchMock.mock.calls.at(-1)!;
    expect(JSON.parse(String((options as RequestInit).body))).toMatchObject({ counted_units: 4 });
  });

  it("opens an order's delivery check from a fixed link", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).endsWith("/orders?cafe_id=CAFE001") ? reply([order]) : String(input).includes("/received?") ? reply([]) : reply(receipt),
    );
    render(<MemoryRouter initialEntries={["/receiving?po=PO-1042"]}><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("Save count")).toBeTruthy();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toContain(
      "/api/v1/receiving/orders/PO-1042/check?cafe_id=CAFE001",
    );
  });

  it("lists the deliveries already received", async () => {
    const done = { receipt_id: "RCV2", po_number: "PO-123", supplier_name: "Willow Ridge Dairy Supply",
      order_status: "partially_received", slip_number: "PS-58216", confirmed_at: "2026-11-04T03:00:00Z", credit_ref: "CR-58216" };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).includes("/received?") ? reply([done]) : String(input).includes("/orders?") ? reply([]) : reply(receipt),
    );
    render(<MemoryRouter><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("Received")).toBeTruthy();
    expect(screen.getByText("Part received")).toBeTruthy();
    expect(screen.getByText(/PO-123 · slip PS-58216 · checked in/)).toBeTruthy();
    expect(screen.getByText("Credit request CR-58216")).toBeTruthy();
    fireEvent.click(screen.getByText("View check-in"));
    expect((await screen.findAllByText("Save count")).length).toBeGreaterThan(0);
  });

  it("says plainly when an order has no delivery check yet", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).includes("/check")
        ? Promise.resolve(new Response(JSON.stringify({ detail: "No delivery check for PO-124 yet." }), { status: 404 }))
        : String(input).includes("/orders?") ? reply([order]) : String(input).includes("/received?") ? reply([]) : reply(receipt),
    );
    render(<MemoryRouter initialEntries={["/receiving?po=PO-124"]}><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("No delivery check for PO-124 yet.")).toBeTruthy();
    expect(await screen.findByText("Willow Ridge Dairy Supply")).toBeTruthy(); // the expected deliveries stay
  });

  it("counts match / check / count it from the lines and badges each row", async () => {
    const base = { ...line, ordered_quantity: null, slip_shipped_quantity: null, seen_may_hide_more: false, display: {} };
    const lines = [
      // Muse couldn't count the croissants: an unsure zero shows as "?"
      { ...base, line_no: 1, description: "Butter Croissants, baked", supplier_item_number: "WR-3305", unit: "each", unit_size: "1",
        ordered_quantity: "10", slip_shipped_quantity: "10", seen_quantity: "0", seen_may_hide_more: true, status: "needs_count",
        message: "Please count the croissants." },
      { ...base, line_no: 2, description: "Coffee Beans, 12 oz bag", supplier_item_number: "WR-2210", unit: "g", unit_size: "340",
        ordered_quantity: "340", slip_shipped_quantity: "340", seen_quantity: "340", status: "match", message: "Matches." },
      { ...base, line_no: 3, description: "Whole Milk, half gallon (1.89 L)", supplier_item_number: "WR-1175", unit: "mL",
        unit_size: "1890", ordered_quantity: "7560", slip_shipped_quantity: "7560", seen_quantity: "5670", seen_may_hide_more: true,
        status: "needs_count", message: "Muse can see 3; the crate may hide more." },
    ];
    const check = { ...receipt, has_slip_image: true, lines, summary: { lines: 3, match: 1, needs_count: 2, problems: 0 } };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).includes("/received?") ? reply([]) : String(input).includes("/orders?") ? reply([order]) : reply(check),
    );
    render(<MemoryRouter initialEntries={["/receiving?receipt=RCV1"]}><ReceivingPage /></MemoryRouter>);

    const tallies = await screen.findByLabelText("Items by what they need");
    expect(within(screen.getByTestId("tally-match")).getByText("1")).toBeTruthy();
    expect(within(screen.getByTestId("tally-check")).getByText("1")).toBeTruthy();
    expect(within(screen.getByTestId("tally-count")).getByText("1")).toBeTruthy();
    expect(within(tallies).getByText("count it")).toBeTruthy();

    const row = (name: string) => screen.getByText(name).closest("article") as HTMLElement;
    expect(row("Butter Croissants, baked").className).toContain("rcv-row--bad");
    expect(within(row("Butter Croissants, baked")).getByText("Please count")).toBeTruthy();
    expect(within(row("Butter Croissants, baked")).getByText("?")).toBeTruthy();
    expect(row("Coffee Beans, 12 oz bag").className).toContain("rcv-row--good");
    expect(within(row("Coffee Beans, 12 oz bag")).getByText("Matches")).toBeTruthy();
    expect(row("Whole Milk, half gallon (1.89 L)").className).toContain("rcv-row--ask");
    expect(within(row("Whole Milk, half gallon (1.89 L)")).getByText("1 may be hidden")).toBeTruthy();
    expect(screen.getByLabelText("Whole Milk counted").textContent).toBe("3");
    expect(screen.getByText(/Butter Croissants not counted · Coffee Beans 1 · Whole Milk 3/)).toBeTruthy();
    expect((screen.getByText("Confirm what arrived") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the PO card and the order's items before the slip is read", async () => {
    const start = { ...receipt, slip_number: null, slip_supplier: null, slip_po_number: null, has_slip_image: false,
      has_photo_image: false, lines: [], summary: { lines: 0, match: 0, needs_count: 0, problems: 0 }, muse_message: "Show me the slip." };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).includes("/received?") ? reply([]) : String(input).includes("/orders?") ? reply([order]) : reply(start),
    );
    render(<MemoryRouter initialEntries={["/receiving?receipt=RCV1"]}><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("PO-1042 · Willow Ridge Dairy Supply")).toBeTruthy();
    expect(screen.getByText("1 · Slip").closest("li")?.getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("On the order")).toBeTruthy();
    expect(screen.getAllByText("Use demo photo").length).toBe(2);
    expect(screen.queryByLabelText("Items by what they need")).toBeNull(); // no counters before there are lines
  });
});

describe("lineView", () => {
  const base = { ...line, seen_may_hide_more: false };
  it("a short delivery needs a look, not a count", () => {
    const view = lineView({ ...base, status: "short", seen_quantity: "3000" });
    expect([view.need, view.tone, view.badge, view.seen]).toEqual(["check", "bad", "Short", 3]);
  });
  it("an unsure count with nothing hidden asks for a count", () => {
    const view = lineView({ ...base, status: "needs_count", seen_quantity: "11000" });
    expect([view.need, view.tone, view.badge]).toEqual(["count", "ask", "Please count"]);
  });
  it("before the delivery photo a line waits instead of asking for a count", () => {
    const view = lineView({ ...base, status: "needs_count", seen_quantity: null }, false);
    expect([view.tone, view.badge]).toEqual(["wait", "Waiting for photo"]);
  });
  it("no number from Muse shows as unknown", () => {
    expect(lineView({ ...base, status: "needs_count", seen_quantity: null }).seen).toBeNull();
    expect(tally([{ ...base, status: "match" }, { ...base, status: "over" }])).toEqual({ match: 1, check: 1, count: 0 });
  });
});
