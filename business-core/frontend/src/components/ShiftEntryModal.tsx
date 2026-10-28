import { useEffect, useMemo, useState } from "react";
import {
  ACTIVE_CAFE_ID,
  StaffingApiError,
  createShift,
  type Employee,
  type Shift,
} from "../api/staffing";
import {
  CAFE_TIME_ZONE,
  resolveCafeTimestamp,
  type CafeTimestampResolution,
} from "../utils/cafeTime";

type ShiftEntryModalProps = {
  employees: Employee[];
  onCancel: () => void;
  onSaved: (shift: Shift) => void;
};

type ShiftFormErrors = Partial<
  Record<"employee" | "startAt" | "endAt" | "range", string>
>;

function selectedTimestamp(
  resolution: CafeTimestampResolution,
  ambiguityChoice: string,
) {
  if (resolution.status === "valid") return resolution.timestamp;
  if (resolution.status === "ambiguous") return ambiguityChoice || null;
  return null;
}

function timestampError(
  label: string,
  value: string,
  resolution: CafeTimestampResolution,
  ambiguityChoice: string,
) {
  if (!value) return `${label} is required.`;
  if (resolution.status === "invalid") return `Enter a valid ${label.toLowerCase()}.`;
  if (resolution.status === "nonexistent") {
    return `${label} does not exist in ${CAFE_TIME_ZONE} because of daylight saving time.`;
  }
  if (resolution.status === "ambiguous" && !ambiguityChoice) {
    return `${label} occurs twice. Choose the intended UTC offset.`;
  }
  return null;
}

