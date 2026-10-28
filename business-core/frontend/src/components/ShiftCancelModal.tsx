import { useState } from "react";
import {
  StaffingApiError,
  cancelShift,
  type Shift,
} from "../api/staffing";

type ShiftCancelModalProps = {
  shift: Shift;
  onCancel: () => void;
  onCancelled: (shift: Shift) => void;
};

function ShiftCancelModal({ shift, onCancel, onCancelled }: ShiftCancelModalProps) {
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isUncertain, setIsUncertain] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleCancelShift = async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    setSubmitError(null);
    setIsUncertain(false);
    try {
      onCancelled(await cancelShift(shift.shift_id));
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "The shift could not be cancelled.",
      );
      setIsUncertain(error instanceof StaffingApiError && error.uncertain);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop modal-backdrop--centered">
      <section
        className="confirm-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="cancel-shift-title"
        aria-describedby="cancel-shift-description"
      >
        <p className="page__eyebrow">Shift History</p>
        <h2 id="cancel-shift-title">Cancel scheduled shift?</h2>
        <p id="cancel-shift-description">
          Cancel {shift.employee_name}&apos;s shift without deleting its historical record.
          Cancelled shifts no longer contribute to staffing coverage.
        </p>

        {submitError && (
          <div className="form-submit-error" role="alert">
            <strong>Cancellation was not confirmed</strong>
            <span>{submitError}</span>
            {isUncertain && (
              <small>Refresh Shifts before retrying to confirm the current status.</small>
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
            Keep Shift
          </button>
          <button
            className="danger-button"
            type="button"
            disabled={isSubmitting}
            onClick={() => void handleCancelShift()}
          >
            {isSubmitting ? "Cancelling…" : "Confirm Cancellation"}
          </button>
        </div>
      </section>
    </div>
  );
}

export default ShiftCancelModal;
