import { requestJson } from "./agent";

/** One post draft. Drafts are never posted anywhere: the owner downloads, copies or shares them. */
export type PromoDraft = {
  id: string;
  status: "draft";
  label: string; // "AI-generated draft" or "AI-generated draft (demo)"
  sample: boolean;
  event_id: string;
  event_title: string;
  offer_text: string;
  created_at: string;
  caption: string;
  hashtags: string[];
  caption_source: "muse" | "code";
  caption_note: string | null;
  image_source: "muse-image" | "code-drawn";
  image_note: string | null;
  model: { caption: string | null; image: string | null };
  latency_ms: { caption: number; image: number; image_attempts?: number };
  image_url: string;
};

export type PromoResult = {
  event_id: string;
  offer_text: string;
  suggested_offer: string;
  posted: false;
  variants: PromoDraft[];
};

export const OFFER_MAX = 80;
export const SUGGESTED_OFFER = "10% off a pastry with any coffee";

export function listPromoPosts(signal?: AbortSignal): Promise<PromoDraft[]> {
  return requestJson<PromoDraft[]>("/api/v1/agent/promo-posts", { signal });
}

export function createPromoPosts(eventId: string, offerText: string): Promise<PromoResult> {
  return requestJson<PromoResult>("/api/v1/agent/promo-posts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_id: eventId, offer_text: offerText }),
  });
}

/** Caption and hashtags as the owner would paste them. */
export function postText(caption: string, hashtags: string[]): string {
  return hashtags.length ? `${caption.trim()}\n\n${hashtags.join(" ")}` : caption.trim();
}
