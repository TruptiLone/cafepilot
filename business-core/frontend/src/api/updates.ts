import { ACTIVE_CAFE_ID } from "./agent";

/** A status change read from the order, shift-offer, run or AI-call records (nothing invented). */
export type CafeUpdate = {
  id: string;
  kind: string;
  group: "staff" | "orders" | "plans";
  at: string;
  title: string;
  detail: string | null;
  /** The reply came from the demo policy, not a real supplier or employee. */
  simulated: boolean;
  run_id: string | null;
  model: string | null;
};

export type AICallRow = {
  id: string;
  created_at: string;
  purpose: "plan" | "replan" | "chat" | "other";
  model: string;
  latency_ms: number;
  attempts: number;
  outcome: "ok" | "retried" | "failed" | "timeout";
  run_id: string | null;
  request_summary: string;
  error: string | null;
};

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json() as Promise<T>;
}

/** Newest first; with `since`, only changes after it. */
export function getUpdates(since?: string, signal?: AbortSignal): Promise<CafeUpdate[]> {
  const query = new URLSearchParams({ cafe_id: ACTIVE_CAFE_ID, ...(since ? { since } : {}) });
  return getJson<CafeUpdate[]>(`/api/v1/agent/updates?${query}`, signal);
}

export function getAICalls(limit = 20): Promise<AICallRow[]> {
  return getJson<AICallRow[]>(`/api/v1/agent/ai-calls?cafe_id=${ACTIVE_CAFE_ID}&limit=${limit}`);
}
