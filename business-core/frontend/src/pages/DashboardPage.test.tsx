import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import DashboardPage, { HISTORY_END_AT, HISTORY_START_AT } from "./DashboardPage";

const responses = {
  sales: {
    cafe_id: "CAFE001",
    start_at: HISTORY_START_AT,
    end_at: HISTORY_END_AT,
    total_revenue_cents: 123456,
    sales_count: 126,
    total_units_sold: 210,
    average_sale_value_cents: 980,
    top_products: [
      { product_id: "LATTE", name: "Caffè Latte", quantity: 48, revenue_cents: 21600 },
      { product_id: "COLD_BREW", name: "Cold Brew", quantity: 31, revenue_cents: 13950 },
    ],
    hourly_sales: [
      { hour: 8, sales_count: 24, units_sold: 39, revenue_cents: 15250 },
      { hour: 14, sales_count: 8, units_sold: 11, revenue_cents: 4100 },
    ],
  },
  items: [
    { ingredient_id: "MILK", cafe_id: "CAFE001", name: "Whole Milk", category: "dairy", base_unit: "mL", reorder_point: null, par_level: null, is_perishable: true, active: true },
    { ingredient_id: "TEA", cafe_id: "CAFE001", name: "Tea Bags", category: "tea", base_unit: "each", reorder_point: null, par_level: null, is_perishable: false, active: true },
    { ingredient_id: "OAT", cafe_id: "CAFE001", name: "Oat Milk", category: "dairy_alternative", base_unit: "mL", reorder_point: null, par_level: null, is_perishable: true, active: true },
  ],
  stock: [
    {
      ingredient_id: "MILK",
      name: "Whole Milk",
      base_unit: "mL",
      stock_status: "known",
      estimated_quantity: "22400.000",
      latest_verified_count_id: "COUNT001",
      latest_verified_counted_at: "2025-01-10T13:30:00Z",
      movement_delta: "-100.000",
      included_movement_count: 1,
      anchor_is_synthetic: true,
      has_estimated_movements: true,
      calculated_at: "2025-01-13T08:00:00Z",
      reason: null,
    },
    {
      ingredient_id: "TEA",
      name: "Tea Bags",
      base_unit: "each",
      stock_status: "unknown",
      estimated_quantity: null,
      latest_verified_count_id: null,
      latest_verified_counted_at: null,
      movement_delta: null,
      included_movement_count: 0,
      anchor_is_synthetic: null,
      has_estimated_movements: false,
      calculated_at: "2025-01-13T08:00:00Z",
      reason: "No verified count",
    },
    {
      ingredient_id: "OAT",
      name: "Oat Milk",
      base_unit: "mL",
      stock_status: "inconsistent",
      estimated_quantity: "-50.000",
      latest_verified_count_id: "COUNT002",
      latest_verified_counted_at: "2025-01-10T13:30:00Z",
      movement_delta: "-550.000",
      included_movement_count: 2,
      anchor_is_synthetic: true,
      has_estimated_movements: false,
      calculated_at: "2025-01-13T08:00:00Z",
      reason: "Computed quantity is negative",
    },
  ],
  employees: [
    { employee_id: "EMP001", cafe_id: "CAFE001", name: "Maya Chen", role: "manager", active: true, created_at: "2025-01-01T08:00:00Z" },
    { employee_id: "EMP002", cafe_id: "CAFE001", name: "Jordan Brooks", role: "shift_lead", active: true, created_at: "2025-01-01T08:00:00Z" },
  ],
  coverage: {
    cafe_id: "CAFE001",
    start_at: HISTORY_START_AT,
    end_at: HISTORY_END_AT,
    required_staff: 2,
    hours: [
      { hour_start: "2025-01-06T16:00:00Z", scheduled_employees: 3, required_staff: 2, coverage_status: "adequate" },
      { hour_start: "2025-01-06T17:00:00Z", scheduled_employees: 1, required_staff: 2, coverage_status: "understaffed" },
      { hour_start: "2025-01-07T16:00:00Z", scheduled_employees: 2, required_staff: 2, coverage_status: "adequate" },
      { hour_start: "2025-01-07T17:00:00Z", scheduled_employees: 0, required_staff: 2, coverage_status: "understaffed" },
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

function successfulResponse(path: string) {
  if (path.includes("/sales/reports/summary")) return jsonResponse(responses.sales);
  if (path.includes("/inventory/items")) return jsonResponse(responses.items);
  if (path.includes("/inventory/stock")) return jsonResponse(responses.stock);
  if (path.includes("/staffing/employees")) return jsonResponse(responses.employees);
  if (path.includes("/staffing/reports/coverage")) return jsonResponse(responses.coverage);
  return jsonResponse({ detail: "Unknown request" }, false);
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

function mockSuccessfulApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => successfulResponse(String(input)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("DashboardPage", () => {
  it("renders API-derived KPIs with integer-cent money formatting", async () => {
    mockSuccessfulApi();
    renderDashboard();

    const revenue = await screen.findByText("Historical Revenue");
    expect(within(revenue.closest("article") as HTMLElement).getByText("$1,234.56")).not.toBeNull();
    expect(within(screen.getByText("Completed Sales").closest("article") as HTMLElement).getByText("126")).not.toBeNull();
    expect(within(screen.getByText("Units Sold").closest("article") as HTMLElement).getByText("210")).not.toBeNull();
    expect(within(screen.getByText("Average Sale Value").closest("article") as HTMLElement).getByText("$9.80")).not.toBeNull();
    expect(within(screen.getByText("Ingredient Catalog").closest("article") as HTMLElement).getByText("3")).not.toBeNull();
    expect(within(screen.getByText("Employees").closest("article") as HTMLElement).getByText("2")).not.toBeNull();
  });

  it("requests the fixed historical period with an exclusive January 13 boundary", async () => {
    const fetchMock = mockSuccessfulApi();
    renderDashboard();
    await screen.findByText("$1,234.56");

    const paths = fetchMock.mock.calls.map((call) => String(call[0]));
    const salesPath = paths.find((path) => path.includes("/sales/reports/summary"));
    const coveragePath = paths.find((path) => path.includes("/staffing/reports/coverage"));
    expect(salesPath).toBeDefined();
    expect(coveragePath).toBeDefined();
    const salesUrl = new URL(salesPath as string, "http://localhost");
    const coverageUrl = new URL(coveragePath as string, "http://localhost");
    expect(salesUrl.searchParams.get("start_at")).toBe(HISTORY_START_AT);
    expect(salesUrl.searchParams.get("end_at")).toBe(HISTORY_END_AT);
    expect(coverageUrl.searchParams.get("start_at")).toBe(HISTORY_START_AT);
    expect(coverageUrl.searchParams.get("end_at")).toBe(HISTORY_END_AT);
    expect(screen.getByText(/Historical operations period: January 6–12, 2025/)).not.toBeNull();
    expect(screen.getByText(/Inventory is a separate latest-known accounting estimate/)).not.toBeNull();
  });

  it("renders top products, hourly sales, and navigation to Sales", async () => {
    mockSuccessfulApi();
    renderDashboard();

    expect(await screen.findByText("Caffè Latte")).not.toBeNull();
    expect(screen.getByText("Cold Brew")).not.toBeNull();
    expect(screen.getByText("8 AM")).not.toBeNull();
    expect(screen.getByText("2 PM")).not.toBeNull();
    expect(screen.getByRole("link", { name: /Open Sales/ }).getAttribute("href")).toBe("/sales");
  });

  it("distinguishes unknown and inconsistent stock without inventing low-stock status", async () => {
    mockSuccessfulApi();
    renderDashboard();

    expect(await screen.findByText("Tea Bags")).not.toBeNull();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Inconsistent").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Estimated movements")).not.toBeNull();
    expect(screen.getAllByText("Synthetic count")).toHaveLength(2);
    expect(screen.getAllByText("Verified observation Jan 10, 2025")).toHaveLength(2);
    expect(screen.getByText(/No low-stock claim is made/)).not.toBeNull();
    expect(screen.queryByText(/low stock/i)).toBeNull();
    expect(screen.getByRole("link", { name: /Open Inventory/ }).getAttribute("href")).toBe("/inventory");
  });

  it("shows understaffed coverage and the fixed required staffing threshold", async () => {
    mockSuccessfulApi();
    renderDashboard();

    const coverage = await screen.findByLabelText("Staffing coverage totals");
    expect(within(coverage).getByText("Adequate Hours")).not.toBeNull();
    expect(within(coverage).getByText("Understaffed Hours")).not.toBeNull();
    expect(within(coverage).getByText("Required Staff")).not.toBeNull();
    expect(within(coverage).getAllByText("2")).toHaveLength(3);
    expect(screen.getByLabelText(/Mon, Jan 6: 1 adequate hours, 1 understaffed hours/)).not.toBeNull();
    expect(screen.getByText(/not demand optimization/)).not.toBeNull();
    expect(screen.getByRole("link", { name: /Open Staffing/ }).getAttribute("href")).toBe("/staffing");
  });

  it("keeps domains usable when independent APIs fail and retries affected resources", async () => {
    const user = userEvent.setup();
    let stockShouldFail = true;
    let employeesShouldFail = true;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("/inventory/stock") && stockShouldFail) {
        return jsonResponse({ detail: "Inventory temporarily unavailable" }, false);
      }
      if (path.includes("/staffing/employees") && employeesShouldFail) {
        return jsonResponse({ detail: "Employees temporarily unavailable" }, false);
      }
      return successfulResponse(path);
    }));
    renderDashboard();

    expect(await screen.findByText("Inventory estimates unavailable")).not.toBeNull();
    expect(screen.getByText("Caffè Latte")).not.toBeNull();
    expect(screen.getByLabelText("Staffing coverage totals")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Retry Employees" })).not.toBeNull();

    stockShouldFail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Tea Bags")).not.toBeNull();

    employeesShouldFail = false;
    await user.click(screen.getByRole("button", { name: "Retry Employees" }));
    await waitFor(() => {
      expect(within(screen.getByText("Employees").closest("article") as HTMLElement).getByText("2")).not.toBeNull();
    });
  });

  it("renders loading and empty states without fabricated values", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("/sales/reports/summary")) {
        return jsonResponse({ ...responses.sales, top_products: [], hourly_sales: [] });
      }
      if (path.includes("/inventory/items") || path.includes("/inventory/stock") || path.includes("/staffing/employees")) {
        return jsonResponse([]);
      }
      if (path.includes("/staffing/reports/coverage")) {
        return jsonResponse({ ...responses.coverage, hours: [] });
      }
      return jsonResponse({}, false);
    }));
    renderDashboard();

    expect(screen.getByText("Loading sales performance")).not.toBeNull();
    expect(screen.getByText("Loading inventory estimates")).not.toBeNull();
    expect(screen.getByText("Loading staffing coverage")).not.toBeNull();
    expect(await screen.findByText("No top-selling products found for CAFE001.")).not.toBeNull();
    expect(screen.getByText("No hourly sales found for CAFE001.")).not.toBeNull();
    expect(screen.getByText("No inventory estimates found for CAFE001.")).not.toBeNull();
    expect(screen.getByText("No coverage hours found for CAFE001.")).not.toBeNull();
  });

  it("retains dashboard content at a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    window.dispatchEvent(new Event("resize"));
    mockSuccessfulApi();
    renderDashboard();

    expect(await screen.findByRole("heading", { name: "Business summary" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Sales performance" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Inventory health" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Staffing coverage" })).not.toBeNull();
  });
});
