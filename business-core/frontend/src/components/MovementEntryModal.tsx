import { useEffect, useMemo, useState } from "react";
import {
  ACTIVE_CAFE_ID,
  createInventoryMovement,
  type InventoryItem,
  type InventoryMovement,
  type InventoryMovementCreate,
} from "../api/inventory";

type MovementType = InventoryMovementCreate["movement_type"];

type MovementEntryModalProps = {
  items: InventoryItem[];
  onCancel: () => void;
  onSaved: (movement: InventoryMovement) => Promise<void>;
};

type FormErrors = Partial<
  Record<"ingredient" | "quantity" | "reason" | "occurredAt", string>
>;

const CAFE_TIME_ZONE = "America/Los_Angeles";

const timeZoneFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function createSourceId() {
  return `MANUAL_${crypto.randomUUID().replaceAll("-", "").toUpperCase()}`;
}

function getTimeZoneParts(date: Date) {
  return Object.fromEntries(
    timeZoneFormatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  ) as Record<string, number>;
}

function getTimeZoneOffset(date: Date) {
  const parts = getTimeZoneParts(date);
  const representedAsUtc = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  );
  return representedAsUtc - date.getTime();
}

export function toCafeTimestamp(localValue: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(localValue);
  if (!match) return null;

  const [, year, month, day, hour, minute] = match.map(Number);
  const wallTime = Date.UTC(year, month - 1, day, hour, minute);
  let instant = wallTime;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    instant = wallTime - getTimeZoneOffset(new Date(instant));
  }

  const candidate = new Date(instant);
  const candidateParts = getTimeZoneParts(candidate);
  const isExactMatch =
    candidateParts.year === year &&
    candidateParts.month === month &&
    candidateParts.day === day &&
    candidateParts.hour === hour &&
    candidateParts.minute === minute;

  return isExactMatch ? candidate.toISOString() : null;
}

function validateQuantity(value: string, movementType: MovementType) {
  const quantity = value.trim();
  if (!quantity) return "Quantity is required.";
  if (!/^[+-]?(?:\d+(?:\.\d{1,3})?|\.\d{1,3})$/.test(quantity)) {
    return "Use a finite number with no more than three decimal places.";
  }

  const unsignedQuantity = quantity.replace(/^[+-]/, "");
  const [integerPart] = unsignedQuantity.split(".");
  const significantInteger = (integerPart || "0").replace(/^0+/, "") || "0";
  if (significantInteger.length > 15) {
    return "Quantity exceeds the supported NUMERIC(18,3) range.";
  }

  const isZero = unsignedQuantity.replace(/[.0]/g, "").length === 0;
  if (isZero) return "Quantity must be nonzero.";
  if (movementType === "RECEIPT" && quantity.startsWith("-")) {
    return "Receipt quantity must be positive.";
  }
  if (movementType === "WASTE" && !quantity.startsWith("-")) {
    return "Waste quantity must be negative.";
  }
  return null;
}

