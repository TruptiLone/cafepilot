import type { AgentRun, RunStatus, StepStatus } from "../../api/agent";

const ACTION_LABELS: Record<string, string> = {
  list_ingredients: "List ingredients",
  get_stock_estimates: "Check stock",
  list_stock_counts: "List stock counts",
  get_stock_count: "Open a stock count",
  list_inventory_movements: "Review stock movements",
  create_stock_count: "Record a stock count",
  verify_stock_count: "Verify a stock count",
  record_inventory_movement: "Record a stock movement",
  list_products: "List menu products",
  list_sales: "List sales",
  sales_summary: "Summarize sales",
  project_recipe_usage: "Work out ingredient needs",
  list_employees: "List staff",
  list_shifts: "Check existing shifts",
  coverage_report: "Check staff coverage",
  calculate_shortfall: "Work out what to order",
  estimate_event_demand: "Expected demand from past events",
  get_purchase_order: "Look up the order",
  schedule_shift: "Add a shift",
  cancel_shift: "Cancel a shift",
  propose_supplier_order: "Order from supplier",
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action.replaceAll("_", " ");
}

export const STEP_STATUS_LABELS: Record<StepStatus, string> = {
  pending: "Queued",
  awaiting_approval: "Needs your OK",
  approved: "Approved",
  running: "Working",
  done: "Done",
  failed: "Failed",
  rejected: "Declined",
  skipped: "Skipped",
};

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  running: "Working",
  awaiting_approval: "Waiting for you",
  completed: "Done",
  completed_with_issues: "Finished, not every step ran",
};

/** Status for a full run: tells "you said no" apart from "something broke". */
export function runStatusLabel(run: AgentRun): { label: string; tone: string } {
  if (run.waiting_on && run.waiting_on.length > 0 && run.status !== "awaiting_approval" && run.status !== "running") {
    return { label: `Waiting on ${run.waiting_on.join(" and ")}`, tone: "awaiting_approval" };
  }
  if (run.status !== "completed_with_issues") {
    return { label: RUN_STATUS_LABELS[run.status], tone: run.status };
  }
  if (run.nodes.some((step) => step.status === "failed")) {
    return { label: "Finished with issues", tone: "completed_with_issues" };
  }
  const writesDone = run.nodes.some((step) => step.side_effect === "write" && step.status === "done");
  return writesDone
    ? { label: "Done, some changes declined", tone: "completed_partial" }
    : { label: "No changes made", tone: "completed_none" };
}

export function conditionText(text: string): string {
  return text.replace(/^\s*approve\s+only\s+if:?\s*/i, "");
}
