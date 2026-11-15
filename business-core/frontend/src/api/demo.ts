import { AgentApiError } from "./agent";

/** The public demo: access code, today's Muse allowance and "Start over". */
export type AccessStatus = {
  required: boolean;
  granted: boolean;
  muse_calls_left: number | null;
  /** Which models run the café: "meta" (Muse) or "openai". Missing on older APIs: Muse. */
  model_provider?: "meta" | "openai";
};

/** Sent when any API call comes back locked (the access cookie expired or the code changed). */
export const LOCKED_EVENT = "cafepilot:locked";

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1/demo${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
    });
  } catch {
    throw new AgentApiError("CaféPilot did not respond. Try again in a moment.");
  }
  if (!response.ok) {
    let detail = "Something went wrong. Try again.";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new AgentApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const getAccess = () => call<AccessStatus>("/access");
export const enterAccessCode = (code: string) => call<AccessStatus>("/access", { method: "POST", body: JSON.stringify({ code }) });
export const startOver = () => call<{ reset: boolean; message: string }>("/reset", { method: "POST" });

/** Watch every fetch: a locked answer from the API brings the access page back. */
export function watchForLockedApi(target: typeof globalThis = globalThis) {
  const original = target.fetch.bind(target);
  target.fetch = async (...args: Parameters<typeof fetch>) => {
    const response = await original(...args);
    if (response.status === 401 && response.headers.get("X-CafePilot-Access") === "required") {
      target.dispatchEvent(new Event(LOCKED_EVENT));
    }
    return response;
  };
}
