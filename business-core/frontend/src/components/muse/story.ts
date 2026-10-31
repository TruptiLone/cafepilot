/**
 * Owner view of a plan: Muse's technical steps grouped into a few business boxes
 * (forecast, ingredients, suppliers, staff, decisions). Edges come from the steps'
 * real dependencies; every number comes from what the steps returned.
 */
import type { AgentRun, RunStep } from "../../api/agent";
import { describeChange, employeeName, formatClock, formatDay } from "../agent/changes";
import { policyRoles } from "./roles";

type Row = Record<string, unknown>;

export type BoxKind = "event" | "demand" | "ingredients" | "suppliers" | "staff" | "sales" | "other" | "decision" | "share";
export type BoxState = "pending" | "running" | "done" | "wait" | "declined" | "failed" | "skipped" | "after";

export type DetailRow = { label: string; value: string; tone?: "bad" | "good" | "muted" };
export type Detail = { title: string; intro?: string; rows: DetailRow[]; steps: string[] };

export type Box = {
  id: string;
  kind: BoxKind;
  title: string;
  subtitle: string;
  state: BoxState;
  steps: RunStep[];
  detail: Detail | null;
  level: number;
  lane: number;
  runId: string;
};

export type Edge = { from: string; to: string; dashed?: boolean; label?: string };
export type Story = { boxes: Box[]; edges: Edge[]; lanes: number; levels: number; summary: string[] };

const GROUP_OF: Record<string, BoxKind> = {
  estimate_event_demand: "demand",
  get_stock_estimates: "ingredients",
  calculate_shortfall: "ingredients",
  project_recipe_usage: "ingredients",
  list_ingredients: "ingredients",
  list_stock_counts: "ingredients",
  get_stock_count: "ingredients",
  list_inventory_movements: "ingredients",
  find_suppliers: "suppliers",
  get_purchase_order: "suppliers",
  list_employees: "staff",
  list_shifts: "staff",
  coverage_report: "staff",
  sales_summary: "sales",
  list_sales: "sales",
  list_products: "sales",
};

const KIND_ORDER: BoxKind[] = ["event", "demand", "ingredients", "suppliers", "sales", "other", "staff", "decision", "share"];

const resultOf = (steps: RunStep[], action: string): unknown =>
  steps.find((s) => s.action === action && s.status === "done")?.result;

function asRows(value: unknown): Row[] {
  return Array.isArray(value) ? (value.filter((v) => v && typeof v === "object") as Row[]) : [];
}

function litres(value: unknown, unit: unknown): string {
  const n = Number(value);
  if (Number.isNaN(n)) return String(value ?? "");
  if (unit === "mL") return `${(n / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} L`;
  if (unit === "g") return `${(n / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} kg`;
  return `${n.toLocaleString("en-US", { maximumFractionDigits: 1 })} ${unit ?? ""}`.trim();
}

