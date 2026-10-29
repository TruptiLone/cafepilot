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
): Promise<AgentRun> {
  return requestJson<AgentRun>(
    `/api/v1/agent/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/decision`,
    json({ decision, channel: "web" }),
  );
}

export function decideAll(runId: string, decision: "approved" | "rejected"): Promise<AgentRun> {
  return requestJson<AgentRun>(
    `/api/v1/agent/runs/${encodeURIComponent(runId)}/decision`,
    json({ decision, channel: "web" }),
  );
}
