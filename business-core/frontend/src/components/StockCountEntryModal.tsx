import { useMemo, useState } from "react";
import {
  ACTIVE_CAFE_ID,
  createStockCount,
  type InventoryItem,
  type StockCountDetail,
} from "../api/inventory";

type StockCountEntryModalProps = {
  items: InventoryItem[];
  onCancel: () => void;
  onSaved: (count: StockCountDetail) => void;
};

type CountLine = {
  key: string;
  ingredientId: string;
  quantity: string;
};

type LineError = {
  ingredient?: string;
  quantity?: string;
};

function createLine(): CountLine {
  return {
    key: crypto.randomUUID(),
    ingredientId: "",
    quantity: "",
  };
}

function validateCountQuantity(value: string) {
  const quantity = value.trim();
  if (!quantity) return "Counted quantity is required.";
  if (!/^(?:\d+(?:\.\d{1,3})?|\.\d{1,3})$/.test(quantity)) {
    return "Use a nonnegative number with no more than three decimal places.";
  }

  const [integerPart] = quantity.split(".");
  const significantInteger = (integerPart || "0").replace(/^0+/, "") || "0";
  if (significantInteger.length > 15) {
    return "Quantity exceeds the supported NUMERIC(18,3) range.";
  }
  return null;
}

function StockCountEntryModal({
  items,
  onCancel,
  onSaved,
}: StockCountEntryModalProps) {
  const [countedBy, setCountedBy] = useState("");
  const [lines, setLines] = useState<CountLine[]>([createLine()]);
  const [lineErrors, setLineErrors] = useState<Record<string, LineError>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const itemLookup = useMemo(
    () => new Map(items.map((item) => [item.ingredient_id, item])),
    [items],
  );

  const selectedIngredientIds = useMemo(
    () => new Set(lines.map((line) => line.ingredientId).filter(Boolean)),
    [lines],
  );

  const updateLine = (key: string, changes: Partial<CountLine>) => {
    setLines((currentLines) =>
      currentLines.map((line) => (line.key === key ? { ...line, ...changes } : line)),
    );
    setLineErrors((currentErrors) => ({ ...currentErrors, [key]: {} }));
    setFormError(null);
  };

  const validateForm = () => {
    if (!lines.length) {
      setFormError("Add at least one ingredient to the count.");
      return false;
    }

    const nextErrors: Record<string, LineError> = {};
    const ingredientCounts = new Map<string, number>();
    lines.forEach((line) => {
      if (line.ingredientId) {
        ingredientCounts.set(
          line.ingredientId,
          (ingredientCounts.get(line.ingredientId) ?? 0) + 1,
        );
      }
    });

    lines.forEach((line) => {
      const errors: LineError = {};
      if (!line.ingredientId) {
        errors.ingredient = "Select an ingredient.";
      } else if ((ingredientCounts.get(line.ingredientId) ?? 0) > 1) {
        errors.ingredient = "Each ingredient can appear only once.";
      }

      const quantityError = validateCountQuantity(line.quantity);
      if (quantityError) errors.quantity = quantityError;
      if (Object.keys(errors).length) nextErrors[line.key] = errors;
    });

    setLineErrors(nextErrors);
    setFormError(
      Object.keys(nextErrors).length
        ? "Correct the highlighted count lines before saving."
        : null,
    );
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !validateForm()) return;

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const count = await createStockCount({
        cafe_id: ACTIVE_CAFE_ID,
        status: "draft",
        counted_at: null,
        counted_by: countedBy.trim() || null,
        is_synthetic: true,
        items: lines.map((line) => ({
          ingredient_id: line.ingredientId,
          counted_quantity: line.quantity.trim(),
        })),
      });
      onSaved(count);
    } catch (requestError) {
      setSubmitError(
        requestError instanceof Error
          ? requestError.message
          : "The draft stock count could not be saved.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop">
      <section
        className="entry-modal entry-modal--wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="stock-count-entry-title"
      >
        <div className="entry-modal__header">
          <div>
            <p className="page__eyebrow">Physical Stock Count</p>
            <h2 id="stock-count-entry-title">New Count</h2>
          </div>
          <button
            className="modal-close"
            type="button"
            aria-label="Close stock count entry"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            ×
          </button>
        </div>

        <div className="entry-warning">
          This local demonstration count is marked synthetic and saved as a draft.
          No observation timestamp or estimated stock quantity is supplied automatically.
        </div>

        <form className="entry-form" onSubmit={handleSubmit} noValidate>
          <label className="form-field">
            <span>Counted By <em>Optional</em></span>
            <input
              type="text"
              value={countedBy}
              placeholder="Employee or operator ID"
              onChange={(event) => setCountedBy(event.target.value)}
            />
          </label>

          <div className="count-lines-heading">
            <div>
              <strong>Counted Ingredients</strong>
              <span>Enter direct physical observations only.</span>
            </div>
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setLines((currentLines) => [...currentLines, createLine()]);
                setFormError(null);
              }}
            >
              + Add Ingredient
            </button>
          </div>

          <div className="count-lines">
            {lines.map((line, index) => {
              const selectedItem = itemLookup.get(line.ingredientId);
              const errors = lineErrors[line.key] ?? {};
              return (
                <div className="count-line" key={line.key}>
                  <label className="form-field count-line__ingredient">
                    <span>Ingredient {index + 1}</span>
                    <select
                      value={line.ingredientId}
                      aria-label={`Ingredient ${index + 1}`}
                      aria-invalid={Boolean(errors.ingredient)}
                      onChange={(event) =>
                        updateLine(line.key, { ingredientId: event.target.value })
                      }
                    >
                      <option value="">Select an ingredient</option>
                      {items.map((item) => (
                        <option
                          key={item.ingredient_id}
                          value={item.ingredient_id}
                          disabled={
                            item.ingredient_id !== line.ingredientId &&
                            selectedIngredientIds.has(item.ingredient_id)
                          }
                        >
                          {item.name}
                        </option>
                      ))}
                    </select>
                    {errors.ingredient && (
                      <small className="field-error">{errors.ingredient}</small>
                    )}
                  </label>

                  <label className="form-field count-line__quantity">
                    <span>
                      Counted Quantity
                      {selectedItem && <em>{selectedItem.base_unit}</em>}
                    </span>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={line.quantity}
                      placeholder="0"
                      aria-label={`Counted Quantity ${index + 1}`}
                      aria-invalid={Boolean(errors.quantity)}
                      onChange={(event) =>
                        updateLine(line.key, { quantity: event.target.value })
                      }
                    />
                    {errors.quantity && (
                      <small className="field-error">{errors.quantity}</small>
                    )}
                  </label>

                  <button
                    className="remove-line-button"
                    type="button"
                    aria-label={`Remove ingredient ${index + 1}`}
                    disabled={isSubmitting}
                    onClick={() => {
                      setLines((currentLines) =>
                        currentLines.filter((currentLine) => currentLine.key !== line.key),
                      );
                      setFormError(null);
                    }}
                  >
                    Remove
                  </button>
                </div>
              );
            })}
          </div>

          {formError && <div className="inline-form-error">{formError}</div>}
          {submitError && (
            <div className="form-submit-error" role="alert">
              <strong>Draft count was not saved</strong>
              <span>{submitError}</span>
              <small>
                If the outcome is uncertain, check Stock Counts history before
                retrying so an existing draft is not overlooked.
              </small>
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
              {isSubmitting ? "Saving Draft…" : "Save Draft"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default StockCountEntryModal;