function MovementEntryModal({ items, onCancel, onSaved }: MovementEntryModalProps) {
  const [sourceId] = useState(createSourceId);
  const [ingredientId, setIngredientId] = useState("");
  const [movementType, setMovementType] = useState<MovementType>("RECEIPT");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [recordedBy, setRecordedBy] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const selectedItem = useMemo(
    () => items.find((item) => item.ingredient_id === ingredientId),
    [ingredientId, items],
  );

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) onCancel();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [isSubmitting, onCancel]);

  const validateForm = () => {
    const nextErrors: FormErrors = {};
    if (!ingredientId) nextErrors.ingredient = "Select an ingredient.";

    const quantityError = validateQuantity(quantity, movementType);
    if (quantityError) nextErrors.quantity = quantityError;

    if (movementType !== "RECEIPT" && !reason.trim()) {
      nextErrors.reason = "Reason is required for Waste and Adjustment.";
    }

    if (!occurredAt) {
      nextErrors.occurredAt = "Event date and time are required.";
    } else {
      const timestamp = toCafeTimestamp(occurredAt);
      if (!timestamp) {
        nextErrors.occurredAt = `Enter a valid time in ${CAFE_TIME_ZONE}.`;
      } else if (new Date(timestamp).getTime() > Date.now()) {
        nextErrors.occurredAt = "Event date and time cannot be in the future.";
      }
    }

    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !validateForm()) return;

    const timestamp = toCafeTimestamp(occurredAt);
    if (!timestamp) return;

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const movement = await createInventoryMovement({
        cafe_id: ACTIVE_CAFE_ID,
        ingredient_id: ingredientId,
        movement_type: movementType,
        quantity_delta: quantity.trim(),
        source_type: "manual_entry",
        source_id: sourceId,
        occurred_at: timestamp,
        recorded_by: recordedBy.trim() || null,
        reason: reason.trim() || null,
      });
      await onSaved(movement);
    } catch (requestError) {
      setSubmitError(
        requestError instanceof Error
          ? requestError.message
          : "The movement could not be saved. Your entry is still available to retry.",
      );
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
        aria-labelledby="movement-entry-title"
      >
        <div className="entry-modal__header">
          <div>
            <p className="page__eyebrow">Inventory Movement</p>
            <h2 id="movement-entry-title">New Entry</h2>
          </div>
          <button
            className="modal-close"
            type="button"
            aria-label="Close movement entry"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            ×
          </button>
        </div>

        <div className="entry-warning">
          Saving writes a real movement to the local CAFE001 demo database.
        </div>

        <form className="entry-form" onSubmit={handleSubmit} noValidate>
          <label className="form-field">
            <span>Ingredient</span>
            <select
              value={ingredientId}
              aria-invalid={Boolean(errors.ingredient)}
              onChange={(event) => setIngredientId(event.target.value)}
            >
              <option value="">Select an ingredient</option>
              {items.map((item) => (
                <option key={item.ingredient_id} value={item.ingredient_id}>
                  {item.name} ({item.base_unit})
                </option>
              ))}
            </select>
            {errors.ingredient && <small className="field-error">{errors.ingredient}</small>}
          </label>

          <label className="form-field">
            <span>Movement Type</span>
            <select
              value={movementType}
              onChange={(event) => setMovementType(event.target.value as MovementType)}
            >
              <option value="RECEIPT">Receipt</option>
              <option value="WASTE">Waste</option>
              <option value="ADJUSTMENT">Adjustment</option>
            </select>
          </label>

          <label className="form-field">
            <span>Quantity{selectedItem ? ` (${selectedItem.base_unit})` : ""}</span>
            <input
              type="text"
              inputMode="decimal"
              value={quantity}
              placeholder={movementType === "WASTE" ? "e.g. -250" : "e.g. 1000"}
              aria-invalid={Boolean(errors.quantity)}
              onChange={(event) => setQuantity(event.target.value)}
            />
            <small className={errors.quantity ? "field-error" : "field-help"}>
              {errors.quantity ?? "Receipts are positive; waste is negative; adjustments may be signed."}
            </small>
          </label>

          <label className="form-field">
            <span>
              Reason
              {movementType !== "RECEIPT" && <em>Required</em>}
            </span>
            <textarea
              value={reason}
              rows={3}
              aria-invalid={Boolean(errors.reason)}
              onChange={(event) => setReason(event.target.value)}
            />
            {errors.reason && <small className="field-error">{errors.reason}</small>}
          </label>

          <label className="form-field">
            <span>Event Date and Time</span>
            <input
              type="datetime-local"
              value={occurredAt}
              aria-invalid={Boolean(errors.occurredAt)}
              onChange={(event) => setOccurredAt(event.target.value)}
            />
            <small className={errors.occurredAt ? "field-error" : "field-help"}>
              {errors.occurredAt ?? `Required · ${CAFE_TIME_ZONE}`}
            </small>
          </label>

          <label className="form-field">
            <span>Recorded By <em>Optional</em></span>
            <input
              type="text"
              value={recordedBy}
              placeholder="Employee or operator ID"
              onChange={(event) => setRecordedBy(event.target.value)}
            />
          </label>

          {submitError && (
            <div className="form-submit-error" role="alert">
              <strong>Entry was not confirmed</strong>
              <span>{submitError}</span>
              <small>You can retry safely; this form retains the same event identity.</small>
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
              {isSubmitting ? "Saving…" : "Save Entry"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default MovementEntryModal;
