import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getStockCountDetail,
  verifyStockCount,
  type InventoryItem,
  type StockCountDetail,
} from "../api/inventory";
import { CAFE_TIME_ZONE, toCafeTimestamp } from "../utils/cafeTime";

type StockCountVerifyModalProps = {
  countId: string;
  items: InventoryItem[];
  onCancel: () => void;
  onVerified: (count: StockCountDetail) => void;
};

function StockCountVerifyModal({
  countId,
  items,
  onCancel,
  onVerified,
}: StockCountVerifyModalProps) {
  const [detail, setDetail] = useState<StockCountDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [observedAt, setObservedAt] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const itemLookup = useMemo(
    () => new Map(items.map((item) => [item.ingredient_id, item])),
    [items],
  );

  const loadDetail = useCallback(async (signal?: AbortSignal) => {
    setIsLoading(true);
    setLoadError(null);
    try {
      setDetail(await getStockCountDetail(countId, signal));
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setLoadError(
        requestError instanceof Error
          ? requestError.message
          : "The stock count details could not be loaded.",
      );
    } finally {
      if (!signal?.aborted) setIsLoading(false);
    }
  }, [countId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadDetail(controller.signal);
    return () => controller.abort();
  }, [loadDetail]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) return;

    if (!observedAt) {
      setFieldError("Actual observation date and time are required.");
      return;
    }
    const timestamp = toCafeTimestamp(observedAt);
    if (!timestamp) {
      setFieldError(`Enter a valid time in ${CAFE_TIME_ZONE}.`);
      return;
    }
    if (new Date(timestamp).getTime() > Date.now()) {
      setFieldError("Observation date and time cannot be in the future.");
      return;
    }
    if (!confirmed) {
      setFieldError("Confirm that this observation establishes a new stock baseline.");
      return;
    }

    setFieldError(null);
    setSubmitError(null);
    setIsSubmitting(true);
    try {
      onVerified(await verifyStockCount(countId, { counted_at: timestamp }));
    } catch (requestError) {
      setSubmitError(
        requestError instanceof Error
          ? requestError.message
          : "The stock count could not be verified.",
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
        aria-labelledby="stock-count-verify-title"
      >
        <div className="entry-modal__header">
          <div>
            <p className="page__eyebrow">Physical Stock Count</p>
            <h2 id="stock-count-verify-title">Verify Count</h2>
          </div>
          <button
            className="modal-close"
            type="button"
            aria-label="Close stock count verification"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            ×
          </button>
        </div>

        <div className="baseline-warning">
          <strong>Verification establishes a new inventory baseline.</strong>
          <span>Review every physical quantity and provide the actual observation time.</span>
        </div>

        <div className="verification-content">
          {isLoading && <div className="panel-loading">Loading count details…</div>}
          {loadError && (
            <div className="form-submit-error" role="alert">
              <strong>Count details are unavailable</strong>
              <span>{loadError}</span>
              <button
                className="secondary-button"
                type="button"
                onClick={() => void loadDetail()}
              >
                Try again
              </button>
            </div>
          )}

          {detail && (
            <>
              <div className="verification-summary">
                <span>Count ID</span>
                <strong>{detail.count_id}</strong>
                <span>Counted By</span>
                <strong>{detail.counted_by ?? "Not recorded"}</strong>
                <span>Items</span>
                <strong>{detail.lines.length}</strong>
              </div>

              <div className="verification-lines">
                {detail.lines.map((line) => {
                  const item = itemLookup.get(line.ingredient_id);
                  return (
                    <div className="verification-line" key={line.count_line_id}>
                      <div>
                        <strong>{item?.name ?? line.ingredient_id}</strong>
                        <span>{line.ingredient_id}</span>
                      </div>
                      <strong>
                        {line.counted_quantity} {item?.base_unit ?? ""}
                      </strong>
                    </div>
                  );
                })}
              </div>

              <form className="verification-form" onSubmit={handleSubmit} noValidate>
                <label className="form-field">
                  <span>Actual Observation Date and Time</span>
                  <input
                    type="datetime-local"
                    value={observedAt}
                    aria-invalid={Boolean(fieldError)}
                    onChange={(event) => {
                      setObservedAt(event.target.value);
                      setFieldError(null);
                    }}
                  />
                  <small className={fieldError ? "field-error" : "field-help"}>
                    {fieldError ?? `Required · ${CAFE_TIME_ZONE}`}
                  </small>
                </label>

                <label className="confirmation-field">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => {
                      setConfirmed(event.target.checked);
                      setFieldError(null);
                    }}
                  />
                  <span>
                    I reviewed these physical quantities and confirm this observation
                    should become the inventory baseline.
                  </span>
                </label>

                {submitError && (
                  <div className="form-submit-error" role="alert">
                    <strong>Count was not verified</strong>
                    <span>{submitError}</span>
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
                  <button
                    className="primary-button"
                    type="submit"
                    disabled={isSubmitting || detail.status !== "draft"}
                  >
                    {isSubmitting ? "Verifying…" : "Verify Count"}
                  </button>
                </div>
              </form>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

export default StockCountVerifyModal;
