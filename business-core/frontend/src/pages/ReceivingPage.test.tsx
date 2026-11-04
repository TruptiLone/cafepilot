import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceivingPage from "./ReceivingPage";

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

afterEach(() => vi.restoreAllMocks());

describe("ReceivingPage", () => {
  it("lists the expected delivery and asks the owner to count what Muse can't see", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).endsWith("/orders?cafe_id=CAFE001") ? reply([order]) : String(input).includes("/received?") ? reply([]) : reply(receipt),
    );
    render(<MemoryRouter><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("Willow Ridge Dairy Supply")).toBeTruthy();
    fireEvent.click(screen.getByText("Check this delivery"));

    expect(await screen.findByText("Cartons here")).toBeTruthy();
    expect(screen.getAllByText(/Muse can see 3 cartons/).length).toBeGreaterThan(0);
    expect((screen.getByText("Approve and add to stock") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByLabelText("One more"));
    fireEvent.click(screen.getByText("Save count"));
    const [, options] = fetchMock.mock.calls.at(-1)!;
    expect(JSON.parse(String((options as RequestInit).body))).toMatchObject({ counted_units: 4 });
  });

  it("opens an order's delivery check from a fixed link", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).endsWith("/orders?cafe_id=CAFE001") ? reply([order]) : String(input).includes("/received?") ? reply([]) : reply(receipt),
    );
    render(<MemoryRouter initialEntries={["/receiving?po=PO-1042"]}><ReceivingPage /></MemoryRouter>);

    expect(await screen.findByText("Cartons here")).toBeTruthy();
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
    expect((await screen.findAllByText("Cartons here")).length).toBeGreaterThan(0);
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
});
