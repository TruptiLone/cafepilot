import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import InventoryPage from "./InventoryPage";

const responses = {
  items: [
    {
      ingredient_id: "OAT_MILK",
      cafe_id: "CAFE001",
      name: "Oat Milk",
      category: "alternative_milk",
      base_unit: "mL",
      reorder_point: null,
      par_level: null,
      is_perishable: false,
      active: true,
    },
  ],
  counts: [
    {
      count_id: "DEMO_COUNT_1",
      cafe_id: "CAFE001",
      status: "verified",
      counted_at: "2025-01-06T13:30:00Z",
      recorded_at: "2025-01-06T13:40:00Z",
      counted_by: "DEMO_EMP_001",
      is_synthetic: true,
      item_count: 1,
    },
  ],
  movements: [
    {
      movement_id: "DEMO_MOVEMENT_1",
      cafe_id: "CAFE001",
      ingredient_id: "OAT_MILK",
      base_unit: "mL",
      movement_type: "RECEIPT",
      quantity_delta: "2000.000",
      is_estimated: false,
      source_type: "delivery",
      source_id: "DEMO_SOURCE_1",
      occurred_at: "2025-01-07T16:00:00Z",
      recorded_at: "2025-01-07T16:05:00Z",
      recorded_by: "DEMO_EMP_001",
      reason: null,
    },
  ],
  stock: [
    {
      ingredient_id: "OAT_MILK",
      name: "Oat Milk",
      base_unit: "mL",
      stock_status: "known",
      estimated_quantity: "5000.000",
      latest_verified_count_id: "DEMO_COUNT_1",
      latest_verified_counted_at: "2025-01-06T13:30:00Z",
      movement_delta: "2000.000",
      included_movement_count: 1,
      anchor_is_synthetic: true,
      has_estimated_movements: false,
      calculated_at: "2025-01-13T12:00:00Z",
      reason: null,
    },
  ],
  detail: {
    count_id: "DEMO_COUNT_1",
    cafe_id: "CAFE001",
    status: "verified",
    counted_at: "2025-01-06T13:30:00Z",
    recorded_at: "2025-01-06T13:40:00Z",
    counted_by: "DEMO_EMP_001",
    is_synthetic: true,
    lines: [
      {
        count_line_id: "DEMO_LINE_1",
        ingredient_id: "OAT_MILK",
        counted_quantity: "3000.000",
      },
    ],
  },
};

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    statusText: ok ? "OK" : "Service Unavailable",
    json: () => Promise.resolve(body),
  } as Response);
}

function mockSuccessfulApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("/counts/DEMO_COUNT_1")) return jsonResponse(responses.detail);
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse(responses.counts);
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("InventoryPage", () => {
  it("loads each dataset, switches tabs, and opens count details", async () => {
    mockSuccessfulApi();
    const user = userEvent.setup();
    render(<InventoryPage />);

    expect(screen.getByText("Loading inventory data")).not.toBeNull();
    expect(await screen.findByText("OAT_MILK")).not.toBeNull();

    await user.click(screen.getByRole("tab", { name: /Stock Counts/ }));
    expect(screen.getByText("DEMO_COUNT_1")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "View details" }));
    await waitFor(() => expect(screen.getByText("3,000")).not.toBeNull());

    await user.click(screen.getByRole("tab", { name: /Stock Movements/ }));
    expect(screen.getByText("+2,000")).not.toBeNull();

    await user.click(screen.getByRole("tab", { name: /Current Stock/ }));
    expect(screen.getByText("5,000")).not.toBeNull();
  });

  it("shows an empty state for an empty dataset", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse([])));
    render(<InventoryPage />);
    expect(await screen.findByText("No ingredients found")).not.toBeNull();
  });

  it("shows a useful backend error and supports retry", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    expect(await screen.findByText("Inventory data is unavailable")).not.toBeNull();
    expect(screen.getByText(/backend is running on port 8001/)).not.toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));
  });
});