function list(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} & ${names[names.length - 1]}`;
}

function groupState(steps: RunStep[]): BoxState {
  if (steps.some((s) => s.status === "running" || s.status === "approved")) return "running";
  if (steps.some((s) => s.status === "failed")) return "failed";
  if (steps.every((s) => s.status === "done")) return "done";
  if (steps.every((s) => s.status === "skipped")) return "skipped";
  return "pending";
}

function decisionState(step: RunStep): BoxState {
  switch (step.status) {
    case "awaiting_approval":
      return "wait";
    case "rejected":
      return "declined";
    case "failed":
      return "failed";
    case "skipped":
      return "skipped";
    case "running":
    case "approved":
      return "running";
    case "done":
      if (step.acknowledgement?.tone === "problem") return "failed";
      if (step.acknowledgement?.tone === "waiting") return "running";
      return "done";
    default:
      return "pending";
  }
}

// ---------------------------------------------------------------------------
// Box contents
// ---------------------------------------------------------------------------

function demandBox(steps: RunStep[], run: AgentRun): Pick<Box, "title" | "subtitle" | "detail"> {
  const r = resultOf(steps, "estimate_event_demand") as Row | undefined;
  const drinks = (r?.drinks ?? {}) as Row;
  const food = (r?.food ?? {}) as Row;
  const examples = asRows(r?.examples).length;
  const day = run.event
    ? new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "America/Los_Angeles" }).format(new Date(run.event.starts_at))
    : "day";
  const extra = Number(drinks.extra_typical ?? 0);
  const title = extra > 0 ? `Busier ${day}` : extra < 0 ? `Quieter ${day}` : `Normal ${day}`;
  const subtitle = r
    ? `${extra >= 0 ? "~" : ""}${Math.abs(extra)} ${extra >= 0 ? "extra" : "fewer"} drinks${Number(food.extra_typical) > 0 ? `, ${food.extra_typical} pastries` : ""} (last ${examples} ${examples === 1 ? "event" : "events"})`
    : "Learning from past events…";
  return {
    title,
    subtitle,
    detail: r
      ? {
          title: "Forecast from past events",
          intro: `Compared ${examples} similar past ${examples === 1 ? "event" : "events"} with normal days at the same hours.`,
          rows: [
            { label: "Normal day", value: `${drinks.normal ?? "?"} drinks` },
            { label: "Expected extra", value: `+${drinks.extra_typical} (from ${drinks.extra_low} to ${drinks.extra_high})` },
            { label: "Extra pastries", value: `${food.extra_typical ?? 0}` },
            { label: "Confidence", value: String(r.confidence ?? "") },
          ],
          steps: steps.map((s) => s.action),
        }
      : null,
  };
}

function ingredientsBox(steps: RunStep[]): Pick<Box, "title" | "subtitle" | "detail"> {
  const shortfall = resultOf(steps, "calculate_shortfall") as Row | undefined;
  const lines = asRows(shortfall?.lines);
  const short = lines.filter((l) => l.status === "short");
  const unknown = lines.filter((l) => l.status === "unknown_stock");
  let subtitle = "Checking stock…";
  if (shortfall) {
    subtitle = short.length
      ? `Short on ${list(short.map((l) => String(l.name).toLowerCase()))}`
      : unknown.length
        ? `Stock unknown for ${list(unknown.map((l) => String(l.name).toLowerCase()))}`
        : "Everything is covered";
  } else if (resultOf(steps, "get_stock_estimates")) {
    subtitle = "Stock checked";
  }
  const rows: DetailRow[] = [...lines]
    .sort((a, b) => Number(b.status === "short") - Number(a.status === "short"))
    .slice(0, 6)
    .map((l) => ({
      label: String(l.name),
      value:
        l.status === "short"
          ? `need ${litres(Number(l.needed_with_buffer) + Number(l.normal_use ?? 0), l.unit)} · short ${litres(l.shortfall, l.unit)}`
          : l.status === "unknown_stock"
            ? "no recent count"
            : `need ${litres(Number(l.needed_with_buffer) + Number(l.normal_use ?? 0), l.unit)} · ok`,
      tone: l.status === "short" ? "bad" : l.status === "unknown_stock" ? "muted" : "good",
    }));
  return {
    title: "Ingredients",
    subtitle,
    detail: {
      title: "Ingredients",
      intro: shortfall?.include_normal_use
        ? "Forecast + latest count, after normal sales until then, regular deliveries and the café's safety buffer. Worked out in code."
        : "Need from the recipes, minus stock on hand. Worked out in code.",
      rows,
      steps: steps.map((s) => s.action),
    },
  };
}

function suppliersBox(steps: RunStep[]): Pick<Box, "title" | "subtitle" | "detail"> {
  const found = resultOf(steps, "find_suppliers") as Row | undefined;
  const po = resultOf(steps, "get_purchase_order") as Row | undefined;
  const options = asRows(found?.suppliers).filter((o) => o.carries_all);
  if (po && !found) {
    return {
      title: "Rejected order",
      subtitle: `${po.supplier_name}: ${po.response_reason ?? po.status}`,
      detail: {
        title: "What the supplier said",
        rows: [
          { label: String(po.supplier_name), value: String(po.response_reason ?? po.status), tone: "bad" },
          ...asRows(po.lines).map((l) => ({ label: String(l.ingredient_id).replaceAll("_", " ").toLowerCase(), value: `${l.packs ?? ""} ${l.pack_name ?? ""}`.trim() })),
        ],
        steps: steps.map((s) => s.action),
      },
    };
  }
  return {
    title: "Suppliers",
    subtitle: options.length
      ? options.map((o) => `${o.name}${o.usual ? " (usual)" : ""}`.replace(" Dairy Supply", "").replace(" Co.", "")).join(" · ")
      : found
        ? "Nobody carries everything"
        : "Checking suppliers…",
    detail: found
      ? {
          title: "Who carries it",
          intro: String(found.note ?? ""),
          rows: asRows(found.suppliers).map((o) => ({
            label: `${o.name}${o.usual ? " (usual)" : ""}`,
            value: o.carries_all
              ? `$${(Number(o.total_cents) / 100).toFixed(2)} · ${o.can_deliver_in_time ? `delivers from ${o.earliest_delivery_that_day}` : "too late"}`
              : "doesn't carry everything",
            tone: o.supplier_id === found.recommended_supplier_id ? "good" : o.carries_all ? undefined : "muted",
          })),
          steps: steps.map((s) => s.action),
        }
      : null,
  };
}

function staffBox(steps: RunStep[], run: AgentRun): Pick<Box, "title" | "subtitle" | "detail"> {
  const coverage = resultOf(steps, "coverage_report") as Row | undefined;
  const hours = asRows(coverage?.hours);
  const short = hours.filter((h) => Number(h.scheduled_employees) < Number(h.required_staff));
  const shifts = asRows(resultOf(steps, "list_shifts"));
  const onShift = new Set(shifts.map((s) => s.employee_id));
  const roles = policyRoles(run);
  const free = asRows(resultOf(steps, "list_employees"))
    .filter((e) => e.active !== false && !onShift.has(e.employee_id) && (roles.length === 0 || roles.includes(String(e.role))))
    .map((e) => String(e.name).split(" ")[0]);
  let subtitle = "Checking the roster…";
  if (coverage) {
    if (short.length) {
      const fewest = Math.min(...short.map((h) => Number(h.scheduled_employees)));
      const first = String(short[0].hour_start);
      subtitle = `${formatDay(first).split(",")[0]} has ${fewest} of ${coverage.required_staff}`;
    } else subtitle = `Fully staffed (${coverage.required_staff} people)`;
    if (free.length) subtitle += ` · ${list(free.slice(0, 3))} free`;
  }
  const rows: DetailRow[] = hours.map((h) => ({
    label: formatClock(String(h.hour_start)),
    value: `${h.scheduled_employees} of ${h.required_staff}`,
    tone: Number(h.scheduled_employees) < Number(h.required_staff) ? "bad" : "good",
  }));
  if (free.length) rows.push({ label: "Free that day", value: free.join(", ") });
  return {
    title: "Staff",
    subtitle,
    detail: { title: "Staff for the event hours", rows, steps: steps.map((s) => s.action) },
  };
}

/** What the change does: the code-written summary names the supplier while it waits for the owner. */
function changeLine(step: RunStep, run: AgentRun): string {
  if (step.status === "awaiting_approval" && step.summary) return step.summary;
  return describeChange(step, run);
}

/** Muse's line once the owner has decided, counting any replan that took over. */
export function storyClosing(run: AgentRun, children: Record<string, AgentRun>): string | null {
  const replaced = run.nodes.filter((n) => n.replanned_in && children[n.replanned_in]);
  if (!replaced.length) return run.closing_message ?? null;
  const leaves: RunStep[] = [
    ...run.nodes.filter((n) => n.side_effect === "write" && !(n.replanned_in && children[n.replanned_in])),
    ...replaced.flatMap((n) => children[n.replanned_in as string].nodes.filter((c) => c.side_effect === "write")),
  ];
  if (leaves.some((n) => n.status === "awaiting_approval")) return null;
  const lines = leaves.map((n) => n.acknowledgement?.headline).filter(Boolean);
  if (!lines.length) return null;
  const waiting = leaves.some((n) => n.acknowledgement?.tone === "waiting");
  return `Here's where things stand: ${lines.join("; ")}.${waiting ? " Waiting on replies." : ""}`;
}

