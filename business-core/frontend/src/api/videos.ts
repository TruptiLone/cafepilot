export type FrameResult = {
  number: number;
  offset_seconds: number;
  latency_ms: number | null;
  error: string | null;
  result: {
    items: { product: string | null; label_text: string | null; visible_units: number;
      container: string | null; possibly_hidden_units: boolean; confidence: string | null }[];
    visible_damage: string | null;
    notes: string | null;
  } | null;
};

export type VideoRecord = {
  id: string;
  filename: string;
  url: string;
  captured_at: string;
  received_at: string;
  size_bytes: number;
  source: "meta-glasses" | "mock-glasses" | "test";
  processing: {
    status: "queued" | "extracting" | "processing" | "complete" | "partial" | "failed";
    model: string | null;
    duration_seconds: number | null;
    width: number | null;
    height: number | null;
    codec: string | null;
    total_frames: number;
    completed_frames: number;
    frames: FrameResult[];
    error: string | null;
    updated_at: string | null;
  };
};

export class VideoApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function videoRequest(path: string, token: string, options: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...options, headers: { ...options.headers, Authorization: `Bearer ${token}` } });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new VideoApiError(typeof body?.detail === "string" ? body.detail : `Service returned HTTP ${response.status}.`, response.status);
  }
  return response;
}

export async function listVideos(token: string, signal: AbortSignal): Promise<VideoRecord[]> {
  return (await videoRequest("/api/videos?limit=12", token, { signal })).json() as Promise<VideoRecord[]>;
}

export async function retryVideo(id: string, token: string): Promise<void> {
  await videoRequest(`/api/videos/${encodeURIComponent(id)}/process`, token, { method: "POST" });
}
