import { Link } from "react-router-dom";
import type { AgentRun, RunStep } from "../../api/agent";
import { describeChange } from "../agent/changes";

type Group = "done" | "waiting" | "problem" | "declined" | "skipped";

const GROUPS: { id: Group; label: string }[] = [
  { id: "waiting", label: "Waiting for confirmation" },
  { id: "problem", label: "Needs another plan" },
  { id: "done", label: "Done" },
  { id: "declined", label: "Declined" },
  { id: "skipped", label: "Not needed" },
];

const LINKS: Record<string, { to: string; label: string }> = {
  schedule_shift: { to: "/staffing", label: "View in Staffing" },
  cancel_shift: { to: "/staffing", label: "View in Staffing" },
  propose_supplier_order: { to: "/receiving", label: "View deliveries" },
  record_inventory_movement: { to: "/inventory", label: "View in Inventory" },
};

function groupOf(step: RunStep): Group {
  if (step.status === "rejected") return "declined";
  if (step.status === "skipped") return "skipped";
  if (step.status === "failed") return "problem";
  const tone = step.acknowledgement?.tone;
  if (tone === "waiting") return "waiting";
  if (tone === "problem") return "problem";
  if (tone === "declined") return "declined";
  return "done";
}

type PlanReceiptProps = {
  run: AgentRun;
  onReplan: (nodeId: string) => void;
  onOpenRun: (runId: string) => void;
  replanning: string | null;
};

/** What happened to each change Muse proposed, kept honest: sent is not the same as confirmed. */
function PlanReceipt({ run, onReplan, onOpenRun, replanning }: PlanReceiptProps) {
  const writes = run.nodes.filter(
    (step) => step.side_effect === "write" && ["done", "rejected", "failed", "skipped"].includes(step.status),
  );
  if (writes.length === 0) return null;
  const links = [...new Map(writes.filter((s) => s.status === "done" && LINKS[s.action]).map((s) => [LINKS[s.action].to, LINKS[s.action]])).values()];

  return (
    <section className="mz-rc" aria-labelledby="mz-rc-title">
      <div className="mz-rc__head">
        <div>
          <h3 id="mz-rc-title">What happened</h3>
          <p>{run.goal}</p>
        </div>
      </div>
      {GROUPS.map((group) => {
        const items = writes.filter((s) => groupOf(s) === group.id);
        if (items.length === 0) return null;
        return (
          <div key={group.id} className="mz-rc__sec">
            <span className="mz-label">{group.label}</span>
            {items.map((step) => {
              const ack = step.acknowledgement;
              return (
                <div key={step.node_id} className={`mz-rc__item is-${group.id}`}>
                  <span className="mz-tile__ico" aria-hidden="true">
                    {group.id === "done" && (
                      <svg viewBox="0 0 24 24">
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    )}
                    {(group.id === "declined" || group.id === "problem") && (
                      <svg viewBox="0 0 24 24">
                        <path d="M18 6 6 18M6 6l12 12" />
                      </svg>
                    )}
                  </span>
                  <div className="mz-rc__body">
                    <div className="mz-rc__title">{ack?.headline ?? describeChange(step, run)}</div>
                    {ack && ack.details.length > 0 && <div className="mz-rc__sub">{ack.details.join(" · ")}</div>}
                    {!ack && step.error && <div className="mz-rc__sub">{step.error}</div>}
                    {ack?.caveat && <div className="mz-rc__caveat">{ack.caveat}</div>}
                    {step.lifecycle && step.lifecycle.length > 1 && (
                      <ol className="mz-trail" aria-label="Progress">
                        {step.lifecycle.map((stage, i) => (
                          <li key={`${stage.stage}-${i}`} className={`is-${stage.state}`}>
                            {stage.stage}
                          </li>
                        ))}
                      </ol>
                    )}
                    {ack?.next_step && !step.replanned_in && (
                      <button
                        type="button"
                        className="mz-btn mz-btn--primary mz-rc__replan"
                        disabled={replanning !== null}
                        onClick={() => onReplan(step.node_id)}
                      >
                        {replanning === step.node_id
                          ? "Muse is planning…"
                          : ack.next_step === "find_cover"
                            ? "Find someone else"
                            : "Find an alternative"}
                      </button>
                    )}
                    {step.replanned_in && (
                      <button type="button" className="mz-link" onClick={() => onOpenRun(step.replanned_in as string)}>
                        See Muse's new plan →
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
      {links.length > 0 && (
        <div className="mz-rc__foot">
          {links.map((link) => (
            <Link key={link.to} className="mz-link" to={link.to}>
              {link.label}
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

export default PlanReceipt;
