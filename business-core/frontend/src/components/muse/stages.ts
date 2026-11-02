import type { AgentRun, RunStep } from "../../api/agent";
import { describeChange } from "../agent/changes";
import { actionLabel } from "../agent/labels";

export type Stage = "lookup" | "workout" | "ok";
export type TileState = "pending" | "running" | "done" | "wait" | "declined" | "failed" | "skipped";

export const STAGES: { id: Stage; label: string }[] = [
  { id: "lookup", label: "Look up" },
  { id: "workout", label: "Work out" },
  { id: "ok", label: "Needs your OK" },
];

const WORK_OUT = new Set(["calculate_shortfall", "estimate_event_demand", "project_recipe_usage", "coverage_report", "find_suppliers"]);

/** Which column a step belongs in: data it fetched, numbers it worked out, or a change it proposes. */
export function stageOf(step: RunStep): Stage {
  if (step.side_effect === "write") return "ok";
  return WORK_OUT.has(step.action) ? "workout" : "lookup";
}

const TILE_TITLES: Record<string, string> = {
  get_stock_estimates: "Check stock",
  list_shifts: "Check shifts",
  coverage_report: "Who's missing",
  list_employees: "Check the team",
  sales_summary: "Check sales",
  estimate_event_demand: "Expected demand",
  calculate_shortfall: "What to order",
  find_suppliers: "Find suppliers",
  get_purchase_order: "Look up the order",
  propose_supplier_order: "Order from supplier",
  schedule_shift: "Add a shift",
  cancel_shift: "Cancel a shift",
};

export function tileTitle(step: RunStep): string {
  return TILE_TITLES[step.action] ?? actionLabel(step.action);
}

export function tileState(step: RunStep): TileState {
  switch (step.status) {
    case "pending":
    case "approved":
      return step.status === "approved" ? "running" : "pending";
    case "running":
      return "running";
    case "awaiting_approval":
      return "wait";
    case "rejected":
      return "declined";
    case "failed":
      return "failed";
    case "skipped":
      return "skipped";
    default:
      return "done";
  }
}

/** The one line under a tile's title: what Muse found, or what it will change. */
export function tileResult(step: RunStep, run: AgentRun): string {
  const state = tileState(step);
  if (state === "running") return "Working…";
  if (step.side_effect === "write") {
    if (state === "done" && step.acknowledgement) return step.acknowledgement.headline;
    if (state === "declined") return "Declined · nothing changed";
    if (state === "failed") return step.error ?? "Could not complete";
    if (state === "skipped") return step.error && !step.error.startsWith("skipped because") ? "Not needed" : "Skipped";
    return describeChange(step, run);
  }
  if (state === "pending") return step.reason;
  if (state === "failed") return step.error ?? "Could not complete";
  if (state === "skipped") return "Skipped";
  return step.summary ?? step.reason;
}

export function planCounts(run: AgentRun) {
  const checks = run.nodes.filter((s) => s.side_effect === "read");
  return {
    checks: checks.length,
    checksDone: checks.filter((s) => s.status === "done").length,
    waiting: run.nodes.filter((s) => s.status === "awaiting_approval").length,
    changes: run.nodes.filter((s) => s.side_effect === "write").length,
  };
}

/** Grid placement for the dependency view: column by stage, rows near the steps they depend on. */
export function layout(run: AgentRun): Map<string, { col: number; row: number }> {
  const col = (step: RunStep) => STAGES.findIndex((s) => s.id === stageOf(step)) + 1;
  const levelOf = new Map<string, number>();
  run.levels.forEach((ids, level) => ids.forEach((id) => levelOf.set(id, level)));
  const ordered = [...run.nodes].sort(
    (a, b) => col(a) - col(b) || (levelOf.get(a.node_id) ?? 0) - (levelOf.get(b.node_id) ?? 0),
  );
  const placed = new Map<string, { col: number; row: number }>();
  const taken = new Set<string>();
  for (const step of ordered) {
    const c = col(step);
    const parents = step.depends_on.map((id) => placed.get(id)).filter(Boolean) as { col: number; row: number }[];
    let row = 2;
    if (parents.length) {
      const sameCol = parents.filter((p) => p.col === c);
      row = sameCol.length
        ? Math.max(...sameCol.map((p) => p.row)) + 1
        : Math.round(parents.reduce((sum, p) => sum + p.row, 0) / parents.length);
    }
    // A wire that skips a column needs that column empty on the target's row.
    const skipFrom = Math.min(c, ...parents.map((p) => p.col + 1));
    const blocked = (r: number) =>
      taken.has(`${c}:${r}`) || Array.from({ length: c - skipFrom }, (_, i) => skipFrom + i).some((k) => taken.has(`${k}:${r}`));
    while (blocked(row)) row += 1;
    taken.add(`${c}:${row}`);
    placed.set(step.node_id, { col: c, row });
  }
  return placed;
}
