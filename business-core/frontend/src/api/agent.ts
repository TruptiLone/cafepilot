export const ACTIVE_CAFE_ID = "CAFE001";

export type StepStatus =
  | "pending"
  | "awaiting_approval"
  | "approved"
  | "running"
  | "done"
  | "failed"
  | "rejected"
  | "skipped";

export type RunStatus = "running" | "awaiting_approval" | "completed" | "completed_with_issues";

export type RunStep = {
  node_id: string;
  action: string;
  side_effect: "read" | "write";
  requires_approval: boolean;
  reason: string;
  only_if: string | null;
  args: Record<string, unknown>;
  resolved_args: Record<string, unknown> | null;
  depends_on: string[];
  level: number;
  status: StepStatus;
  result: unknown;
  error: string | null;
  replay_of: string | null;
  started_at: string | null;
  finished_at: string | null;
  summary?: string | null;
  acknowledgement?: Acknowledgement | null;
  lifecycle?: LifecycleStage[] | null;
  external_status?: string | null;
  replanned_in?: string | null;
};

export type Acknowledgement = {
  headline: string;
  details: string[];
  caveat: string | null;
  tone?: "done" | "waiting" | "problem" | "declined";
  next_step?: "find_alternative" | "find_cover" | null;
};

export type LifecycleStage = {
  stage: string;
  state: "done" | "current" | "failed" | "pending";
  at: string | null;
  note: string | null;
};

export type RunEdge = { source: string; target: string; kind: "data" | "safety" };

export type AgentRun = {
  run_id: string;
  cafe_id: string;
  query: string;
  goal: string;
  assumptions: string[];
  status: RunStatus;
  as_of: string;
  model: string;
  attempts: number;
  edges: RunEdge[];
  levels: string[][];
  warnings: string[];
  created_at: string;
  updated_at: string;
  nodes: RunStep[];
  closing_message?: string | null;
  waiting_on?: string[];
  parent_run_id?: string | null;
  parent_node_id?: string | null;
};

export type RunSummary = {
  run_id: string;
  goal: string;
  status: RunStatus;
  created_at: string;
  steps: number;
  awaiting_approval: number;
};

export type PlanIssue = { code: string; message: string; node_id: string | null };

export type Planning = {
  plan: {
    status: "plan" | "needs_clarification" | "out_of_scope";
    goal: string;
    assumptions: string[];
    question: string | null;
  };
  validation: { valid: boolean; issues: PlanIssue[]; warnings: string[] };
  attempts: number;
  model: string;
  latency_ms: number;
};

export type StartRunResponse = { planning: Planning; run: AgentRun | null };

export class AgentApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "AgentApiError";
    this.status = status;
  }
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...options.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new AgentApiError("CaféPilot did not respond. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = response.statusText || "The request could not be completed.";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new AgentApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function startRun(query: string, asOf?: string): Promise<StartRunResponse> {
  return requestJson<StartRunResponse>(
    "/api/v1/agent/runs",
    json({ query, cafe_id: ACTIVE_CAFE_ID, ...(asOf ? { as_of: asOf } : {}) }),
  );
}

export function getRun(runId: string, signal?: AbortSignal): Promise<AgentRun> {
  return requestJson<AgentRun>(`/api/v1/agent/runs/${encodeURIComponent(runId)}`, { signal });
}

export function listRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  return requestJson<RunSummary[]>(`/api/v1/agent/runs?cafe_id=${ACTIVE_CAFE_ID}&limit=10`, {
    signal,
  });
}

export function decideStep(
  runId: string,
  nodeId: string,
  decision: "approved" | "rejected",
  editedArgs?: Record<string, unknown>,
): Promise<AgentRun> {
  return requestJson<AgentRun>(
    `/api/v1/agent/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/decision`,
    json({ decision, channel: "web", ...(editedArgs ? { edited_args: editedArgs } : {}) }),
  );
}

export function decideAll(runId: string, decision: "approved" | "rejected"): Promise<AgentRun> {
  return requestJson<AgentRun>(
    `/api/v1/agent/runs/${encodeURIComponent(runId)}/decision`,
    json({ decision, channel: "web" }),
  );
}

export type DetectedEvent = {
  event_id: string;
  title: string;
  category: string;
  starts_at: string;
  ends_at: string;
  location: string | null;
  distance_m: number | null;
  source: string;
  is_demo: boolean;
  muse_message: string;
  run_id?: string | null;
  latest_run_id?: string | null;
  plan_state?: PlanState;
  expected: {
    confidence: "good" | "rough" | "none";
    examples: number;
    normal_drinks: number;
    extra_drinks_low: number;
    extra_drinks_typical: number;
    extra_drinks_high: number;
    extra_food_typical: number;
    basis: string;
  };
};

export type PlanState = "new" | "checking" | "needs_ok" | "waiting" | "planned" | "issues";

export type Kpi = {
  label: string;
  value: string;
  of: string | null;
  note: string;
  tone: "neutral" | "good" | "warn" | "bad";
};

export type InventoryRow = {
  ingredient_id: string;
  name: string;
  unit: string;
  on_hand: string | null;
  arriving: string;
  on_order: string;
  on_order_status: string | null;
  needed: string;
  shortfall: string;
  status: "covered" | "short" | "ordered" | "unknown";
  note: string;
};

export type CoverageDay = {
  date: string;
  label: string;
  shifts: number;
  is_event_day: boolean;
  halves: { label: "AM" | "PM"; staffed: number; required: number }[];
};

export type EventStatus = {
  event_id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  note: string;
  plan_state: PlanState;
  run_id: string | null;
  latest_run_id: string | null;
};

export type CafeStatus = {
  cafe_id: string;
  cafe_name: string;
  as_of: string;
  greeting: string;
  date_label: string;
  focus_event_id: string | null;
  focus_event_name: string | null;
  kpis: Partial<Record<"stock" | "coverage" | "approvals" | "sales", Kpi>>;
  inventory: InventoryRow[];
  coverage: CoverageDay[];
  events: EventStatus[];
};

export function getCafeStatus(signal?: AbortSignal): Promise<CafeStatus> {
  return requestJson<CafeStatus>(`/api/v1/agent/status?cafe_id=${ACTIVE_CAFE_ID}`, { signal });
}

export function listEvents(signal?: AbortSignal): Promise<DetectedEvent[]> {
  return requestJson<DetectedEvent[]>(`/api/v1/agent/events?cafe_id=${ACTIVE_CAFE_ID}`, { signal });
}

export function startEventRun(eventId: string): Promise<StartRunResponse> {
  return requestJson<StartRunResponse>(
    `/api/v1/agent/events/${encodeURIComponent(eventId)}/runs`,
    { method: "POST", headers: { Accept: "application/json" } },
  );
}

export function replanStep(runId: string, nodeId: string): Promise<StartRunResponse> {
  return requestJson<StartRunResponse>(
    `/api/v1/agent/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/replan`,
    { method: "POST", headers: { Accept: "application/json" } },
  );
}
