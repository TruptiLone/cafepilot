import { useState } from "react";
import { type AgentRun, AgentApiError, decideAll, decideStep } from "../../api/agent";
import { describeChange, evidenceFor } from "./changes";
import { actionLabel, conditionText } from "./labels";

type ApprovalPanelProps = {
  run: AgentRun;
  onUpdated: (run: AgentRun) => void;
  onShowStep: (nodeId: string) => void;
};

type Pending = { nodeId: string | "all"; decision: "approved" | "rejected" } | null;

function ApprovalPanel({ run, onUpdated, onShowStep }: ApprovalPanelProps) {
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  const waiting = run.nodes.filter((step) => step.status === "awaiting_approval");
  if (waiting.length === 0) return null;

  async function decide(nodeId: string | "all", decision: "approved" | "rejected") {
    setPending({ nodeId, decision });
    setError(null);
    try {
      const updated =
        nodeId === "all" ? await decideAll(run.run_id, decision) : await decideStep(run.run_id, nodeId, decision);
      onUpdated(updated);
    } catch (caught) {
      setError(
        caught instanceof AgentApiError ? caught.message : "Your decision was not saved. Try again.",
      );
    } finally {
      setPending(null);
    }
  }

  const busy = pending !== null;

  return (
    <section className="approval-panel" aria-labelledby="approval-title">
      <div className="approval-panel__header">
        <h3 id="approval-title">
          {waiting.length === 1 ? "One change needs your OK" : `${waiting.length} changes need your OK`}
        </h3>
        {waiting.length > 1 && (
          <button
            type="button"
            className="primary-button"
            disabled={busy}
            onClick={() => decide("all", "approved")}
          >
            {pending?.nodeId === "all" ? "Approving…" : "Approve all"}
          </button>
        )}
      </div>

      <ul className="approval-panel__list">
        {waiting.map((step) => {
          const facts = evidenceFor(step, run);
          const isThis = pending?.nodeId === step.node_id;
          return (
            <li key={step.node_id} className="approval-card">
              <p className="approval-card__kind">{actionLabel(step.action)}</p>
              <p className="approval-card__change">{describeChange(step, run)}</p>
              {step.only_if && (
                <p className="approval-card__condition">
                  <strong>Approve only if:</strong> {conditionText(step.only_if)}
                </p>
              )}
              {facts.length > 0 && (
                <ul className="approval-card__facts" aria-label="What CaféPilot found">
                  {facts.map((fact) => (
                    <li key={fact}>{fact}</li>
                  ))}
                </ul>
              )}
              <div className="approval-card__actions">
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy}
                  onClick={() => decide(step.node_id, "approved")}
                >
                  {isThis && pending?.decision === "approved" ? "Approving…" : "Approve"}
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => decide(step.node_id, "rejected")}
                >
                  {isThis && pending?.decision === "rejected" ? "Declining…" : "Decline"}
                </button>
                <button type="button" className="text-button" onClick={() => onShowStep(step.node_id)}>
                  Show details
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      {error && (
        <p className="ask-bar__error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export default ApprovalPanel;
