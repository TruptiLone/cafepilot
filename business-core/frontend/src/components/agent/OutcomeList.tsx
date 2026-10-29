import { Link } from "react-router-dom";
import type { AgentRun } from "../../api/agent";
import { describeChange } from "./changes";

const LINKS: Record<string, { to: string; label: string }> = {
  schedule_shift: { to: "/staffing", label: "See it in Staffing" },
  cancel_shift: { to: "/staffing", label: "See it in Staffing" },
  create_stock_count: { to: "/inventory", label: "See it in Inventory" },
  verify_stock_count: { to: "/inventory", label: "See it in Inventory" },
  record_inventory_movement: { to: "/inventory", label: "See it in Inventory" },
};

function OutcomeList({ run }: { run: AgentRun }) {
  const writes = run.nodes.filter(
    (step) => step.side_effect === "write" && ["done", "rejected", "failed", "skipped"].includes(step.status),
  );
  if (writes.length === 0) return null;
  return (
    <section className="outcome-list" aria-labelledby="outcome-title">
      <h3 id="outcome-title">What happened</h3>
      <ul>
        {writes.map((step) => {
          const link = LINKS[step.action];
          let prefix = "Done";
          if (step.replay_of) prefix = "Already done earlier, not repeated";
          else if (step.status === "rejected") prefix = "You declined";
          else if (step.status === "failed") prefix = "Could not complete";
          else if (step.status === "skipped")
            prefix = step.error && !step.error.startsWith("skipped because") ? "Not needed" : "Skipped";
          return (
            <li key={step.node_id} className={`outcome-list__item outcome-list__item--${step.status}`}>
              <span>
                <strong>{prefix}:</strong> {describeChange(step, run)}
                {(step.status === "failed" || prefix === "Not needed") && step.error ? `. ${step.error}` : ""}
              </span>
              {step.status === "done" && link && <Link to={link.to}>{link.label}</Link>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default OutcomeList;
