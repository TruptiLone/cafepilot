import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SalesPage from "./SalesPage";

const responses = {
  products: [
    {
      product_id: "LATTE",
      cafe_id: "CAFE001",
      name: "Caffè Latte",
      category: "espresso_drinks",
      price_cents: 450,
      active: true,
    },
    {
      product_id: "BLUEBERRY_MUFFIN",
      cafe_id: "CAFE001",
      name: "Blueberry Muffin",
      category: "bakery",
      price_cents: 375,
      active: true,
    },
  ],
  sales: [
    {
      sale_id: "DEMO_SALE_002",
      cafe_id: "CAFE001",
      source_id: "DEMO_SOURCE_002",
      sold_at: "2025-01-12T18:30:00Z",
      recorded_at: "2025-01-12T18:31:00Z",
      status: "completed",
      total_cents: 900,
      item_count: 1,
      total_units: 2,
    },
    {
      sale_id: "DEMO_SALE_001",
      cafe_id: "CAFE001",
      source_id: "DEMO_SOURCE_001",
      sold_at: "2025-01-06T16:15:00Z",
      recorded_at: "2025-01-06T16:16:00Z",
      status: "completed",
      total_cents: 375,
      item_count: 1,
      total_units: 1,
    },
  ],
  report: {
    cafe_id: "CAFE001",
    start_at: "2025-01-06T08:00:00-08:00",
    end_at: "2025-01-12T18:00:00-08:00",
    total_revenue_cents: 123456,
    sales_count: 126,
    total_units_sold: 210,
    average_sale_value_cents: 980,
    top_products: [
      {
        product_id: "LATTE",
        name: "Caffè Latte",
        quantity: 48,
        revenue_cents: 21600,
      },
    ],
    hourly_sales: [
      {
        hour: 8,
        sales_count: 24,
        units_sold: 39,
        revenue_cents: 15250,
      },
      {
        hour: 14,
        sales_count: 8,
        units_sold: 11,
        revenue_cents: 4100,
      },
    ],
  },
  details: {
    DEMO_SALE_002: {
      sale_id: "DEMO_SALE_002",
      cafe_id: "CAFE001",
      source_id: "DEMO_SOURCE_002",
      sold_at: "2025-01-12T18:30:00Z",
      recorded_at: "2025-01-12T18:31:00Z",
      status: "completed",
      total_cents: 900,
      items: [
        {
          product_id: "LATTE",
          product_name: "Caffè Latte",
          quantity: 2,
          unit_price_cents: 450,
          line_total_cents: 900,
        },
      ],
    },
    DEMO_SALE_001: {
      sale_id: "DEMO_SALE_001",
      cafe_id: "CAFE001",
      source_id: "DEMO_SOURCE_001",
      sold_at: "2025-01-06T16:15:00Z",
      recorded_at: "2025-01-06T16:16:00Z",
      status: "completed",
      total_cents: 375,
      items: [
        {
          product_id: "BLUEBERRY_MUFFIN",
          product_name: "Blueberry Muffin",
          quantity: 1,
          unit_price_cents: 375,
          line_total_cents: 375,
        },
      ],
    },
  },
};

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    statusText: ok ? "OK" : "Service Unavailable",
    json: () => Promise.resolve(body),
  } as Response);
}

function successfulResponse(path: string) {
  if (path.includes("/reports/summary")) return jsonResponse(responses.report);
  if (path.includes("/products")) return jsonResponse(responses.products);
  if (path.includes("/sales/DEMO_SALE_002")) {
    return jsonResponse(responses.details.DEMO_SALE_002);
  }
  if (path.includes("/sales/DEMO_SALE_001")) {
    return jsonResponse(responses.details.DEMO_SALE_001);
  }
  return jsonResponse(responses.sales);
}

function mockSuccessfulApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL) =>
    successfulResponse(String(input)),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("SalesPage", () => {
  it("renders backend overview values as historical café-local sales data", async () => {
    const fetchMock = mockSuccessfulApi();
    render(<SalesPage />);

    expect(screen.getByText("Loading sales data")).not.toBeNull();
    expect(await screen.findByText("$1,234.56")).not.toBeNull();
    expect(screen.getByText("126")).not.toBeNull();
    expect(screen.getByText("210")).not.toBeNull();
    expect(screen.getByText("$9.80")).not.toBeNull();
    expect(screen.getByText(/January 6–12, 2025/)).not.toBeNull();
    expect(screen.getByText("8:00 AM")).not.toBeNull();
    expect(screen.getByText("2:00 PM")).not.toBeNull();
    expect(screen.getByText("$216.00")).not.toBeNull();
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("start_at"))).toBe(false);
  });

  it("shows sales history and loads a selected sale with historical prices", async () => {
    mockSuccessfulApi();
    const user = userEvent.setup();
    render(<SalesPage />);

    await screen.findByText("$1,234.56");
    await user.click(screen.getByRole("tab", { name: /Sales History/ }));

    const saleRow = screen.getByText("DEMO_SALE_002").closest("tr");
    expect(saleRow).not.toBeNull();
    expect(within(saleRow as HTMLElement).getByText("2")).not.toBeNull();
    expect(within(saleRow as HTMLElement).getByText("$9.00")).not.toBeNull();
    expect(screen.getByText(/Jan 12, 2025/)).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "View sale DEMO_SALE_002" }));
    const detail = await screen.findByRole("heading", { name: "DEMO_SALE_002" });
    const detailPanel = detail.closest("section");
    expect(detailPanel).not.toBeNull();
    expect(within(detailPanel as HTMLElement).getByText("Caffè Latte")).not.toBeNull();
    expect(within(detailPanel as HTMLElement).getByText("$4.50")).not.toBeNull();
    expect(within(detailPanel as HTMLElement).getByText("Transaction total $9.00")).not.toBeNull();
  });

  it("renders the live menu product catalog", async () => {
    mockSuccessfulApi();
    const user = userEvent.setup();
    render(<SalesPage />);

    await screen.findByText("$1,234.56");
    await user.click(screen.getByRole("tab", { name: /Menu Products/ }));

    expect(screen.getByText("Caffè Latte")).not.toBeNull();
    expect(screen.getByText("Espresso Drinks")).not.toBeNull();
    expect(screen.getByText("$4.50")).not.toBeNull();
    expect(screen.getAllByText("Active")).toHaveLength(2);
  });

  it("shows an API error and retries all read-only datasets", async () => {
    const user = userEvent.setup();
    let shouldFail = true;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (shouldFail) return Promise.reject(new TypeError("Failed to fetch"));
      return successfulResponse(String(input));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SalesPage />);

    expect(await screen.findByText("Sales data is unavailable")).not.toBeNull();
    expect(screen.getByText(/backend is running on port 8001/)).not.toBeNull();

    shouldFail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("$1,234.56")).not.toBeNull();
  });

  it("shows predictable empty states for all sales views", async () => {
    const user = userEvent.setup();
    const emptyReport = {
      ...responses.report,
      total_revenue_cents: 0,
      sales_count: 0,
      total_units_sold: 0,
      average_sale_value_cents: 0,
      top_products: [],
      hourly_sales: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = String(input);
        if (path.includes("/reports/summary")) return jsonResponse(emptyReport);
        return jsonResponse([]);
      }),
    );
    render(<SalesPage />);

    expect(await screen.findByText("No product sales found for this history.")).not.toBeNull();
    expect(screen.getByText("No hourly sales found for this history.")).not.toBeNull();

    await user.click(screen.getByRole("tab", { name: "Sales History 0" }));
    expect(screen.getByText("No completed sales found")).not.toBeNull();

    await user.click(screen.getByRole("tab", { name: "Menu Products 0" }));
    expect(screen.getByText("No menu products found")).not.toBeNull();
  });

  it("does not display stale sale details after rapid selection changes", async () => {
    const user = userEvent.setup();
    let resolveFirstDetail: ((value: Response) => void) | undefined;
    let resolveSecondDetail: ((value: Response) => void) | undefined;
    const firstDetail = new Promise<Response>((resolve) => {
      resolveFirstDetail = resolve;
    });
    const secondDetail = new Promise<Response>((resolve) => {
      resolveSecondDetail = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = String(input);
        if (path.includes("/sales/DEMO_SALE_002")) return firstDetail;
        if (path.includes("/sales/DEMO_SALE_001")) return secondDetail;
        return successfulResponse(path);
      }),
    );
    render(<SalesPage />);

    await screen.findByText("$1,234.56");
    await user.click(screen.getByRole("tab", { name: /Sales History/ }));
    await user.click(screen.getByRole("button", { name: "View sale DEMO_SALE_002" }));
    await user.click(screen.getByRole("button", { name: "View sale DEMO_SALE_001" }));

    await act(async () => {
      resolveSecondDetail?.({
        ok: true,
        statusText: "OK",
        json: () => Promise.resolve(responses.details.DEMO_SALE_001),
      } as Response);
    });
    expect(await screen.findByText("Blueberry Muffin")).not.toBeNull();

    await act(async () => {
      resolveFirstDetail?.({
        ok: true,
        statusText: "OK",
        json: () => Promise.resolve(responses.details.DEMO_SALE_002),
      } as Response);
    });
    await waitFor(() => {
      expect(screen.queryByText("Caffè Latte")).toBeNull();
      expect(screen.getByText("Blueberry Muffin")).not.toBeNull();
    });
  });
});