function decisionTitle(step: RunStep, run: AgentRun): string {
  const args = (step.resolved_args ?? step.args) as Row;
  if (step.action === "propose_supplier_order") {
    const lines = Array.isArray(args.lines) ? (args.lines as Row[]) : [];
    const allMilk = lines.length > 0 && lines.every((l) => String(l.ingredient_id).includes("MILK"));
    return allMilk ? "Order milk" : "Order supplies";
  }
  if (step.action === "schedule_shift") return `Add ${employeeName(run, String(args.employee_id)).split(" ")[0]}`;
  if (step.action === "cancel_shift") return "Cancel a shift";
  if (step.action === "record_inventory_movement") return "Record stock change";
  return describeChange(step, run);
}

// ---------------------------------------------------------------------------
// Graph
// ---------------------------------------------------------------------------

export function buildStory(run: AgentRun, children: Record<string, AgentRun> = {}, replanning: string | null = null): Story {
  const groups = new Map<BoxKind, RunStep[]>();
  const decisions: RunStep[] = [];
  for (const step of run.nodes) {
    if (step.side_effect === "write") decisions.push(step);
    else {
      const kind = GROUP_OF[step.action] ?? "other";
      groups.set(kind, [...(groups.get(kind) ?? []), step]);
    }
  }
  const boxOf = new Map<string, string>(); // node_id -> box id
  const boxes: Box[] = [];
  const root: Box = {
    id: "event",
    kind: "event",
    title: run.event?.title ?? run.goal,
    subtitle: run.event
      ? `${formatDay(run.event.starts_at)} · ${formatClock(run.event.starts_at)}–${formatClock(run.event.ends_at)}${run.event.distance_m ? ` · ${(run.event.distance_m / 1609).toFixed(1)} miles away` : ""}`
      : run.query.length > 90 ? `${run.query.slice(0, 90)}…` : run.query,
    state: "done",
    steps: [],
    detail: null,
    level: 0,
    lane: 0,
    runId: run.run_id,
  };
  boxes.push(root);

  for (const kind of KIND_ORDER) {
    const steps = groups.get(kind);
    if (!steps) continue;
    const content =
      kind === "demand"
        ? demandBox(steps, run)
        : kind === "ingredients"
          ? ingredientsBox(steps)
          : kind === "suppliers"
            ? suppliersBox(steps)
            : kind === "staff"
              ? staffBox(steps, run)
              : {
                  title: kind === "sales" ? "Sales" : "Other checks",
                  subtitle: steps.map((s) => s.summary).filter(Boolean)[0] ?? "",
                  detail: null,
                };
    const box: Box = { id: kind, kind, state: groupState(steps), steps, level: 0, lane: 0, runId: run.run_id, ...content };
    if (!box.detail && steps.some((s) => s.summary))
      box.detail = { title: box.title, rows: steps.map((s) => ({ label: s.action.replaceAll("_", " "), value: s.summary ?? "" })), steps: steps.map((s) => s.action) };
    boxes.push(box);
    steps.forEach((s) => boxOf.set(s.node_id, kind));
  }
  for (const step of decisions) {
    const id = `d:${step.node_id}`;
    boxes.push({
      id,
      kind: "decision",
      title: decisionTitle(step, run),
      subtitle: changeLine(step, run),
      state: decisionState(step),
      steps: [step],
      detail: null,
      level: 0,
      lane: 0,
      runId: run.run_id,
    });
    boxOf.set(step.node_id, id);
  }

  // Edges between boxes from the real step dependencies.
  const edgeKey = new Set<string>();
  const edges: Edge[] = [];
  const add = (from: string, to: string, dashed = false) => {
    const key = `${from}>${to}`;
    if (from === to || edgeKey.has(key)) return;
    edgeKey.add(key);
    edges.push({ from, to, dashed });
  };
  for (const step of run.nodes) {
    const to = boxOf.get(step.node_id);
    if (!to) continue;
    for (const dep of step.depends_on) {
      const from = boxOf.get(dep);
      if (from) add(from, to);
    }
  }
  for (const box of boxes) {
    if (box.id !== "event" && !edges.some((e) => e.to === box.id)) add("event", box.id);
  }
  // Decisions reading data that a group produced without an explicit $ref still depend on it (safety edges).
  for (const e of run.edges) {
    const from = boxOf.get(e.source);
    const to = boxOf.get(e.target);
    if (from && to) add(from, to);
  }

  // Levels: longest path from the event; decisions share the bottom row.
  const level = new Map<string, number>([["event", 0]]);
  const order = boxes.map((b) => b.id);
  for (let pass = 0; pass < boxes.length; pass++) {
    for (const e of edges) {
      const l = (level.get(e.from) ?? 0) + 1;
      if (l > (level.get(e.to) ?? 0)) level.set(e.to, l);
    }
  }
  const nonDecisionMax = Math.max(0, ...boxes.filter((b) => b.kind !== "decision").map((b) => level.get(b.id) ?? 0));
  const decisionLevel = Math.max(nonDecisionMax + 1, ...boxes.filter((b) => b.kind === "decision").map((b) => level.get(b.id) ?? 0));
  for (const b of boxes) b.level = b.kind === "decision" ? decisionLevel : (level.get(b.id) ?? 0);

  // Replans grow under the change that fell through.
  let bottom = decisionLevel;
  for (const step of decisions) {
    const parentId = `d:${step.node_id}`;
    const child = step.replanned_in ? children[step.replanned_in] : undefined;
    if (child) {
      for (const write of child.nodes.filter((n) => n.side_effect === "write")) {
        const id = `c:${child.run_id}:${write.node_id}`;
        boxes.push({
          id, kind: "decision", title: decisionTitle(write, child), subtitle: changeLine(write, child),
          state: decisionState(write), steps: [write], detail: null, level: decisionLevel + 1, lane: 0, runId: child.run_id,
        });
        edges.push({ from: parentId, to: id, label: "Muse found another way" });
        bottom = decisionLevel + 1;
      }
    } else if (replanning === step.node_id || (step.replanned_in && !child)) {
      const id = `r:${step.node_id}`;
      boxes.push({
        id, kind: "decision", title: step.action === "schedule_shift" ? "Finding someone else" : "Finding another supplier",
        subtitle: "Muse is planning…", state: "running", steps: [], detail: null, level: decisionLevel + 1, lane: 0, runId: run.run_id,
      });
      edges.push({ from: parentId, to: id });
      bottom = decisionLevel + 1;
    }
  }

  // "Share it?" comes after the decisions (owner approves it like any change; not built yet).
  if (decisions.length && run.event) {
    boxes.push({
      id: "share",
      kind: "share",
      title: "Share it?",
      subtitle: "Instagram post, after you approve",
      state: "after",
      steps: [],
      detail: { title: "Coming next", intro: "Muse drafts a post about the event for your OK. Not connected yet.", rows: [], steps: [] },
      level: bottom + 1,
      lane: 0,
      runId: run.run_id,
    });
    const leaves = boxes.filter((b) => b.kind === "decision" && !edges.some((e) => e.from === b.id));
    for (const d of leaves) edges.push({ from: d.id, to: "share", dashed: true });
  }

  // Lanes: children sit under their leftmost parent; free lanes are taken left to right.
  const laneOf = new Map<string, number>();
  const byLevel = new Map<number, Box[]>();
  for (const b of boxes) byLevel.set(b.level, [...(byLevel.get(b.level) ?? []), b]);
  let lanes = 1;
  const levels = Math.max(...boxes.map((b) => b.level)) + 1;
  for (let l = 1; l < levels; l++) {
    const row = (byLevel.get(l) ?? []).sort(
      (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || order.indexOf(a.id) - order.indexOf(b.id),
    );
    const taken = new Set<number>();
    for (const b of row) {
      if (b.kind === "share") continue;
      const parents = edges.filter((e) => e.to === b.id && e.from !== "event").map((e) => laneOf.get(e.from)).filter((x): x is number => x !== undefined);
      let lane = parents.length ? Math.min(...parents) : 0;
      while (taken.has(lane)) lane += 1;
      taken.add(lane);
      laneOf.set(b.id, lane);
      lanes = Math.max(lanes, lane + 1);
    }
  }
  for (const b of boxes) {
    if (b.id.startsWith("c:") || b.id.startsWith("r:")) {
      const parent = edges.find((e) => e.to === b.id)?.from;
      b.lane = (parent ? laneOf.get(parent) : undefined) ?? 0;
      laneOf.set(b.id, b.lane);
    } else b.lane = laneOf.get(b.id) ?? 0;
  }

  // Owner-friendly label on the edge into a staff decision.
  const coverage = resultOf(groups.get("staff") ?? [], "coverage_report") as Row | undefined;
  if (coverage) {
    for (const e of edges) if (e.from === "staff" && e.to.startsWith("d:")) e.label = `event needs ${coverage.required_staff} people`;
  }

  // "In short": three plain lines.
  const summary: string[] = [];
  const ing = boxes.find((b) => b.kind === "ingredients");
  if (ing && ing.state === "done") {
    const lines = asRows((resultOf(ing.steps, "calculate_shortfall") as Row | undefined)?.lines).filter((l) => l.status === "short");
    if (lines.length)
      summary.push(
        `Short ${list(lines.map((l) => `${litres(l.shortfall, l.unit)} ${String(l.name).toLowerCase()}`))}. ${list(
          lines.map((l) => `${l.packs ?? 1} ${String(l.pack_name ?? "pack")}${Number(l.packs) > 1 ? "s" : ""}`),
        )} cover${lines.length === 1 ? "s" : ""} it.`,
      );
    else summary.push("Ingredients are covered.");
  }
  const staff = boxes.find((b) => b.kind === "staff");
  if (staff && staff.state === "done") summary.push(`${staff.subtitle.replace(" · ", ". ")}.`);
  const sup = boxes.find((b) => b.kind === "suppliers");
  if (sup && sup.state === "done" && sup.title === "Suppliers")
    summary.push(`${sup.subtitle.replaceAll(" · ", " and ")} carry it; suppliers confirm stock when we order.`);

  return { boxes, edges, lanes, levels, summary };
}
