import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceivingPage from "./ReceivingPage";

const order = {
  order_id: "PO1", po_number: "PO-1042", supplier_name: "Willow Ridge Dairy Supply", status: "confirmed",
  requested_delivery_at: "2026-11-01T13:30:00Z", confirmed_delivery_at: "2026-11-01T13:30:00Z", simulated: true,
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
      String(input).endsWith("/orders?cafe_id=CAFE001") ? reply([order]) : reply(receipt),
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
});
