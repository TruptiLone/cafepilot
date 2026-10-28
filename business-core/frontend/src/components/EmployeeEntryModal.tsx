import { useEffect, useState } from "react";
import {
  ACTIVE_CAFE_ID,
  StaffingApiError,
  createEmployee,
  type Employee,
} from "../api/staffing";

type EmployeeEntryModalProps = {
  onCancel: () => void;
  onSaved: (employee: Employee) => void;
};

function EmployeeEntryModal({ onCancel, onSaved }: EmployeeEntryModalProps) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [errors, setErrors] = useState<{ name?: string; role?: string }>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isUncertain, setIsUncertain] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) onCancel();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [isSubmitting, onCancel]);

  const validate = () => {
    const nextErrors: { name?: string; role?: string } = {};
    if (!name.trim()) nextErrors.name = "Employee name is required.";
    if (!role.trim()) nextErrors.role = "Employee role is required.";
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !validate()) return;

    setIsSubmitting(true);
    setSubmitError(null);
    setIsUncertain(false);
    try {
      onSaved(
        await createEmployee({
          cafe_id: ACTIVE_CAFE_ID,
          name: name.trim(),
          role: role.trim(),
        }),
      );
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "The employee could not be created.",
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
        aria-labelledby="employee-entry-title"
      >
        <div className="entry-modal__header">
          <div>
            <p className="page__eyebrow">Staffing</p>
            <h2 id="employee-entry-title">Add Employee</h2>
          </div>
          <button
            className="modal-close"
            type="button"
            aria-label="Close employee form"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            ×
          </button>
        </div>

        <div className="entry-warning">
          Saving creates an active employee in the local CAFE001 demo database.
        </div>

        <form className="entry-form" onSubmit={handleSubmit} noValidate>
          <label className="form-field">
            <span>Employee Name</span>
            <input
              autoFocus
              type="text"
              aria-label="Employee Name"
              value={name}
              aria-invalid={Boolean(errors.name)}
              onChange={(event) => setName(event.target.value)}
            />
            {errors.name && <small className="field-error">{errors.name}</small>}
          </label>

          <label className="form-field">
            <span>Role</span>
            <input
              type="text"
              aria-label="Role"
              value={role}
              placeholder="e.g. Barista"
              aria-invalid={Boolean(errors.role)}
              onChange={(event) => setRole(event.target.value)}
            />
            {errors.role && <small className="field-error">{errors.role}</small>}
          </label>

          {submitError && (
            <div className="form-submit-error" role="alert">
              <strong>Employee was not confirmed</strong>
              <span>{submitError}</span>
              {isUncertain && (
                <small>
                  Check the Employees list before retrying because this write does not
                  have a guaranteed idempotent retry contract.
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
              {isSubmitting ? "Saving…" : "Add Employee"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default EmployeeEntryModal;
