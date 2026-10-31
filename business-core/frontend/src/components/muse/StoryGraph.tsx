import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { AgentRun, RunStep } from "../../api/agent";
import ChangeForm, { canChange } from "../agent/ChangeForm";
import { evidenceFor } from "../agent/changes";
import { conditionText } from "../agent/labels";
import type { Box, Story } from "./story";

type Decide = (runId: string, step: RunStep, decision: "approved" | "rejected", edited?: Record<string, unknown>) => Promise<void>;

type StoryGraphProps = {
  story: Story;
  runs: Record<string, AgentRun>;
  busy: string | null;
  reveal: boolean;
  onDecide: Decide;
  onOpenRun: (runId: string) => void;
};

function Icon({ state }: { state: Box["state"] }) {
  if (state === "done")
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    );
  if (state === "declined" || state === "failed")
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18 6 6 18M6 6l12 12" />
      </svg>
    );
  return null;
}

function DecisionBody({ box, run, busy, onDecide }: { box: Box; run: AgentRun; busy: string | null; onDecide: Decide }) {
  const [editing, setEditing] = useState(false);
  const step = box.steps[0];
  if (!step) return null;
  const ack = step.acknowledgement;
  const working = busy === `${box.runId}:${step.node_id}`;
  if (step.status === "awaiting_approval") {
    if (editing)
      return (
        <div className="sg-edit">
          <ChangeForm
            step={step}
            run={run}
            busy={working}
            onSubmit={(edited) => onDecide(box.runId, step, "approved", edited).then(() => setEditing(false))}
            onCancel={() => setEditing(false)}
          />
        </div>
      );
    return (
      <div className="sg-acts">
        <button type="button" className="mz-btn mz-btn--primary" disabled={busy !== null} onClick={() => onDecide(box.runId, step, "approved")}>
          {working ? "Approving…" : "Approve"}
        </button>
        {canChange(step) && (
          <button type="button" className="mz-btn mz-btn--secondary" disabled={busy !== null} onClick={() => setEditing(true)}>
            Change
          </button>
        )}
        <button type="button" className="mz-btn mz-btn--text" disabled={busy !== null} onClick={() => onDecide(box.runId, step, "rejected")}>
          Decline
        </button>
      </div>
    );
  }
  return (
    <>
      {ack && ack.headline !== box.subtitle && <div className="sg-ack">{ack.headline}</div>}
      {step.lifecycle && step.lifecycle.length > 1 && (
        <ol className="sg-trail" aria-label="Progress">
          {step.lifecycle.map((stage, i) => (
            <li key={`${stage.stage}-${i}`} className={`is-${stage.state}`}>
              {stage.stage}
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function DetailCard({ box, run }: { box: Box; run: AgentRun | undefined }) {
  if (box.kind === "decision") {
    const step = box.steps[0];
    if (!step || !run) return null;
    const facts = evidenceFor(step, run);
    return (
      <div className="sg-tip" role="tooltip">
        <h4>Why Muse suggests this</h4>
        <p>{step.reason}</p>
        {step.only_if && <p className="sg-tip__muted">Approve only if {conditionText(step.only_if)}</p>}
        {step.acknowledgement?.details.map((d) => (
          <p key={d} className="sg-tip__muted">
            {d}
          </p>
        ))}
        {facts.length > 0 && (
          <ul>
            {facts.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        )}
        <p className="sg-tip__steps">step: {step.action}</p>
      </div>
    );
  }
  if (!box.detail) return null;
  return (
    <div className="sg-tip" role="tooltip">
      <h4>{box.detail.title}</h4>
      {box.detail.intro && <p>{box.detail.intro}</p>}
      {box.detail.rows.length > 0 && (
        <dl>
          {box.detail.rows.map((r) => (
            <div key={r.label} className={r.tone ? `is-${r.tone}` : undefined}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {box.detail.steps.length > 0 && <p className="sg-tip__steps">steps: {box.detail.steps.join(" · ")}</p>}
    </div>
  );
}

/** The owner view of a plan: a vertical graph, decisions inside it, details on hover (click to pin). */
function StoryGraph({ story, runs, busy, reveal, onDecide, onOpenRun }: StoryGraphProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<{ d: string; dashed: boolean; label?: string; lx: number; ly: number }[]>([]);
  const [hover, setHover] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const open = pinned ?? hover;

  const draw = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const m = el.getBoundingClientRect();
    const out: typeof paths = [];
    for (const e of story.edges) {
      const a = el.querySelector<HTMLElement>(`[data-box="${e.from.replace(/"/g, "\\\"")}"]`);
      const b = el.querySelector<HTMLElement>(`[data-box="${e.to.replace(/"/g, "\\\"")}"]`);
      if (!a || !b) continue;
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      const x1 = ra.left - m.left + ra.width / 2;
      const y1 = ra.bottom - m.top + 2;
      const x2 = rb.left - m.left + rb.width / 2;
      const y2 = rb.top - m.top - 3;
      const k = (y2 - y1) * 0.55;
      out.push({ d: `M${x1} ${y1} C${x1} ${y1 + k},${x2} ${y2 - k},${x2} ${y2}`, dashed: !!e.dashed, label: e.label, lx: (x1 + x2) / 2 + 8, ly: (y1 + y2) / 2 });
    }
    setPaths(out);
  }, [story]);

  useLayoutEffect(() => {
    draw();
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(el);
    return () => observer.disconnect();
  }, [draw]);

  return (
    <div
      className="sg"
      ref={ref}
      style={{ gridTemplateColumns: `repeat(${story.lanes}, minmax(0, 290px))`, gridTemplateRows: `repeat(${story.levels}, auto)` }}
    >
      <svg className="sg-wires" aria-hidden="true">
        <defs>
          <marker id="sg-a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0.5 .8L7 4L.5 7.2" fill="none" stroke="#3A3F47" strokeWidth="1.4" />
          </marker>
          <marker id="sg-g" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0.5 .8L7 4L.5 7.2" fill="none" stroke="#B5B1AA" strokeWidth="1.4" />
          </marker>
        </defs>
        {paths.map((p, i) => (
          <g key={i}>
            <path
              d={p.d}
              fill="none"
              stroke={p.dashed ? "#B5B1AA" : "#3A3F47"}
              strokeWidth="1.6"
              strokeDasharray={p.dashed ? "4 4" : undefined}
              markerEnd={`url(#${p.dashed ? "sg-g" : "sg-a"})`}
            />
            {p.label && (
              <text x={p.lx} y={p.ly} fontSize="11" fill="#6e747c">
                {p.label}
              </text>
            )}
          </g>
        ))}
      </svg>
      {story.boxes.map((box) => {
        const spanAll = box.kind === "event" || box.kind === "share";
        const run = runs[box.runId];
        const isOpen = open === box.id;
        const hasDetail = box.kind === "decision" ? box.steps.length > 0 : !!box.detail;
        const replanned = box.steps[0]?.replanned_in;
        return (
          <div
            key={box.id}
            data-box={box.id}
            className={`sg-box sg-box--${box.kind}${isOpen ? " is-open" : ""}${reveal ? " is-reveal" : ""}`}
            data-s={box.state}
            style={{
              gridColumn: spanAll ? `1 / span ${story.lanes}` : box.lane + 1,
              gridRow: box.level + 1,
              ["--d" as string]: `${box.level * 380}ms`,
            }}
            onMouseEnter={() => hasDetail && setHover(box.id)}
            onMouseLeave={() => setHover((h) => (h === box.id ? null : h))}
          >
            <button
              type="button"
              className="sg-head"
              aria-expanded={hasDetail ? isOpen : undefined}
              onClick={() => hasDetail && setPinned((p) => (p === box.id ? null : box.id))}
            >
              {box.kind !== "event" && (
                <span className="sg-ico">
                  <Icon state={box.state} />
                </span>
              )}
              <span className="sg-title">{box.title}</span>
            </button>
            <div className="sg-sub">{box.subtitle}</div>
            {box.kind === "decision" && run && <DecisionBody box={box} run={run} busy={busy} onDecide={onDecide} />}
            {replanned && runs[replanned] === undefined && (
              <button type="button" className="mz-link sg-open" onClick={() => onOpenRun(replanned)}>
                Open Muse's new plan →
              </button>
            )}
            {isOpen && hasDetail && <DetailCard box={box} run={run} />}
          </div>
        );
      })}
    </div>
  );
}

export default StoryGraph;
