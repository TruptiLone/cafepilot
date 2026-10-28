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
  const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input);
    if (options?.method === "POST") return jsonResponse(responses.movements[0]);
    if (path.includes("/counts/DEMO_COUNT_1")) return jsonResponse(responses.detail);
    if (path.includes("/items")) return jsonResponse(responses.items);
    if (path.includes("/counts")) return jsonResponse(responses.counts);
    if (path.includes("/movements")) return jsonResponse(responses.movements);
    return jsonResponse(responses.stock);
  });
  vi.stubGlobal(
    "fetch",
    fetchMock,
  );
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function openMovementForm(user: ReturnType<typeof userEvent.setup>) {
  expect(await screen.findByText("OAT_MILK")).not.toBeNull();
  await user.click(screen.getByRole("tab", { name: /Stock Movements/ }));
  await user.click(screen.getByRole("button", { name: /New Entry/ }));
  await user.selectOptions(screen.getByLabelText("Ingredient"), "OAT_MILK");
}

async function enterValidReceipt(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Quantity/), "1000.125");
  await user.type(screen.getByLabelText("Reason"), "Weekly delivery");
  fireEvent.change(screen.getByLabelText(/Event Date and Time/), {
    target: { value: "2025-01-08T09:00" },
  });
  await user.type(screen.getByLabelText(/Recorded By/), "EMP_UI_TEST");
}

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

  it("creates a movement with an explicit timezone and refreshes movement data", async () => {
    const user = userEvent.setup();
    const newMovement = {
      ...responses.movements[0],
      movement_id: "MANUAL_MOVEMENT_1",
      quantity_delta: "1000.125",
      source_type: "manual_entry",
      source_id: "MANUAL_TEST_SOURCE",
      reason: "Weekly delivery",
    };
    let movementCreated = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST") {
        movementCreated = true;
        return jsonResponse(newMovement);
      }
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse(responses.counts);
      if (path.includes("/movements")) {
        return jsonResponse(movementCreated ? [newMovement, ...responses.movements] : responses.movements);
      }
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await openMovementForm(user);
    await enterValidReceipt(user);
    await user.click(screen.getByRole("button", { name: "Save Entry" }));

    expect(await screen.findByText(/Movement saved/)).not.toBeNull();
    expect(screen.getByText("Weekly delivery")).not.toBeNull();
    const postCall = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    const request = JSON.parse(String(postCall?.[1]?.body));
    expect(request).toMatchObject({
      ingredient_id: "OAT_MILK",
      movement_type: "RECEIPT",
      quantity_delta: "1000.125",
      source_type: "manual_entry",
      occurred_at: "2025-01-08T17:00:00.000Z",
      recorded_by: "EMP_UI_TEST",
      reason: "Weekly delivery",
    });
    expect(request.source_id).toMatch(/^MANUAL_[A-F0-9]{32}$/);
  });

  it("rejects invalid movement signs and missing required reasons", async () => {
    const user = userEvent.setup();
    const fetchMock = mockSuccessfulApi();
    render(<InventoryPage />);

    await openMovementForm(user);
    await user.selectOptions(screen.getByLabelText("Movement Type"), "WASTE");
    await user.type(screen.getByLabelText(/Quantity/), "10.1234");
    fireEvent.change(screen.getByLabelText(/Event Date and Time/), {
      target: { value: "2025-01-08T09:00" },
    });
    await user.click(screen.getByRole("button", { name: "Save Entry" }));

    expect(screen.getByText(/no more than three decimal places/)).not.toBeNull();
    expect(screen.getByText(/Reason is required/)).not.toBeNull();

    const quantityInput = screen.getByLabelText(/Quantity/);
    await user.clear(quantityInput);
    await user.type(quantityInput, "10.123");
    await user.type(screen.getByLabelText(/Reason/), "Spill waste");
    await user.click(screen.getByRole("button", { name: "Save Entry" }));
    expect(screen.getByText("Waste quantity must be negative.")).not.toBeNull();
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(0);
  });

  it("retains the same source identity when a failed request is retried", async () => {
    const user = userEvent.setup();
    const submittedSourceIds: string[] = [];
    let postAttempt = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST") {
        postAttempt += 1;
        const request = JSON.parse(String(options.body));
        submittedSourceIds.push(request.source_id);
        if (postAttempt === 1) return jsonResponse({ detail: "Temporary outage" }, false);
        return jsonResponse(responses.movements[0]);
      }
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse(responses.counts);
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await openMovementForm(user);
    await enterValidReceipt(user);
    await user.click(screen.getByRole("button", { name: "Save Entry" }));
    expect(await screen.findByText("Temporary outage")).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Save Entry" }));
    expect(await screen.findByText(/Movement saved/)).not.toBeNull();
    expect(submittedSourceIds).toHaveLength(2);
    expect(submittedSourceIds[0]).toBe(submittedSourceIds[1]);
  });

  it("disables submission while a movement request is pending", async () => {
    const user = userEvent.setup();
    let resolvePost: ((response: Response) => void) | undefined;
    const pendingPost = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST") return pendingPost;
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse(responses.counts);
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await openMovementForm(user);
    await enterValidReceipt(user);
    await user.click(screen.getByRole("button", { name: "Save Entry" }));
    const savingButton = screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement;
    expect(savingButton.disabled).toBe(true);
    fireEvent.click(savingButton);
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);

    await act(async () => {
      resolvePost?.(await jsonResponse(responses.movements[0]));
    });
    expect(await screen.findByText(/Movement saved/)).not.toBeNull();
  });

  it("ignores stale count details after a rapid count selection change", async () => {
    const user = userEvent.setup();
    const secondCount = {
      ...responses.counts[0],
      count_id: "DEMO_COUNT_2",
      counted_at: "2025-01-07T13:30:00Z",
    };
    let resolveFirst: ((response: Response) => void) | undefined;
    let resolveSecond: ((response: Response) => void) | undefined;
    const firstDetail = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const secondDetail = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("/counts/DEMO_COUNT_1")) return firstDetail;
      if (path.includes("/counts/DEMO_COUNT_2")) return secondDetail;
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse([responses.counts[0], secondCount]);
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    expect(await screen.findByText("OAT_MILK")).not.toBeNull();
    await user.click(screen.getByRole("tab", { name: /Stock Counts/ }));
    await user.click(screen.getAllByRole("button", { name: "View details" })[0]);
    await user.click(screen.getByRole("button", { name: "View details" }));

    await act(async () => {
      resolveSecond?.(
        await jsonResponse({
          ...responses.detail,
          count_id: "DEMO_COUNT_2",
          lines: [{ ...responses.detail.lines[0], counted_quantity: "4000.000" }],
        }),
      );
    });
    expect(await screen.findByText("4,000")).not.toBeNull();

    await act(async () => {
      resolveFirst?.(await jsonResponse(responses.detail));
    });
    await waitFor(() => expect(screen.queryByText("3,000")).toBeNull());
  });
});