function AmbiguousTimeChoice({
  label,
  resolution,
  value,
  onChange,
}: {
  label: string;
  resolution: CafeTimestampResolution;
  value: string;
  onChange: (value: string) => void;
}) {
  if (resolution.status !== "ambiguous") return null;
  return (
    <label className="form-field form-field--nested">
      <span>{label} UTC Offset</span>
      <select
        aria-label={`${label} UTC Offset`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose first or second occurrence</option>
        {resolution.options.map((option) => (
          <option key={option.timestamp} value={option.timestamp}>
            {option.label}
          </option>
        ))}
      </select>
      <small className="field-help">
        This café-local time occurs twice when daylight saving time ends.
      </small>
    </label>
  );
}

function ShiftEntryModal({ employees, onCancel, onSaved }: ShiftEntryModalProps) {
  const [employeeId, setEmployeeId] = useState("");
  const [startAt, setStartAt] = useState("");
  const [endAt, setEndAt] = useState("");
  const [startAmbiguityChoice, setStartAmbiguityChoice] = useState("");
  const [endAmbiguityChoice, setEndAmbiguityChoice] = useState("");
  const [errors, setErrors] = useState<ShiftFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isUncertain, setIsUncertain] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const startResolution = useMemo(() => resolveCafeTimestamp(startAt), [startAt]);
  const endResolution = useMemo(() => resolveCafeTimestamp(endAt), [endAt]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) onCancel();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [isSubmitting, onCancel]);

  const validate = () => {
    const nextErrors: ShiftFormErrors = {};
    if (!employeeId) nextErrors.employee = "Select an active employee.";
    const startError = timestampError(
      "Start time",
      startAt,
      startResolution,
      startAmbiguityChoice,
    );
    const endError = timestampError(
      "End time",
      endAt,
      endResolution,
      endAmbiguityChoice,
    );
    if (startError) nextErrors.startAt = startError;
    if (endError) nextErrors.endAt = endError;

    const startTimestamp = selectedTimestamp(startResolution, startAmbiguityChoice);
    const endTimestamp = selectedTimestamp(endResolution, endAmbiguityChoice);
    if (
      !startError &&
      !endError &&
      startTimestamp &&
      endTimestamp &&
      new Date(startTimestamp).getTime() >= new Date(endTimestamp).getTime()
    ) {
      nextErrors.range = "Shift end must be after shift start. Overnight shifts are supported.";
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !validate()) return;
    const startTimestamp = selectedTimestamp(startResolution, startAmbiguityChoice);
    const endTimestamp = selectedTimestamp(endResolution, endAmbiguityChoice);
    if (!startTimestamp || !endTimestamp) return;

    setIsSubmitting(true);
    setSubmitError(null);
    setIsUncertain(false);
    try {
      onSaved(
        await createShift({
          cafe_id: ACTIVE_CAFE_ID,
          employee_id: employeeId,
          start_at: startTimestamp,
          end_at: endTimestamp,
        }),
      );
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "The shift could not be scheduled.",
      );
      setIsUncertain(error instanceof StaffingApiError && error.uncertain);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop">
      <section
        className="entry-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shift-entry-title"
      >
        <div className="entry-modal__header">
          <div>
            <p className="page__eyebrow">Staffing</p>
            <h2 id="shift-entry-title">Schedule Shift</h2>
          </div>
          <button
            className="modal-close"
            type="button"
            aria-label="Close shift form"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            ×
          </button>
        </div>

        <div className="entry-warning">
          Times are entered in {CAFE_TIME_ZONE}. Saving writes a scheduled shift to
          the local demo database.
        </div>

        <form className="entry-form" onSubmit={handleSubmit} noValidate>
          <label className="form-field">
            <span>Active Employee</span>
            <select
              autoFocus
              aria-label="Active Employee"
              value={employeeId}
              aria-invalid={Boolean(errors.employee)}
              onChange={(event) => setEmployeeId(event.target.value)}
            >
              <option value="">Select an employee</option>
              {employees.map((employee) => (
                <option key={employee.employee_id} value={employee.employee_id}>
                  {employee.name} · {employee.role}
                </option>
              ))}
            </select>
            {errors.employee && <small className="field-error">{errors.employee}</small>}
          </label>

          <label className="form-field">
            <span>Start Date and Time</span>
            <input
              type="datetime-local"
              aria-label="Start Date and Time"
              value={startAt}
              aria-invalid={Boolean(errors.startAt)}
              onChange={(event) => {
                setStartAt(event.target.value);
                setStartAmbiguityChoice("");
              }}
            />
            <small className={errors.startAt ? "field-error" : "field-help"}>
              {errors.startAt ?? CAFE_TIME_ZONE}
            </small>
          </label>
          <AmbiguousTimeChoice
            label="Start Time"
            resolution={startResolution}
            value={startAmbiguityChoice}
            onChange={setStartAmbiguityChoice}
          />

          <label className="form-field">
            <span>End Date and Time</span>
            <input
              type="datetime-local"
              aria-label="End Date and Time"
              value={endAt}
              aria-invalid={Boolean(errors.endAt)}
              onChange={(event) => {
                setEndAt(event.target.value);
                setEndAmbiguityChoice("");
              }}
            />
            <small className={errors.endAt ? "field-error" : "field-help"}>
              {errors.endAt ?? "End may be on the following day for an overnight shift."}
            </small>
          </label>
          <AmbiguousTimeChoice
            label="End Time"
            resolution={endResolution}
            value={endAmbiguityChoice}
            onChange={setEndAmbiguityChoice}
          />

          {errors.range && <div className="inline-form-error">{errors.range}</div>}
          {submitError && (
            <div className="form-submit-error" role="alert">
              <strong>Shift was not confirmed</strong>
              <span>{submitError}</span>
              {isUncertain && (
                <small>
                  Check the Shifts list before retrying because shift creation does
                  not have a guaranteed idempotent retry contract.
                </small>
              )}
            </div>
          )}

          <div className="entry-form__actions">
            <button
              className="secondary-button"
              type="button"
              disabled={isSubmitting}
              onClick={onCancel}
            >
              Cancel
            </button>
            <button className="primary-button" type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Scheduling…" : "Schedule Shift"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default ShiftEntryModal;
