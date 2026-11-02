import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { AgentRun, RunStep } from "../../api/agent";
import { STAGES, layout, stageOf, tileResult, tileState, tileTitle, type TileState } from "./stages";

export type PlanView = "tiles" | "graph";

type PlanMapProps = {
  run: AgentRun;
  view: PlanView;
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
};

function TileIcon({ state }: { state: TileState }) {
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

function Tile({
  step,
  run,
  selected,
  onSelect,
  style,
}: {
  step: RunStep;
  run: AgentRun;
  selected: boolean;
  onSelect: (id: string) => void;
  style?: React.CSSProperties;
}) {
  const state = tileState(step);
  const body = (
    <>
      <span className="mz-tile__top">
        <span className="mz-tile__ico">
          <TileIcon state={state} />
        </span>
        <span className="mz-tile__title">{tileTitle(step)}</span>
      </span>
      <span className="mz-tile__res">{tileResult(step, run)}</span>
      {state === "wait" && <span className="mz-chip mz-chip--wait">Needs your OK</span>}
    </>
  );
  return (
    <button
      type="button"
      className={`mz-tile${selected ? " is-sel" : ""}`}
      data-s={state}
      data-node={step.node_id}
      style={style}
      aria-pressed={selected}
      aria-label={state === "wait" ? `Review: ${tileTitle(step)}` : `Details: ${tileTitle(step)}`}
      onClick={() => onSelect(step.node_id)}
    >
      {body}
    </button>
  );
}

function TilesView({ run, selectedId, onSelect }: Omit<PlanMapProps, "view">) {
  return (
    <div className="mz-groups">
      {STAGES.map((stage) => {
        const steps = run.nodes.filter((s) => stageOf(s) === stage.id);
        if (steps.length === 0) return null;
        return (
          <section key={stage.id} className="mz-group" aria-label={stage.label}>
            <h4 className="mz-col-label">
              {stage.label} <span>{steps.length}</span>
            </h4>
            <div className="mz-group__tiles">
              {steps.map((step) => (
                <Tile key={step.node_id} step={step} run={run} selected={selectedId === step.node_id} onSelect={onSelect} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

const MARKERS = [
  ["grey", "#BDBAB4"],
  ["dark", "#3A3F47"],
  ["ind", "#2A2F6B"],
  ["red", "#C9938C"],
] as const;

function GraphView({ run, selectedId, onSelect }: Omit<PlanMapProps, "view">) {
  const mapRef = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<{ d: string; stroke: string; marker: string; dash: string; flow: boolean }[]>([]);
  const places = layout(run);
  const byId = new Map(run.nodes.map((s) => [s.node_id, s]));

  const draw = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const m = map.getBoundingClientRect();
    const next: typeof paths = [];
    for (const edge of run.edges) {
      const a = map.querySelector<HTMLElement>(`[data-node="${edge.source}"]`);
      const b = map.querySelector<HTMLElement>(`[data-node="${edge.target}"]`);
      const sa = byId.get(edge.source);
      const sb = byId.get(edge.target);
      if (!a || !b || !sa || !sb) continue;
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      const stateA = tileState(sa);
      const stateB = tileState(sb);
      let stroke = "#BDBAB4", marker = "grey", dash = "4 4", flow = false;
      if (stateB === "running") [stroke, marker, flow] = ["#2A2F6B", "ind", true];
      else if (stateB === "declined" || stateB === "failed") [stroke, marker, dash] = ["#C9938C", "red", ""];
      else if (stateA === "done") [stroke, marker, dash] = ["#3A3F47", "dark", ""];
      if (edge.kind === "safety") dash = "2 4";
      let d: string;
      const skips = rb.left - ra.right > (rb.width + 60);
      if (skips) {
        // Run along the gap after the source's column, then straight across the empty row.
        const x1 = ra.right - m.left + 2;
        const y1 = ra.top - m.top + Math.min(ra.height / 2, 26);
        const x2 = rb.left - m.left - 3;
        const y2 = rb.top - m.top + Math.min(rb.height / 2, 26);
        const xg = x1 + 18;
        const r = Math.min(8, Math.abs(y2 - y1) / 2);
        const dir = y2 > y1 ? 1 : -1;
        d = Math.abs(y2 - y1) < 1
          ? `M${x1} ${y1} L${x2} ${y2}`
          : `M${x1} ${y1} L${xg - r} ${y1} Q${xg} ${y1} ${xg} ${y1 + dir * r} L${xg} ${y2 - dir * r} Q${xg} ${y2} ${xg + r} ${y2} L${x2} ${y2}`;
      } else if (Math.abs(ra.left - rb.left) < 4) {
        const x = ra.left - m.left + ra.width / 2;
        d = `M${x} ${ra.bottom - m.top + 2} L${x} ${rb.top - m.top - 3}`;
      } else {
        const x1 = ra.right - m.left + 2;
        const y1 = ra.top - m.top + Math.min(ra.height / 2, 26);
        const x2 = rb.left - m.left - 3;
        const y2 = rb.top - m.top + Math.min(rb.height / 2, 26);
        const k = (x2 - x1) * 0.55;
        d = Math.abs(y1 - y2) < 1 ? `M${x1} ${y1} L${x2} ${y2}` : `M${x1} ${y1} C${x1 + k} ${y1}, ${x2 - k} ${y2}, ${x2} ${y2}`;
      }
      next.push({ d, stroke, marker, dash, flow });
    }
    setPaths(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run]);

  useLayoutEffect(() => {
    draw();
    const map = mapRef.current;
    if (!map || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(map);
    return () => observer.disconnect();
  }, [draw]);

  return (
    <div className="mz-map" ref={mapRef}>
      <svg className="mz-wires" aria-hidden="true">
        <defs>
          {MARKERS.map(([id, color]) => (
            <marker key={id} id={`mz-ah-${id}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0.5 0.8 L7 4 L0.5 7.2" fill="none" stroke={color} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </marker>
          ))}
        </defs>
        {paths.map((p, i) => (
          <path
            key={i}
            className={p.flow ? "mz-flow" : undefined}
            d={p.d}
            fill="none"
            stroke={p.stroke}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeDasharray={p.dash || undefined}
            markerEnd={`url(#mz-ah-${p.marker})`}
          />
        ))}
      </svg>
      {STAGES.map((stage, i) => (
        <div key={stage.id} className="mz-col-label" style={{ gridColumn: i + 1, gridRow: 1 }}>
          {stage.label}
        </div>
      ))}
      {run.nodes.map((step) => {
        const place = places.get(step.node_id);
        return (
          <Tile
            key={step.node_id}
            step={step}
            run={run}
            selected={selectedId === step.node_id}
            onSelect={onSelect}
            style={place ? { gridColumn: place.col, gridRow: place.row } : undefined}
          />
        );
      })}
    </div>
  );
}

export function useNarrow(): boolean {
  const query = "(max-width: 860px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(query).matches);
  useLayoutEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return;
    const update = () => setNarrow(media.matches);
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return narrow;
}

function PlanMap({ view, ...props }: PlanMapProps) {
  // On a phone the dependency map has no room for three columns: show the steps.
  const narrow = useNarrow();
  return (
    <>
      {view === "graph" && !narrow ? <GraphView {...props} /> : <TilesView {...props} />}
      <div className="mz-legend" aria-label="Tile states">
        <span><i className="lg-pending" />Pending</span>
        <span><i className="lg-running" />Running</span>
        <span><i className="lg-done" />Done</span>
        <span><i className="lg-wait" />Needs your OK</span>
        <span><i className="lg-declined" />Declined</span>
      </div>
    </>
  );
}

export default PlanMap;
