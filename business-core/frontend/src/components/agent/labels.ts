import type { RunStatus, StepStatus } from "../../api/agent";

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
  completed_with_issues: "Finished with issues",
};
