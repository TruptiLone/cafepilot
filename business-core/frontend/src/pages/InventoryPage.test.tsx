import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    {
      ingredient_id: "WHOLE_MILK",
      cafe_id: "CAFE001",
      name: "Whole Milk",
      category: "dairy",
      base_unit: "mL",
      reorder_point: null,
      par_level: null,
      is_perishable: true,
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
  draftDetail: {
    count_id: "DRAFT_COUNT_1",
    cafe_id: "CAFE001",
    status: "draft",
    counted_at: null,
    recorded_at: "2025-01-08T17:05:00Z",
    counted_by: "EMP_UI_TEST",
    is_synthetic: true,
    lines: [
      {
        count_line_id: "DRAFT_LINE_1",
        ingredient_id: "OAT_MILK",
        counted_quantity: "0.000",
      },
      {
        count_line_id: "DRAFT_LINE_2",
        ingredient_id: "WHOLE_MILK",
        counted_quantity: "12.345",
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

async function openCountForm(user: ReturnType<typeof userEvent.setup>) {
  expect(await screen.findByText("OAT_MILK")).not.toBeNull();
  await user.click(screen.getByRole("tab", { name: /Stock Counts/ }));
  await user.click(screen.getByRole("button", { name: /New Count/ }));
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

  it("reports a saved movement separately when the table refresh fails", async () => {
    const user = userEvent.setup();
    let movementReads = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST") return jsonResponse(responses.movements[0]);
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse(responses.counts);
      if (path.includes("/movements")) {
        movementReads += 1;
        return movementReads === 1
          ? jsonResponse(responses.movements)
          : Promise.reject(new TypeError("Refresh failed"));
      }
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await openMovementForm(user);
    await enterValidReceipt(user);
    await user.click(screen.getByRole("button", { name: "Save Entry" }));

    expect(await screen.findByText(/Movement was saved, but the tables could not be refreshed/)).not.toBeNull();
    expect(screen.queryByRole("dialog", { name: "New Entry" })).toBeNull();
    expect(screen.queryByText("Entry was not confirmed")).toBeNull();
  });

  it("creates a synthetic multi-ingredient draft count and accepts zero quantity", async () => {
    const user = userEvent.setup();
    let countCreated = false;
    const draftSummary = {
      count_id: responses.draftDetail.count_id,
      cafe_id: responses.draftDetail.cafe_id,
      status: responses.draftDetail.status,
      counted_at: responses.draftDetail.counted_at,
      recorded_at: responses.draftDetail.recorded_at,
      counted_by: responses.draftDetail.counted_by,
      is_synthetic: responses.draftDetail.is_synthetic,
      item_count: responses.draftDetail.lines.length,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST" && path.endsWith("/counts")) {
        countCreated = true;
        return jsonResponse(responses.draftDetail);
      }
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) {
        return jsonResponse(countCreated ? [draftSummary, ...responses.counts] : responses.counts);
      }
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await openCountForm(user);
    await user.type(screen.getByLabelText(/Counted By/), "EMP_UI_TEST");
    await user.selectOptions(screen.getByLabelText("Ingredient 1"), "OAT_MILK");
    await user.type(screen.getByLabelText("Counted Quantity 1"), "0");
    await user.click(screen.getByRole("button", { name: /Add Ingredient/ }));

    const secondIngredient = screen.getByLabelText("Ingredient 2");
    expect(
      (within(secondIngredient).getByRole("option", { name: "Oat Milk" }) as HTMLOptionElement)
        .disabled,
    ).toBe(true);
    await user.selectOptions(secondIngredient, "WHOLE_MILK");
    await user.type(screen.getByLabelText("Counted Quantity 2"), "12.345");
    await user.click(screen.getByRole("button", { name: "Save Draft" }));

    expect(await screen.findByText(/DRAFT_COUNT_1 saved and count history refreshed/)).not.toBeNull();
    const postCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === "POST" && String(call[0]).endsWith("/counts"),
    );
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      cafe_id: "CAFE001",
      status: "draft",
      counted_at: null,
      counted_by: "EMP_UI_TEST",
      is_synthetic: true,
      items: [
        { ingredient_id: "OAT_MILK", counted_quantity: "0" },
        { ingredient_id: "WHOLE_MILK", counted_quantity: "12.345" },
      ],
    });
  });

  it("validates draft count lines before sending them", async () => {
    const user = userEvent.setup();
    const fetchMock = mockSuccessfulApi();
    render(<InventoryPage />);

    await openCountForm(user);
    await user.selectOptions(screen.getByLabelText("Ingredient 1"), "OAT_MILK");
    await user.type(screen.getByLabelText("Counted Quantity 1"), "-1.000");
    await user.click(screen.getByRole("button", { name: "Save Draft" }));

    expect(screen.getByText(/nonnegative number with no more than three decimal places/)).not.toBeNull();
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Remove ingredient 1" }));
    await user.click(screen.getByRole("button", { name: "Save Draft" }));
    expect(screen.getByText("Add at least one ingredient to the count.")).not.toBeNull();
  });

  it("warns users to check count history before retrying an uncertain save", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST" && path.endsWith("/counts")) {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse(responses.counts);
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await openCountForm(user);
    await user.selectOptions(screen.getByLabelText("Ingredient 1"), "OAT_MILK");
    await user.type(screen.getByLabelText("Counted Quantity 1"), "1.000");
    await user.click(screen.getByRole("button", { name: "Save Draft" }));

    expect(await screen.findByText("Failed to fetch")).not.toBeNull();
    expect(screen.getByText(/check Stock Counts history before retrying/)).not.toBeNull();
    expect(screen.getByRole("dialog", { name: "New Count" })).not.toBeNull();
  });

  it("verifies a draft with an explicit cafe-local observation time", async () => {
    const user = userEvent.setup();
    let verified = false;
    const draftSummary = {
      ...responses.counts[0],
      count_id: "DRAFT_COUNT_1",
      status: "draft",
      counted_at: null,
      counted_by: "EMP_UI_TEST",
      item_count: 2,
    };
    const verifiedDetail = {
      ...responses.draftDetail,
      status: "verified",
      counted_at: "2025-01-08T17:00:00.000Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST" && path.includes("/verify?")) {
        verified = true;
        return jsonResponse(verifiedDetail);
      }
      if (path.includes("/counts/DRAFT_COUNT_1")) return jsonResponse(responses.draftDetail);
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) {
        return jsonResponse([
          verified ? { ...draftSummary, status: "verified", counted_at: verifiedDetail.counted_at } : draftSummary,
          ...responses.counts,
        ]);
      }
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    expect(await screen.findByText("OAT_MILK")).not.toBeNull();
    await user.click(screen.getByRole("tab", { name: /Stock Counts/ }));
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/Verification establishes a new inventory baseline/)).not.toBeNull();

    fireEvent.change(screen.getByLabelText(/Actual Observation Date and Time/), {
      target: { value: "2099-01-08T09:00" },
    });
    await user.click(screen.getByLabelText(/I reviewed these physical quantities/));
    await user.click(screen.getByRole("button", { name: "Verify Count" }));
    expect(screen.getByText("Observation date and time cannot be in the future.")).not.toBeNull();

    fireEvent.change(screen.getByLabelText(/Actual Observation Date and Time/), {
      target: { value: "2025-01-08T09:00" },
    });
    await user.click(screen.getByRole("button", { name: "Verify Count" }));

    expect(await screen.findByText(/Count DRAFT_COUNT_1 verified/)).not.toBeNull();
    const postCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === "POST" && String(call[0]).includes("/verify?"),
    );
    expect(String(postCall?.[0])).toContain("cafe_id=CAFE001");
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      counted_at: "2025-01-08T17:00:00.000Z",
    });
  });

  it("keeps the verification form open when the backend reports a conflict", async () => {
    const user = userEvent.setup();
    const draftSummary = {
      ...responses.counts[0],
      count_id: "DRAFT_COUNT_1",
      status: "draft",
      counted_at: null,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (options?.method === "POST" && path.includes("/verify?")) {
        return jsonResponse({ detail: "Stock count is already verified" }, false);
      }
      if (path.includes("/counts/DRAFT_COUNT_1")) return jsonResponse(responses.draftDetail);
      if (path.includes("/items")) return jsonResponse(responses.items);
      if (path.includes("/counts")) return jsonResponse([draftSummary]);
      if (path.includes("/movements")) return jsonResponse(responses.movements);
      return jsonResponse(responses.stock);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    expect(await screen.findByText("OAT_MILK")).not.toBeNull();
    await user.click(screen.getByRole("tab", { name: /Stock Counts/ }));
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByLabelText(/Actual Observation Date and Time/);
    fireEvent.change(screen.getByLabelText(/Actual Observation Date and Time/), {
      target: { value: "2025-01-08T09:00" },
    });
    await user.click(screen.getByLabelText(/I reviewed these physical quantities/));
    await user.click(screen.getByRole("button", { name: "Verify Count" }));

    expect(await screen.findByText("Stock count is already verified")).not.toBeNull();
    expect(screen.getByRole("dialog", { name: "Verify Count" })).not.toBeNull();
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
