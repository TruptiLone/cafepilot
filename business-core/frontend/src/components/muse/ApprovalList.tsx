import { useEffect, useRef, useState } from "react";
import { type AgentRun, AgentApiError, decideAll, decideStep } from "../../api/agent";
import ChangeForm, { canChange } from "../agent/ChangeForm";
import { describeChange, evidenceFor } from "../agent/changes";
import { conditionText } from "../agent/labels";
import { tileTitle } from "./stages";

type ApprovalListProps = {
  run: AgentRun;
  selectedId: string | null;
  onSelect: (nodeId: string | null) => void;
  onUpdated: (run: AgentRun) => void;
};

type Pending = { nodeId: string | "all"; decision: "approved" | "rejected" } | null;

/** Every change Muse wants to make, one card each: approve, change, or decline. */
function ApprovalList({ run, selectedId, onSelect, onUpdated }: ApprovalListProps) {
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const cards = useRef(new Map<string, HTMLElement>());
  const waiting = run.nodes.filter((step) => step.status === "awaiting_approval");

  useEffect(() => {
    if (!selectedId) return;
    const card = cards.current.get(selectedId);
    if (card && typeof card.scrollIntoView === "function") card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [selectedId]);

  if (waiting.length === 0) return null;

  async function decide(nodeId: string | "all", decision: "approved" | "rejected", editedArgs?: Record<string, unknown>) {
    setPending({ nodeId, decision });
    setError(null);
    try {
      const updated =
        nodeId === "all" ? await decideAll(run.run_id, decision) : await decideStep(run.run_id, nodeId, decision, editedArgs);
      setEditing(null);
      onSelect(null);
      onUpdated(updated);
    } catch (caught) {
      setError(caught instanceof AgentApiError ? caught.message : "Your decision was not saved. Try again.");
    } finally {
      setPending(null);
    }
  }

  const busy = pending !== null;

  return (
    <section className="mz-approvals" aria-labelledby="mz-approvals-title">
      <div className="mz-approvals__head">
        <div>
          <h3 id="mz-approvals-title">
            {waiting.length === 1 ? "1 change needs your OK" : `${waiting.length} changes need your OK`}
          </h3>
          <p>Nothing changes until you approve it.</p>
        </div>
        {waiting.length > 1 && (
          <button type="button" className="mz-btn mz-btn--ghost" disabled={busy} onClick={() => decide("all", "approved")}>
            {pending?.nodeId === "all" ? "Approving…" : "Approve all"}
          </button>
        )}
      </div>

      {waiting.map((step, index) => {
        const facts = evidenceFor(step, run);
        const isThis = pending?.nodeId === step.node_id;
        return (
          <article
            key={step.node_id}
            ref={(el) => {
              if (el) cards.current.set(step.node_id, el);
              else cards.current.delete(step.node_id);
            }}
            className={`mz-act${selectedId === step.node_id ? " is-sel" : ""}`}
            aria-label={`Change to approve: ${tileTitle(step)}`}
          >
            <div className="mz-act__top">
              <span className="mz-chip mz-chip--wait">Needs your OK</span>
              {step.action === "propose_supplier_order" && <span className="mz-tag">Simulated supplier</span>}
              {step.action === "schedule_shift" && <span className="mz-tag">Simulated reply</span>}
              <span className="mz-act__count">
                {index + 1} of {waiting.length}
              </span>
            </div>
            <p className="mz-act__title">{describeChange(step, run)}</p>
            {editing === step.node_id ? (
              <ChangeForm
                step={step}
                run={run}
                busy={busy}
                onSubmit={(edited) => decide(step.node_id, "approved", edited)}
                onCancel={() => {
                  setEditing(null);
                  setError(null);
                }}
              />
            ) : (
              <div className="mz-act__btns">
                <button type="button" className="mz-btn mz-btn--primary" disabled={busy} onClick={() => decide(step.node_id, "approved")}>
                  {isThis && pending?.decision === "approved" ? "Approving…" : "Approve"}
                </button>
                {canChange(step) && (
                  <button
                    type="button"
                    className="mz-btn mz-btn--secondary"
                    disabled={busy}
                    onClick={() => {
                      setEditing(step.node_id);
                      setError(null);
                    }}
                  >
                    Change
                  </button>
                )}
                <button type="button" className="mz-btn mz-btn--text" disabled={busy} onClick={() => decide(step.node_id, "rejected")}>
                  {isThis && pending?.decision === "rejected" ? "Declining…" : "Decline"}
                </button>
              </div>
            )}
            <details className="mz-why">
              <summary>Why</summary>
              <ul>
                <li>
                  <b>Muse's reason</b>
                  {step.reason}
                </li>
                {step.only_if && (
                  <li>
                    <b>Approve only if</b>
                    {conditionText(step.only_if)}
                  </li>
                )}
                {facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>
            </details>
          </article>
        );
      })}
      {error && (
        <p className="mz-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export default ApprovalList;
