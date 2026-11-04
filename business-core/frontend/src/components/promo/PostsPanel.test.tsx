import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DetectedEvent } from "../../api/agent";
import type { PromoDraft } from "../../api/promo";
import { PostsPanel, isUpcoming } from "./PostsPanel";

const AS_OF = "2026-11-05T18:00:00-08:00";
const event = (id: string, title: string, starts: string) =>
  ({ event_id: id, title, starts_at: starts, ends_at: starts, knowledge_as_of: AS_OF }) as unknown as DetectedEvent;
const MARKET = event("EVT_MARKET", "Santa Clara Farmers Market", "2026-11-08T08:00:00-08:00");
const FAIR = event("EVT_FAIR", "Holiday Lights & Craft Fair", "2026-12-05T10:00:00-08:00");
const HALLOWEEN = event("EVT_HALLOWEEN", "Halloween", "2026-10-31T14:00:00-07:00");

const draft = (id: string, over: Partial<PromoDraft> = {}): PromoDraft => ({
  id, status: "draft", label: "AI-generated draft", sample: false, event_id: "EVT_MARKET",
  event_title: "Santa Clara Farmers Market", offer_text: "10% off a pastry with any coffee", created_at: "2026-11-03T19:00:00Z",
  caption: `Caption ${id}: Sunday, November 8, 8 AM to 2 PM.`, hashtags: ["#SantaClara", "#FarmersMarket"],
  caption_source: "muse", caption_note: null, image_source: "muse-image", image_note: null,
  model: { caption: "muse-spark-1.3", image: "muse-image-1.0" }, latency_ms: { caption: 25500, image: 9800 },
  image_url: `/api/v1/agent/promo-posts/${id}/image`, ...over,
});
const SAMPLE = draft("SAMPLE-HALLOWEEN", { sample: true, label: "AI-generated draft (demo)", event_title: "Halloween" });

