import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import StaffingPage from "./StaffingPage";

const employees = [
  {
    employee_id: "EMP001",
    cafe_id: "CAFE001",
    name: "Avery Chen",
    role: "Barista",
    active: true,
    created_at: "2025-01-01T16:00:00Z",
  },
  {
    employee_id: "EMP002",
    cafe_id: "CAFE001",
    name: "Morgan Lee",
    role: "Manager",
    active: false,
    created_at: "2025-01-01T16:00:00Z",
  },
];

const shifts = [
  {
    shift_id: "SHIFT001",
    cafe_id: "CAFE001",
    employee_id: "EMP001",
    employee_name: "Avery Chen",
    start_at: "2025-01-06T16:00:00Z",
    end_at: "2025-01-07T00:00:00Z",
    status: "scheduled",
    created_at: "2025-01-01T16:00:00Z",
  },
  {
    shift_id: "SHIFT002",
    cafe_id: "CAFE001",
    employee_id: "EMP002",
    employee_name: "Morgan Lee",
    start_at: "2025-01-07T17:00:00Z",
    end_at: "2025-01-08T01:00:00Z",
    status: "cancelled",
    created_at: "2025-01-01T16:00:00Z",
  },
];

const coverage = {
  cafe_id: "CAFE001",
  start_at: "2025-01-06T00:00:00-08:00",
  end_at: "2025-01-13T00:00:00-08:00",
  required_staff: 2,
  hours: [
    {
      hour_start: "2025-01-06T08:00:00-08:00",
      scheduled_employees: 3,
      required_staff: 2,
      coverage_status: "adequate",
    },
    {
      hour_start: "2025-01-06T09:00:00-08:00",
      scheduled_employees: 1,
      required_staff: 2,
      coverage_status: "understaffed",
    },
  ],
};

function jsonResponse(
  body: unknown,
  options: { ok?: boolean; status?: number; statusText?: string } = {},
) {
  return Promise.resolve({
    ok: options.ok ?? true,
    status: options.status ?? 200,
    statusText: options.statusText ?? (options.ok === false ? "Request failed" : "OK"),
    json: () => Promise.resolve(body),
  } as Response);
}

function successfulResponse(path: string, method = "GET") {
  if (method === "POST" && path.includes("/cancel")) {
    return jsonResponse({ ...shifts[0], status: "cancelled" });
  }
  if (method === "POST" && path.endsWith("/employees")) {
    return jsonResponse({
      employee_id: "EMP003",
      cafe_id: "CAFE001",
      name: "Riley Stone",
      role: "Shift Lead",
      active: true,
      created_at: "2025-01-02T16:00:00Z",
    });
  }
  if (method === "POST" && path.endsWith("/shifts")) {
    return jsonResponse({
      shift_id: "SHIFT003",
      cafe_id: "CAFE001",
      employee_id: "EMP001",
      employee_name: "Avery Chen",
      start_at: "2025-01-13T06:00:00Z",
      end_at: "2025-01-13T14:00:00Z",
      status: "scheduled",
      created_at: "2025-01-02T16:00:00Z",
    });
  }
  if (path.includes("/reports/coverage")) return jsonResponse(coverage);
  if (path.includes("/shifts")) return jsonResponse(shifts);
  return jsonResponse(employees);
}

function mockSuccessfulApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    successfulResponse(String(input), init?.method ?? "GET"),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("StaffingPage", () => {
  it("renders employees and loads the seeded inclusive historical date range", async () => {
    const fetchMock = mockSuccessfulApi();
    render(<StaffingPage />);

    expect(screen.getByText("Loading employees")).not.toBeNull();
    expect(await screen.findByText("Avery Chen")).not.toBeNull();
    expect(screen.getByText("Barista")).not.toBeNull();
    expect(screen.getByText("Inactive")).not.toBeNull();
    expect(screen.getByText(/January 6–12, 2025/)).not.toBeNull();

    const shiftRequest = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/staffing/shifts?"),
    );
    expect(shiftRequest).toBeDefined();
    const requestUrl = new URL(String(shiftRequest?.[0]), "http://localhost");
    expect(requestUrl.searchParams.get("start_at")).toBe("2025-01-06T08:00:00.000Z");
    expect(requestUrl.searchParams.get("end_at")).toBe("2025-01-13T08:00:00.000Z");
  });

  it("validates and creates an employee from the modal", async () => {
    const fetchMock = mockSuccessfulApi();
    const user = userEvent.setup();
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");

    await user.click(screen.getByRole("button", { name: "Add Employee" }));
    const dialog = screen.getByRole("dialog", { name: "Add Employee" });
    await user.click(within(dialog).getByRole("button", { name: "Add Employee" }));
    expect(within(dialog).getByText("Employee name is required.")).not.toBeNull();
    expect(within(dialog).getByText("Employee role is required.")).not.toBeNull();

    await user.type(within(dialog).getByLabelText("Employee Name"), "Riley Stone");
    await user.type(within(dialog).getByLabelText("Role"), "Shift Lead");
    await user.click(within(dialog).getByRole("button", { name: "Add Employee" }));

    expect(await screen.findByText(/Riley Stone was added/)).not.toBeNull();
    const employeePost = fetchMock.mock.calls.find(
      (call) => call[1]?.method === "POST" && String(call[0]).endsWith("/employees"),
    );
    expect(JSON.parse(String(employeePost?.[1]?.body))).toEqual({
      cafe_id: "CAFE001",
      name: "Riley Stone",
      role: "Shift Lead",
    });
  });

  it("lists shifts and schedules an overnight café-local shift", async () => {
    const fetchMock = mockSuccessfulApi();
    const user = userEvent.setup();
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("tab", { name: /Shifts/ }));

    expect((await screen.findAllByText("8 hrs")).length).toBe(2);
    expect(screen.getByText("Scheduled")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Schedule Shift" }));
    const dialog = screen.getByRole("dialog", { name: "Schedule Shift" });
    await user.selectOptions(within(dialog).getByLabelText("Active Employee"), "EMP001");
    fireEvent.change(within(dialog).getByLabelText("Start Date and Time"), {
      target: { value: "2025-01-12T22:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("End Date and Time"), {
      target: { value: "2025-01-13T06:00" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Schedule Shift" }));

    expect(await screen.findByText(/SHIFT003 was scheduled/)).not.toBeNull();
    const shiftPost = fetchMock.mock.calls.find(
      (call) => call[1]?.method === "POST" && String(call[0]).endsWith("/shifts"),
    );
    expect(JSON.parse(String(shiftPost?.[1]?.body))).toEqual({
      cafe_id: "CAFE001",
      employee_id: "EMP001",
      start_at: "2025-01-13T06:00:00.000Z",
      end_at: "2025-01-13T14:00:00.000Z",
    });
  });

  it.each([
    ["Shift overlaps an existing shift", "Shift overlaps an existing shift"],
    ["Employee is inactive", "Employee is inactive"],
  ])("shows the server conflict: %s", async (detail, expected) => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (init?.method === "POST" && path.endsWith("/shifts")) {
          return jsonResponse({ detail }, { ok: false, status: 409 });
        }
        return successfulResponse(path, init?.method ?? "GET");
      }),
    );
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("tab", { name: /Shifts/ }));
    await user.click(screen.getByRole("button", { name: "Schedule Shift" }));
    const dialog = screen.getByRole("dialog", { name: "Schedule Shift" });
    await user.selectOptions(within(dialog).getByLabelText("Active Employee"), "EMP001");
    fireEvent.change(within(dialog).getByLabelText("Start Date and Time"), {
      target: { value: "2025-01-08T09:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("End Date and Time"), {
      target: { value: "2025-01-08T17:00" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Schedule Shift" }));
    expect(await within(dialog).findByText(expected)).not.toBeNull();
  });

  it("requires explicit confirmation before cancelling a shift", async () => {
    const fetchMock = mockSuccessfulApi();
    const user = userEvent.setup();
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("tab", { name: /Shifts/ }));
    await user.click(await screen.findByRole("button", { name: "Cancel Shift" }));

    const confirmation = screen.getByRole("alertdialog", { name: "Cancel scheduled shift?" });
    expect(within(confirmation).getByText(/without deleting its historical record/)).not.toBeNull();
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/cancel"))).toBe(false);
    await user.click(within(confirmation).getByRole("button", { name: "Confirm Cancellation" }));

    expect(await screen.findByText(/SHIFT001 was cancelled/)).not.toBeNull();
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("SHIFT001/cancel"))).toBe(true);
  });

  it("renders coverage totals and refreshes for a required-staff change", async () => {
    const fetchMock = mockSuccessfulApi();
    const user = userEvent.setup();
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("tab", { name: /Coverage/ }));

    expect(await screen.findByText("Total Coverage Hours")).not.toBeNull();
    expect(screen.getByText("Adequately Staffed")).not.toBeNull();
    expect(screen.getAllByText("Understaffed").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("3 scheduled")).not.toBeNull();
    expect(screen.getAllByText("2 required")).toHaveLength(2);

    fireEvent.change(screen.getByLabelText("Required Staff"), { target: { value: "3" } });
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes("required_staff=3"),
        ),
      ).toBe(true);
    });
  });

  it("renders loading, API error, retry, and empty states", async () => {
    const user = userEvent.setup();
    let shouldFail = true;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (shouldFail) return Promise.reject(new TypeError("Failed to fetch"));
        const path = String(input);
        if (path.includes("/reports/coverage")) return jsonResponse({ ...coverage, hours: [] });
        return jsonResponse([]);
      }),
    );
    render(<StaffingPage />);
    expect(screen.getByText("Loading employees")).not.toBeNull();
    expect(await screen.findByText("Employee data is unavailable")).not.toBeNull();

    shouldFail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No employees found")).not.toBeNull();
    await user.click(screen.getByRole("tab", { name: /Shifts/ }));
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No shifts in this date range found")).not.toBeNull();
    await user.click(screen.getByRole("tab", { name: /Coverage/ }));
    expect(screen.getByText("No coverage hours found")).not.toBeNull();
  });

  it("warns users to inspect records after an uncertain employee write", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") return Promise.reject(new TypeError("Connection lost"));
        return successfulResponse(String(input));
      }),
    );
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("button", { name: "Add Employee" }));
    const dialog = screen.getByRole("dialog", { name: "Add Employee" });
    await user.type(within(dialog).getByLabelText("Employee Name"), "Casey Park");
    await user.type(within(dialog).getByLabelText("Role"), "Barista");
    await user.click(within(dialog).getByRole("button", { name: "Add Employee" }));
    expect(await within(dialog).findByText(/Check the Employees list before retrying/)).not.toBeNull();
  });

  it("rejects nonexistent DST times and requires a choice for ambiguous times", async () => {
    mockSuccessfulApi();
    const user = userEvent.setup();
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("tab", { name: /Shifts/ }));
    await user.click(screen.getByRole("button", { name: "Schedule Shift" }));
    const dialog = screen.getByRole("dialog", { name: "Schedule Shift" });
    await user.selectOptions(within(dialog).getByLabelText("Active Employee"), "EMP001");
    fireEvent.change(within(dialog).getByLabelText("Start Date and Time"), {
      target: { value: "2025-03-09T02:30" },
    });
    fireEvent.change(within(dialog).getByLabelText("End Date and Time"), {
      target: { value: "2025-03-09T04:00" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Schedule Shift" }));
    expect(within(dialog).getByText(/does not exist in America\/Los_Angeles/)).not.toBeNull();

    fireEvent.change(within(dialog).getByLabelText("Start Date and Time"), {
      target: { value: "2025-11-02T01:30" },
    });
    expect(within(dialog).getByLabelText("Start Time UTC Offset")).not.toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Schedule Shift" }));
    expect(within(dialog).getAllByText(/occurs twice/)).toHaveLength(2);
  });

  it("ignores stale shift responses after rapid date changes", async () => {
    const user = userEvent.setup();
    let resolveOlder: ((response: Response) => void) | undefined;
    let resolveNewer: ((response: Response) => void) | undefined;
    const older = new Promise<Response>((resolve) => { resolveOlder = resolve; });
    const newer = new Promise<Response>((resolve) => { resolveNewer = resolve; });

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = String(input);
        if (path.includes("/reports/coverage")) return jsonResponse(coverage);
        if (path.includes("/shifts?")) {
          const startAt = new URL(path, "http://localhost").searchParams.get("start_at");
          if (startAt === "2025-01-07T08:00:00.000Z") return older;
          if (startAt === "2025-01-08T08:00:00.000Z") return newer;
          return jsonResponse(shifts);
        }
        return jsonResponse(employees);
      }),
    );
    render(<StaffingPage />);
    await screen.findByText("Avery Chen");
    await user.click(screen.getByRole("tab", { name: /Shifts/ }));
    const startInput = screen.getByLabelText("Start Date");
    fireEvent.change(startInput, { target: { value: "2025-01-07" } });
    fireEvent.change(startInput, { target: { value: "2025-01-08" } });

    await act(async () => {
      resolveNewer?.(await jsonResponse([{ ...shifts[0], employee_name: "Newest Range" }]));
    });
    expect(await screen.findByText("Newest Range")).not.toBeNull();
    await act(async () => {
      resolveOlder?.(await jsonResponse([{ ...shifts[0], employee_name: "Stale Range" }]));
    });
    await waitFor(() => {
      expect(screen.queryByText("Stale Range")).toBeNull();
      expect(screen.getByText("Newest Range")).not.toBeNull();
    });
  });
});
