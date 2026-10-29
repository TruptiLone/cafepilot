import { useLayoutEffect, useRef, useState } from "react";
import type { AgentRun, RunStep } from "../../api/agent";
import { STEP_STATUS_LABELS, actionLabel } from "./labels";

type TicketRailProps = {
  run: AgentRun;
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
};

type Line = { key: string; d: string; kind: "data" | "safety" };

function escapeId(id: string): string {
  return typeof CSS !== "undefined" && typeof CSS.escape === "function"
    ? CSS.escape(id)
    : id.replace(/["\\]/g, "\\$&");
}

function stageTitle(steps: RunStep[], index: number): string {
  if (steps.every((step) => step.side_effect === "write")) return "Changes for your OK";
  if (index === 0) return "Look things up";
  return "Then";
}

function TicketRail({ run, selectedId, onSelect }: TicketRailProps) {
  const railRef = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const byId = new Map(run.nodes.map((step) => [step.node_id, step]));

  useLayoutEffect(() => {
    const rail = railRef.current;
    if (!rail) return;
    const measure = () => {
      const origin = rail.getBoundingClientRect();
      const box = (id: string) =>
        rail.querySelector<HTMLElement>(`[data-ticket="${escapeId(id)}"]`)?.getBoundingClientRect();
      const next: Line[] = [];
      for (const edge of run.edges) {
        const from = box(edge.source);
        const to = box(edge.target);
        if (!from || !to) continue;
        const x1 = from.right - origin.left + rail.scrollLeft;
        const y1 = from.top + from.height / 2 - origin.top;
        const x2 = to.left - origin.left + rail.scrollLeft;
        const y2 = to.top + to.height / 2 - origin.top;
        const bend = Math.max(24, (x2 - x1) / 2);
        next.push({
          key: `${edge.source}-${edge.target}`,
          d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
          kind: edge.kind,
        });
      }
      setLines(next);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(rail);
    return () => observer?.disconnect();
  }, [run]);

  return (
    <div className="ticket-rail" ref={railRef} role="list" aria-label="Plan steps in order">
      <svg className="ticket-rail__lines" aria-hidden="true">
        {lines.map((line) => (
          <path key={line.key} d={line.d} className={`ticket-rail__line ticket-rail__line--${line.kind}`} />
        ))}
      </svg>
      {run.levels.map((ids, index) => {
        const steps = ids.map((id) => byId.get(id)).filter((step): step is RunStep => !!step);
        return (
          <section className="ticket-rail__stage" key={index} role="listitem" aria-label={`Stage ${index + 1}`}>
            <h3 className="ticket-rail__stage-title">
              <span className="ticket-rail__stage-number">{index + 1}</span>
              {stageTitle(steps, index)}
            </h3>
            <ul className="ticket-rail__tickets">
              {steps.map((step) => (
                <li key={step.node_id}>
                  <button
                    type="button"
                    className={`ticket ticket--${step.status}${step.side_effect === "write" ? " ticket--write" : ""}${
                      selectedId === step.node_id ? " is-selected" : ""
                    }`}
                    data-ticket={step.node_id}
                    aria-pressed={selectedId === step.node_id}
                    onClick={() => onSelect(step.node_id)}
                  >
                    <span className="ticket__title">{actionLabel(step.action)}</span>
                    <span className="ticket__reason">{step.reason}</span>
                    <span className="ticket__status">
                      <span className="ticket__dot" aria-hidden="true" />
                      {step.replay_of ? "Already done" : STEP_STATUS_LABELS[step.status]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export default TicketRail;
