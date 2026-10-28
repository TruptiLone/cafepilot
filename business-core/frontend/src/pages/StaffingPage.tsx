import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getCoverage,
  getEmployees,
  getShifts,
  type CoverageReport,
  type Employee,
  type Shift,
} from "../api/staffing";
import EmployeeEntryModal from "../components/EmployeeEntryModal";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import ShiftCancelModal from "../components/ShiftCancelModal";
import ShiftEntryModal from "../components/ShiftEntryModal";
import StatusBadge from "../components/StatusBadge";
import { CAFE_TIME_ZONE, resolveCafeTimestamp } from "../utils/cafeTime";

type StaffingTab = "employees" | "shifts" | "coverage";
type PageNotice = { kind: "success" | "warning"; message: string };

const DEFAULT_START_DATE = "2025-01-06";
const DEFAULT_END_DATE = "2025-01-12";

const tabs: { id: StaffingTab; label: string }[] = [
  { id: "employees", label: "Employees" },
  { id: "shifts", label: "Shifts" },
  { id: "coverage", label: "Coverage" },
];

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
});

const coverageHourFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  timeZoneName: "short",
});

function addCalendarDays(value: string, days: number) {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function apiDateRange(startDate: string, endDate: string) {
  const exclusiveEndDate = addCalendarDays(endDate, 1);
  if (!exclusiveEndDate || startDate > endDate) return null;
  const start = resolveCafeTimestamp(`${startDate}T00:00`);
  const end = resolveCafeTimestamp(`${exclusiveEndDate}T00:00`);
  if (start.status !== "valid" || end.status !== "valid") return null;
  return { startAt: start.timestamp, endAt: end.timestamp };
}

function formatDuration(startAt: string, endAt: string) {
  const hours = (new Date(endAt).getTime() - new Date(startAt).getTime()) / 3_600_000;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} hr${hours === 1 ? "" : "s"}`;
}

function StaffingPage() {
  const [activeTab, setActiveTab] = useState<StaffingTab>("employees");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [startDate, setStartDate] = useState(DEFAULT_START_DATE);
  const [endDate, setEndDate] = useState(DEFAULT_END_DATE);
  const [requiredStaff, setRequiredStaff] = useState(2);
  const [isEmployeesLoading, setIsEmployeesLoading] = useState(true);
  const [isScheduleLoading, setIsScheduleLoading] = useState(true);
  const [employeesError, setEmployeesError] = useState<string | null>(null);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [isEmployeeEntryOpen, setIsEmployeeEntryOpen] = useState(false);
  const [isShiftEntryOpen, setIsShiftEntryOpen] = useState(false);
  const [shiftToCancel, setShiftToCancel] = useState<Shift | null>(null);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const scheduleRequestId = useRef(0);

  const loadEmployees = useCallback(async (signal?: AbortSignal) => {
    setIsEmployeesLoading(true);
    setEmployeesError(null);
    try {
      setEmployees(await getEmployees(signal));
      return true;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return false;
      setEmployeesError(
        error instanceof Error
          ? `${error.message} Confirm the Business Core backend is running on port 8001.`
          : "Confirm the Business Core backend is running on port 8001.",
      );
      return false;
    } finally {
      if (!signal?.aborted) setIsEmployeesLoading(false);
    }
  }, []);

  const loadSchedule = useCallback(
    async (signal?: AbortSignal) => {
      const requestId = ++scheduleRequestId.current;
      const range = apiDateRange(startDate, endDate);
      if (!range) {
        setScheduleError("Select a valid inclusive date range with the end on or after the start.");
        setIsScheduleLoading(false);
        return false;
      }

      setIsScheduleLoading(true);
      setScheduleError(null);
      try {
        const [nextShifts, nextCoverage] = await Promise.all([
          getShifts(range.startAt, range.endAt, signal),
          getCoverage(range.startAt, range.endAt, requiredStaff, signal),
        ]);
        if (requestId !== scheduleRequestId.current || signal?.aborted) return false;
        setShifts(nextShifts);
        setCoverage(nextCoverage);
        return true;
      } catch (error) {
        if (
          requestId !== scheduleRequestId.current ||
          (error instanceof DOMException && error.name === "AbortError")
        ) {
          return false;
        }
        setScheduleError(
          error instanceof Error
            ? `${error.message} Confirm the Business Core backend is running on port 8001.`
            : "Staffing schedule data could not be loaded.",
        );
        return false;
      } finally {
        if (requestId === scheduleRequestId.current && !signal?.aborted) {
          setIsScheduleLoading(false);
        }
      }
    },
    [endDate, requiredStaff, startDate],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadEmployees(controller.signal);
    return () => controller.abort();
  }, [loadEmployees]);

  useEffect(() => {
    const controller = new AbortController();
    void loadSchedule(controller.signal);
    return () => controller.abort();
  }, [loadSchedule]);

  const activeEmployees = useMemo(
    () => employees.filter((employee) => employee.active),
    [employees],
  );

  const handleEmployeeSaved = (employee: Employee) => {
    setIsEmployeeEntryOpen(false);
    setEmployees((current) =>
      [...current, employee].sort((left, right) =>
        left.name.localeCompare(right.name) || left.employee_id.localeCompare(right.employee_id),
      ),
    );
    setNotice({ kind: "success", message: `${employee.name} was added as an active employee.` });
  };

  const refreshAfterShiftWrite = async (successMessage: string) => {
    setNotice({ kind: "success", message: `${successMessage} Refreshing schedule and coverage…` });
    const refreshed = await loadSchedule();
    setNotice(
      refreshed
        ? { kind: "success", message: `${successMessage} Schedule and coverage were refreshed.` }
        : {
            kind: "warning",
            message: `${successMessage} The write succeeded, but schedule data could not be refreshed. Reload before retrying any action.`,
          },
    );
  };

  const handleShiftSaved = (shift: Shift) => {
    setIsShiftEntryOpen(false);
    void refreshAfterShiftWrite(`Shift ${shift.shift_id} was scheduled.`);
  };

  const handleShiftCancelled = (shift: Shift) => {
    setShiftToCancel(null);
    void refreshAfterShiftWrite(`Shift ${shift.shift_id} was cancelled without deleting history.`);
  };

  const tabCount = (tab: StaffingTab) => {
    if (tab === "employees") return employees.length;
    if (tab === "shifts") return shifts.length;
    return coverage?.hours.length ?? 0;
  };

  return (
    <section className="page page--staffing" aria-labelledby="page-title">
      <div className="page__heading staffing-heading">
        <div>
          <p className="page__eyebrow">Business Management</p>
          <h1 id="page-title">Staffing</h1>
          <p className="page__description">
            Manage employees, scheduled shifts, and hourly staffing coverage for CAFE001.
          </p>
        </div>
        <div className="demo-label">
          <span aria-hidden="true" />
          Local Demo Database
        </div>
      </div>

      <div className="historical-notice">
        Seeded staffing history is synthetic and dated January 6–12, 2025. Times use
        {` ${CAFE_TIME_ZONE}`}. Coverage compares scheduled employees with a selected
        threshold; it is not demand forecasting or proof of employee availability.
      </div>

      {notice && (
        <div
          className={`success-message success-message--${notice.kind}`}
          role={notice.kind === "warning" ? "alert" : "status"}
        >
          <span>{notice.message}</span>
          <button type="button" aria-label="Dismiss message" onClick={() => setNotice(null)}>
            ×
          </button>
        </div>
      )}

      <div className="inventory-panel staffing-panel">
        <div className="inventory-tabs" role="tablist" aria-label="Staffing views">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`inventory-tab${activeTab === tab.id ? " is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`staffing-panel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
              <span className="tab-count">{tabCount(tab.id)}</span>
            </button>
          ))}
        </div>

        <div id={`staffing-panel-${activeTab}`} role="tabpanel">
          {activeTab === "employees" && (
            <EmployeesView
              employees={employees}
              isLoading={isEmployeesLoading}
              error={employeesError}
              onRetry={() => void loadEmployees()}
              onAdd={() => {
                setNotice(null);
                setIsEmployeeEntryOpen(true);
              }}
            />
          )}

          {activeTab !== "employees" && (
            <>
              <DateRangeControls
                startDate={startDate}
                endDate={endDate}
                requiredStaff={requiredStaff}
                showRequiredStaff={activeTab === "coverage"}
                onStartDate={setStartDate}
                onEndDate={setEndDate}
                onRequiredStaff={setRequiredStaff}
              />
              {isScheduleLoading && <LoadingState label="staffing schedule" />}
              {!isScheduleLoading && scheduleError && (
                <ErrorState
                  heading="Staffing schedule is unavailable"
                  message={scheduleError}
                  onRetry={() => void loadSchedule()}
                />
              )}
              {!isScheduleLoading && !scheduleError && activeTab === "shifts" && (
                <ShiftsView
                  shifts={shifts}
                  activeEmployeeCount={activeEmployees.length}
                  onAdd={() => {
                    setNotice(null);
                    setIsShiftEntryOpen(true);
                  }}
                  onCancel={setShiftToCancel}
                />
              )}
              {!isScheduleLoading && !scheduleError && activeTab === "coverage" && (
                <CoverageView coverage={coverage} />
              )}
            </>
          )}
        </div>
      </div>

      {isEmployeeEntryOpen && (
        <EmployeeEntryModal
          onCancel={() => setIsEmployeeEntryOpen(false)}
          onSaved={handleEmployeeSaved}
        />
      )}
      {isShiftEntryOpen && (
        <ShiftEntryModal
          employees={activeEmployees}
          onCancel={() => setIsShiftEntryOpen(false)}
          onSaved={handleShiftSaved}
        />
      )}
      {shiftToCancel && (
        <ShiftCancelModal
          shift={shiftToCancel}
          onCancel={() => setShiftToCancel(null)}
          onCancelled={handleShiftCancelled}
        />
      )}
    </section>
  );
}

