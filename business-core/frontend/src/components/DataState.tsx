type ErrorStateProps = {
  message: string;
  onRetry: () => void;
};

export function LoadingState() {
  return (
    <div className="data-state" role="status">
      <span className="loading-indicator" aria-hidden="true" />
      <strong>Loading inventory data</strong>
      <p>Connecting to CaféPilot Business Core…</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="data-state data-state--error" role="alert">
      <strong>Inventory data is unavailable</strong>
      <p>{message}</p>
      <button className="secondary-button" type="button" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="data-state">
      <strong>No {label} found</strong>
      <p>There are no records to display for CAFE001.</p>
    </div>
  );
}
