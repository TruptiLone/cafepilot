import type { RunStep } from "../../api/agent";
import { STEP_STATUS_LABELS, actionLabel } from "./labels";

type StepDetailProps = {
  step: RunStep;
  onClose: () => void;
};

function preview(value: unknown): string {
  const text = JSON.stringify(value, null, 2) ?? "";
  return text.length > 4000 ? `${text.slice(0, 4000)}\n…` : text;
}

function conditionText(text: string): string {
  return text.replace(/^\s*approve\s+only\s+if:?\s*/i, "");
}

function StepDetail({ step, onClose }: StepDetailProps) {
  const args = step.resolved_args ?? step.args;
  return (
    <aside className="step-detail" aria-label={`Details for ${actionLabel(step.action)}`}>
      <div className="step-detail__header">
        <div>
          <h3>{actionLabel(step.action)}</h3>
          <p className="step-detail__meta">
            {STEP_STATUS_LABELS[step.status]}
            {step.side_effect === "write" ? ", changes data" : ", read only"}
          </p>
        </div>
        <button type="button" className="text-button" onClick={onClose}>
          Close
        </button>
      </div>

      <p className="step-detail__reason">{step.reason}</p>
      {step.only_if && (
        <p className="step-detail__condition">
          <strong>Approve only if:</strong> {conditionText(step.only_if)}
        </p>
      )}
      {step.depends_on.length > 0 && (
        <p className="step-detail__meta">Waits for: {step.depends_on.join(", ")}</p>
      )}
      {step.replay_of && (
        <p className="step-detail__meta">Already done in an earlier run ({step.replay_of}), so it was not repeated.</p>
      )}
      {step.error && <p className="step-detail__error">{step.error}</p>}

      <h4>What it was asked to do</h4>
      <pre className="step-detail__code">{preview(args)}</pre>

      {step.result !== null && step.result !== undefined && (
        <>
          <h4>What came back</h4>
          <pre className="step-detail__code">{preview(step.result)}</pre>
        </>
      )}
      <p className="step-detail__technical">Action: {step.action}</p>
    </aside>
  );
}

export default StepDetail;