function stub(variants: PromoDraft[] | { status: number; detail: string } = [draft("PROMO-A"), draft("PROMO-B")]) {
  let listed: PromoDraft[] = [SAMPLE];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
    if (url === "/api/v1/agent/promo-posts" && init?.method === "POST") {
      if (!Array.isArray(variants)) return json({ detail: variants.detail }, variants.status);
      const body = JSON.parse(String(init.body));
      listed = [...variants, SAMPLE];
      return json({ event_id: body.event_id, offer_text: body.offer_text, suggested_offer: "10% off a pastry with any coffee",
        posted: false, variants });
    }
    if (url === "/api/v1/agent/promo-posts") return json(listed);
    return json({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Posts", () => {
  it("only upcoming events can be promoted; past ones stay out of the picker", async () => {
    expect(isUpcoming(MARKET)).toBe(true);
    expect(isUpcoming(HALLOWEEN)).toBe(false);
    stub();
    render(<PostsPanel hidden={false} events={[MARKET, FAIR, HALLOWEEN]} request={null} />);
    const options = within(screen.getByRole("combobox")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Santa Clara Farmers Market · Sun, Nov 8", "Holiday Lights & Craft Fair · Sat, Dec 5"]);
  });

  it("starts with the suggested offer, limited to 80 characters", async () => {
    stub();
    render(<PostsPanel hidden={false} events={[MARKET]} request={null} />);
    const offer = screen.getByRole("textbox", { name: "Your offer" }) as HTMLInputElement;
    expect(offer.value).toBe("10% off a pastry with any coffee");
    expect(offer.maxLength).toBe(80);
    await userEvent.clear(offer);
    await userEvent.type(offer, "Free cookie");
    expect(screen.getByText(/11\/80/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Use “10% off a pastry with any coffee”/ }));
    expect(offer.value).toBe("10% off a pastry with any coffee");
  });

  it("makes two drafts, previews one as 'Draft – not posted' with an editable caption", async () => {
    const fetchMock = stub();
    render(<PostsPanel hidden={false} events={[MARKET]} request={{ eventId: "EVT_MARKET", nonce: 1 }} />);
    await userEvent.click(screen.getByRole("button", { name: /Make 2 drafts/ }));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ event_id: "EVT_MARKET", offer_text: "10% off a pastry with any coffee" });

    const thumbs = within(await screen.findByRole("group", { name: "Drafts to choose from" })).getAllByRole("button");
    expect(thumbs.map((t) => t.textContent)).toEqual(["Draft 1", "Draft 2"]);
    expect(thumbs[0].getAttribute("aria-pressed")).toBe("true");
    const preview = screen.getByRole("article", { name: "Draft for Santa Clara Farmers Market" });
    expect(within(preview).getByText("Draft – not posted")).toBeTruthy();
    const caption = within(preview).getByRole("textbox") as HTMLTextAreaElement;
    expect(caption.value).toBe("Caption PROMO-A: Sunday, November 8, 8 AM to 2 PM.\n\n#SantaClara #FarmersMarket");

    await userEvent.click(thumbs[1]);
    expect((within(screen.getByRole("article")).getByRole("textbox") as HTMLTextAreaElement).value).toMatch(/^Caption PROMO-B/);
    // The new drafts join the gallery, next to the samples.
    const gallery = within(screen.getByRole("region", { name: "Drafts" }));
    expect(gallery.getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Santa Clara Farmers Market, AI-generated draft", "Santa Clara Farmers Market, AI-generated draft",
      "Halloween, AI-generated draft (demo)"]);
  });

  it("downloads the image, copies the edited caption, and shares only through the device", async () => {
    stub();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<PostsPanel hidden={false} events={[MARKET]} request={null} />);
    await userEvent.click(screen.getByRole("button", { name: /Make 2 drafts/ }));
    const preview = within(await screen.findByRole("article"));

    const download = preview.getByRole("link", { name: "Download image" });
    expect(download.getAttribute("href")).toBe("/api/v1/agent/promo-posts/PROMO-A/image");
    expect(download.getAttribute("download")).toMatch(/^santa-clara-farmers-market-promo-a\.jpg$/);

    const caption = preview.getByRole("textbox");
    await userEvent.clear(caption);
    await userEvent.type(caption, "Our own words");
    await userEvent.click(preview.getByRole("button", { name: "Copy caption" }));
    expect(writeText).toHaveBeenLastCalledWith("Our own words");
    expect(preview.getByRole("status").textContent).toBe("Caption copied.");

    await userEvent.click(preview.getByRole("button", { name: "Share…" }));  // no navigator.share in this browser
    expect(preview.getByRole("status").textContent).toMatch(/Sharing isn't available.*post it yourself/);
  });

  it("says when code wrote the caption or drew the background", async () => {
    stub([draft("PROMO-C", { caption_source: "code", caption_note: "Muse's caption was replaced: number 25 isn't in the event",
      image_source: "code-drawn", model: { caption: "muse-spark-1.3", image: null } })]);
    render(<PostsPanel hidden={false} events={[MARKET]} request={null} />);
    await userEvent.click(screen.getByRole("button", { name: /Make 2 drafts/ }));
    const preview = within(await screen.findByRole("article"));
    expect(preview.getByText(/Caption written by code\. Muse's caption was replaced: number 25/)).toBeTruthy();
    expect(preview.getByText(/Background drawn by code/)).toBeTruthy();
    expect(preview.getByText(/Caption by code · Background by code/)).toBeTruthy();
  });

  it("shows the gallery with the demo samples, and errors from the backend", async () => {
    stub({ status: 409, detail: "This event has already happened; only upcoming events can be promoted." });
    render(<PostsPanel hidden={false} events={[MARKET]} request={null} />);
    const gallery = within(screen.getByRole("region", { name: "Drafts" }));
    expect(await gallery.findByText("AI-generated draft (demo)")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/published|\bposted on\b/i);
    await userEvent.click(screen.getByRole("button", { name: /Make 2 drafts/ }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/already happened/);
  });

  it("says so when nothing is coming up", () => {
    stub();
    render(<PostsPanel hidden={false} events={[HALLOWEEN]} request={null} />);
    expect(screen.getByText("No upcoming events to promote right now.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Make 2 drafts/ })).toBeNull();
  });
});