function EmployeesView({
  employees,
  isLoading,
  error,
  onRetry,
  onAdd,
}: {
  employees: Employee[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  onAdd: () => void;
}) {
  return (
    <>
      <div className="inventory-toolbar">
        <div>
          <strong>Employees</strong>
          <span>Current café employee records and active status</span>
        </div>
        <button className="primary-button" type="button" onClick={onAdd}>
          <span aria-hidden="true">+</span> Add Employee
        </button>
      </div>
      {isLoading && <LoadingState label="employees" />}
      {!isLoading && error && (
        <ErrorState heading="Employee data is unavailable" message={error} onRetry={onRetry} />
      )}
      {!isLoading && !error && !employees.length && <EmptyState label="employees" />}
      {!isLoading && !error && employees.length > 0 && (
        <div className="table-scroll">
          <table className="data-table staffing-table">
            <thead>
              <tr>
                <th>Employee</th>
                <th>Role</th>
                <th>Status</th>
                <th>Employee ID</th>
              </tr>
            </thead>
            <tbody>
              {employees.map((employee) => (
                <tr key={employee.employee_id}>
                  <td className="primary-cell">{employee.name}</td>
                  <td>{employee.role}</td>
                  <td><StatusBadge status={employee.active ? "active" : "inactive"} /></td>
                  <td className="identifier-cell">{employee.employee_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function DateRangeControls({
  startDate,
  endDate,
  requiredStaff,
  showRequiredStaff,
  onStartDate,
  onEndDate,
  onRequiredStaff,
}: {
  startDate: string;
  endDate: string;
  requiredStaff: number;
  showRequiredStaff: boolean;
  onStartDate: (value: string) => void;
  onEndDate: (value: string) => void;
  onRequiredStaff: (value: number) => void;
}) {
  return (
    <div className="staffing-filters" aria-label="Staffing date range">
      <label>
        <span>Start Date</span>
        <input
          aria-label="Start Date"
          type="date"
          value={startDate}
          onChange={(event) => onStartDate(event.target.value)}
        />
      </label>
      <label>
        <span>End Date <em>Inclusive</em></span>
        <input
          aria-label="End Date"
          type="date"
          value={endDate}
          onChange={(event) => onEndDate(event.target.value)}
        />
      </label>
      {showRequiredStaff && (
        <label>
          <span>Required Staff</span>
          <input
            type="number"
            aria-label="Required Staff"
            min="1"
            step="1"
            value={requiredStaff}
            onChange={(event) => onRequiredStaff(Math.max(1, Number(event.target.value) || 1))}
          />
        </label>
      )}
      <p>Historical demo range · café-local dates</p>
    </div>
  );
}

function ShiftsView({
  shifts,
  activeEmployeeCount,
  onAdd,
  onCancel,
}: {
  shifts: Shift[];
  activeEmployeeCount: number;
  onAdd: () => void;
  onCancel: (shift: Shift) => void;
}) {
  return (
    <>
      <div className="inventory-toolbar">
        <div>
          <strong>Scheduled Shifts</strong>
          <span>{activeEmployeeCount} active employees available for scheduling</span>
        </div>
        <button className="primary-button" type="button" disabled={!activeEmployeeCount} onClick={onAdd}>
          <span aria-hidden="true">+</span> Schedule Shift
        </button>
      </div>
      {!shifts.length ? (
        <EmptyState label="shifts in this date range" />
      ) : (
        <div className="table-scroll staffing-shift-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>Employee</th>
                <th>Schedule Date</th>
                <th>Start</th>
                <th>End</th>
                <th>Duration</th>
                <th>Status</th>
                <th><span className="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {shifts.map((shift) => (
                <tr key={shift.shift_id}>
                  <td>
                    <div className="stacked-cell">
                      <strong>{shift.employee_name}</strong>
                      <small>{shift.employee_id}</small>
                    </div>
                  </td>
                  <td>{dateFormatter.format(new Date(shift.start_at))}</td>
                  <td>{dateTimeFormatter.format(new Date(shift.start_at))}</td>
                  <td>{dateTimeFormatter.format(new Date(shift.end_at))}</td>
                  <td>{formatDuration(shift.start_at, shift.end_at)}</td>
                  <td><StatusBadge status={shift.status} /></td>
                  <td className="action-cell">
                    {shift.status === "scheduled" && (
                      <button className="text-button" type="button" onClick={() => onCancel(shift)}>
                        Cancel Shift
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function CoverageView({ coverage }: { coverage: CoverageReport | null }) {
  if (!coverage || !coverage.hours.length) return <EmptyState label="coverage hours" />;
  const adequateHours = coverage.hours.filter((hour) => hour.coverage_status === "adequate").length;
  const understaffedHours = coverage.hours.length - adequateHours;
  return (
    <div className="staffing-coverage">
      <div className="staffing-metrics" aria-label="Coverage summary">
        <CoverageMetric label="Total Coverage Hours" value={coverage.hours.length} />
        <CoverageMetric label="Adequately Staffed" value={adequateHours} />
        <CoverageMetric label="Understaffed" value={understaffedHours} tone={understaffedHours ? "warning" : undefined} />
      </div>

      <section className="coverage-card" aria-labelledby="coverage-hours-title">
        <div className="sales-card__heading">
          <p className="page__eyebrow">Scheduled Employee Count</p>
          <h2 id="coverage-hours-title">Hour-by-hour coverage</h2>
        </div>
        <div className="coverage-list">
          {coverage.hours.map((hour) => {
            const fill = Math.min(100, (hour.scheduled_employees / hour.required_staff) * 100);
            return (
              <div
                className="coverage-row"
                key={hour.hour_start}
                aria-label={`${coverageHourFormatter.format(new Date(hour.hour_start))}: ${hour.scheduled_employees} scheduled, ${hour.required_staff} required, ${hour.coverage_status}`}
              >
                <strong>{coverageHourFormatter.format(new Date(hour.hour_start))}</strong>
                <div className="coverage-row__track" aria-hidden="true">
                  <span style={{ width: `${fill}%` }} />
                </div>
                <span>{hour.scheduled_employees} scheduled</span>
                <span>{hour.required_staff} required</span>
                <StatusBadge status={hour.coverage_status} />
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function CoverageMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning";
}) {
  return (
    <div className={`staffing-metric${tone ? ` staffing-metric--${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default StaffingPage;
