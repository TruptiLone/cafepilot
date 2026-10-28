export const ACTIVE_CAFE_ID = "CAFE001";

export type Employee = {
  employee_id: string;
  cafe_id: string;
  name: string;
  role: string;
  active: boolean;
  created_at: string;
};

export type Shift = {
  shift_id: string;
  cafe_id: string;
  employee_id: string;
  employee_name: string;
  start_at: string;
  end_at: string;
  status: "scheduled" | "cancelled";
  created_at: string;
};

export type CoverageHour = {
  hour_start: string;
  scheduled_employees: number;
  required_staff: number;
  coverage_status: "adequate" | "understaffed";
};

export type CoverageReport = {
  cafe_id: string;
  start_at: string;
  end_at: string;
  required_staff: number;
  hours: CoverageHour[];
};

export type EmployeeCreate = {
  cafe_id: string;
  name: string;
  role: string;
};

export type ShiftCreate = {
  cafe_id: string;
  employee_id: string;
  start_at: string;
  end_at: string;
};

export class StaffingApiError extends Error {
  status?: number;
  uncertain: boolean;

  constructor(message: string, options: { status?: number; uncertain?: boolean } = {}) {
    super(message);
    this.name = "StaffingApiError";
    this.status = options.status;
    this.uncertain = options.uncertain ?? false;
  }
}

async function requestJson<T>(
  path: string,
  options: RequestInit = {},
  writeRequest = false,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        Accept: "application/json",
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new StaffingApiError("The Staffing request did not receive a response.", {
      uncertain: writeRequest,
    });
  }

  if (!response.ok) {
    let detail = response.statusText || "The request could not be completed.";
    try {
      const body = (await response.json()) as { detail?: string | unknown[] };
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {}
    throw new StaffingApiError(detail, { status: response.status });
  }
  return response.json() as Promise<T>;
}

function query(params: Record<string, string | number>) {
  return new URLSearchParams(
    Object.entries(params).map(([key, value]) => [key, String(value)]),
  ).toString();
}

export function getEmployees(signal?: AbortSignal): Promise<Employee[]> {
  return requestJson<Employee[]>(
    `/api/v1/staffing/employees?${query({ cafe_id: ACTIVE_CAFE_ID })}`,
    { signal },
  );
}

export function createEmployee(payload: EmployeeCreate): Promise<Employee> {
  return requestJson<Employee>(
    "/api/v1/staffing/employees",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    true,
  );
}

export function getShifts(
  startAt: string,
  endAt: string,
  signal?: AbortSignal,
): Promise<Shift[]> {
  return requestJson<Shift[]>(
    `/api/v1/staffing/shifts?${query({
      cafe_id: ACTIVE_CAFE_ID,
      start_at: startAt,
      end_at: endAt,
    })}`,
    { signal },
  );
}

export function createShift(payload: ShiftCreate): Promise<Shift> {
  return requestJson<Shift>(
    "/api/v1/staffing/shifts",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    true,
  );
}

export function cancelShift(shiftId: string): Promise<Shift> {
  return requestJson<Shift>(
    `/api/v1/staffing/shifts/${encodeURIComponent(shiftId)}/cancel?${query({
      cafe_id: ACTIVE_CAFE_ID,
    })}`,
    { method: "POST" },
    true,
  );
}

export function getCoverage(
  startAt: string,
  endAt: string,
  requiredStaff: number,
  signal?: AbortSignal,
): Promise<CoverageReport> {
  return requestJson<CoverageReport>(
    `/api/v1/staffing/reports/coverage?${query({
      cafe_id: ACTIVE_CAFE_ID,
      start_at: startAt,
      end_at: endAt,
      required_staff: requiredStaff,
    })}`,
    { signal },
  );
}
