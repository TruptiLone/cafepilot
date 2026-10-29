import type { AgentRun, RunStep } from "../../api/agent";

const CAFE_TIME_ZONE = "America/Los_Angeles";

type Row = Record<string, unknown>;

function asRows(value: unknown): Row[] {
  return Array.isArray(value) ? (value.filter((item) => item && typeof item === "object") as Row[]) : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

export function formatDay(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: CAFE_TIME_ZONE,
  }).format(new Date(value));
}

export function formatClock(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: CAFE_TIME_ZONE,
  })
    .format(new Date(value))
    .replace(":00", "");
}

function timeRange(start: unknown, end: unknown): string {
  const startText = text(start);
  const endText = text(end);
  if (!startText || !endText) return "";
  return `${formatDay(startText)}, ${formatClock(startText)}–${formatClock(endText)}`;
}

function formatQuantity(value: unknown, unit: unknown): string {
  const number = Number(value);
  const unitText = text(unit);
  if (Number.isNaN(number)) return `${text(value)} ${unitText}`.trim();
  if (unitText === "mL" && number >= 1000) return `${(number / 1000).toLocaleString("en-US")} L`;
  if (unitText === "g" && number >= 1000) return `${(number / 1000).toLocaleString("en-US")} kg`;
  return `${number.toLocaleString("en-US")} ${unitText}`.trim();
}

/** Every row any finished step returned for one of these actions. */
function resultsOf(run: AgentRun, action: string): Row[] {
  return run.nodes
    .filter((node) => node.action === action && node.status === "done")
    .flatMap((node) => (Array.isArray(node.result) ? asRows(node.result) : asRows([node.result])));
}

function employeeName(run: AgentRun, employeeId: string): string {
  const fromStaff = resultsOf(run, "list_employees").find((row) => row.employee_id === employeeId);
  if (fromStaff) return text(fromStaff.name);
  const fromShifts = resultsOf(run, "list_shifts").find((row) => row.employee_id === employeeId);
  return fromShifts ? text(fromShifts.employee_name) : employeeId;
}

function ingredientName(run: AgentRun, ingredientId: string): string {
  for (const action of ["get_stock_estimates", "list_ingredients"]) {
    const row = resultsOf(run, action).find((item) => item.ingredient_id === ingredientId);
    if (row) return text(row.name);
  }
  return ingredientId.toLowerCase().replaceAll("_", " ");
}

/** One plain sentence saying what the step will do (or did). */
export function describeChange(step: RunStep, run: AgentRun): string {
  const args = (step.resolved_args ?? step.args) as Row;
  switch (step.action) {
    case "schedule_shift":
      return `Schedule ${employeeName(run, text(args.employee_id))} for ${timeRange(args.start_at, args.end_at)}`;
    case "cancel_shift":
      return `Cancel shift ${text(args.shift_id)}`;
    case "propose_supplier_order": {
      const lines = asRows(args.lines)
        .map((line) => `${formatQuantity(line.quantity, line.unit)} ${ingredientName(run, text(line.ingredient_id))}`)
        .join(", ");
      const delivery = text(args.requested_delivery_at);
      return `Order ${lines}${delivery ? ` for delivery ${formatDay(delivery)} by ${formatClock(delivery)}` : ""} (simulated supplier)`;
    }
    case "create_stock_count": {
      const items = asRows(args.items)
        .map((item) => `${ingredientName(run, text(item.ingredient_id))} ${text(item.counted_quantity)}`)
        .join(", ");
      return `Record a ${text(args.status) || "draft"} stock count: ${items}`;
    }
    case "verify_stock_count":
      return "Verify the stock count so it becomes the new starting point";
    case "record_inventory_movement":
      return `Record ${text(args.movement_type).toLowerCase()} of ${formatQuantity(args.quantity_delta, "")} ${ingredientName(
        run,
        text(args.ingredient_id),
      )}`.replace(/\s+/g, " ");
    default:
      return step.reason;
  }
}

/** Facts from earlier steps that help the owner decide. */
export function evidenceFor(step: RunStep, run: AgentRun): string[] {
  const args = (step.resolved_args ?? step.args) as Row;
  const facts: string[] = [];

  if (step.action === "schedule_shift") {
    const coverage = run.nodes.find((node) => node.action === "coverage_report" && node.status === "done");
    const hours = asRows((coverage?.result as Row | undefined)?.hours);
    if (hours.length > 0) {
      const short = hours.filter((hour) => hour.coverage_status === "understaffed");
      const required = text(hours[0].required_staff);
      if (short.length === 0) {
        facts.push(`Coverage already meets ${required} people every hour checked.`);
      } else {
        const fewest = Math.min(...short.map((hour) => Number(hour.scheduled_employees)));
        facts.push(
          `${short.length} of ${hours.length} hours are short: as few as ${fewest} of ${required} people scheduled.`,
        );
      }
    }
    const employeeId = text(args.employee_id);
    const name = employeeName(run, employeeId);
    const start = Date.parse(text(args.start_at));
    const end = Date.parse(text(args.end_at));
    const shiftsChecked = run.nodes.some((node) => node.action === "list_shifts" && node.status === "done");
    if (shiftsChecked && !Number.isNaN(start) && !Number.isNaN(end)) {
      const clash = resultsOf(run, "list_shifts").find(
        (shift) =>
          shift.employee_id === employeeId &&
          shift.status === "scheduled" &&
          Date.parse(text(shift.start_at)) < end &&
          Date.parse(text(shift.end_at)) > start,
      );
      facts.push(
        clash
          ? `${name} already works ${timeRange(clash.start_at, clash.end_at)}, so this would overlap.`
          : `${name} has no other shift at that time.`,
      );
    }
  }

  if (step.action === "propose_supplier_order") {
    const demand = run.nodes.find((node) => node.action === "project_recipe_usage" && node.status === "done");
    const needed = asRows((demand?.result as Row | undefined)?.ingredients);
    const stock = resultsOf(run, "get_stock_estimates");
    for (const line of asRows(args.lines)) {
      const id = text(line.ingredient_id);
      const have = stock.find((row) => row.ingredient_id === id);
      const need = needed.find((row) => row.ingredient_id === id);
      if (have || need) {
        const haveText =
          have && have.estimated_quantity !== null
            ? formatQuantity(have.estimated_quantity, have.base_unit)
            : "unknown";
        const needText = need ? formatQuantity(need.quantity, need.unit) : "not projected";
        facts.push(`${ingredientName(run, id)}: about ${haveText} on hand, ${needText} needed for the event.`);
      }
    }
  }
  return facts;
}
