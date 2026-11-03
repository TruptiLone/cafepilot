import { useCallback, useEffect, useRef, useState } from "react";
import type { DetectedEvent } from "../../api/agent";
import {
  OFFER_MAX,
  SUGGESTED_OFFER,
  createPromoPosts,
  listPromoPosts,
  postText,
  type PromoDraft,
} from "../../api/promo";
import { modelName } from "../muse/story";

export type PostRequest = { eventId: string; nonce: number };

/** Only events still to come can be promoted (the backend answers 409 for past ones). */
export function isUpcoming(event: Pick<DetectedEvent, "starts_at" | "knowledge_as_of">): boolean {
  const now = event.knowledge_as_of ? new Date(event.knowledge_as_of).getTime() : Date.now();
  return new Date(event.starts_at).getTime() > now;
}

const seconds = (ms?: number) => (ms ? `${(ms / 1000).toFixed(1)} s` : "");

function whenLine(event: DetectedEvent): string {
  const tz = "America/Los_Angeles";
  const day = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: tz });
  return day.format(new Date(event.starts_at));
}

/** Larger preview of one draft: the caption is editable here and copied, downloaded or shared by the owner. */
export function DraftPreview({ draft }: { draft: PromoDraft }) {
  const [text, setText] = useState(() => postText(draft.caption, draft.hashtags));
  const [note, setNote] = useState<string | null>(null);
  const file = `${draft.event_title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${draft.id.toLowerCase()}.jpg`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setNote("Caption copied.");
    } catch {
      setNote("Couldn't copy here. Select the caption and copy it.");
    }
  };

  const share = async () => {
    const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
    if (!nav.share) {
      await copy();
      setNote("Sharing isn't available in this browser: caption copied. Download the image and post it yourself.");
      return;
    }
    try {
      const blob = await (await fetch(draft.image_url)).blob();
      const image = new File([blob], file, { type: "image/jpeg" });
      await nav.share(nav.canShare?.({ files: [image] }) ? { files: [image], text } : { text });
      setNote("Opened your device's share sheet. You choose where it goes.");
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) setNote("Couldn't open sharing. Download the image instead.");
    }
  };

  return (
    <article className="mz-card mz-draft" aria-label={`Draft for ${draft.event_title}`}>
      <div className="mz-draft__media">
        <img src={draft.image_url} alt={`Post draft for ${draft.event_title}: image with the event name, date, time and offer`} />
        <span className="mz-draft__badge">Draft – not posted</span>
      </div>
      <div className="mz-draft__body">
        <p className="mz-label">
          {draft.label} · {draft.event_title}
        </p>
        <label className="mz-draft__caption">
          <span className="mz-muted">Caption (you can edit it)</span>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={7} />
        </label>
        {draft.caption_source === "code" && draft.caption_note && (
          <p className="mz-draft__note">Caption written by code. {draft.caption_note}.</p>
        )}
        {draft.image_source === "code-drawn" && (
          <p className="mz-draft__note">Background drawn by code: Muse Image didn't deliver in time.</p>
        )}
        <div className="mz-draft__acts">
          <a className="mz-btn mz-btn--primary" href={draft.image_url} download={file}>
            Download image
          </a>
          <button type="button" className="mz-btn mz-btn--secondary" onClick={copy}>
            Copy caption
          </button>
          <button type="button" className="mz-btn mz-btn--ghost" onClick={share}>
            Share…
          </button>
        </div>
        <p className="mz-draft__status" role="status">
          {note}
        </p>
        <p className="mz-draft__meta">
          {draft.caption_source === "muse" && draft.model.caption
            ? `Caption by ${modelName(draft.model.caption)} · ${seconds(draft.latency_ms.caption)}`
            : "Caption by code"}
          {" · "}
          {draft.image_source === "muse-image" && draft.model.image
            ? `Background by ${modelName(draft.model.image)} · ${seconds(draft.latency_ms.image)}`
            : "Background by code"}
          {" · words on the image set by code from the event record. CaféPilot never posts for you."}
        </p>
      </div>
    </article>
  );
}

type Props = { hidden: boolean; events: DetectedEvent[]; request: PostRequest | null };

export function PostsPanel({ hidden, events, request }: Props) {
  const upcoming = events.filter(isUpcoming);
  const [eventId, setEventId] = useState("");
  const [offer, setOffer] = useState(SUGGESTED_OFFER);
  const [making, setMaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [variants, setVariants] = useState<PromoDraft[] | null>(null);
  const [selected, setSelected] = useState<PromoDraft | null>(null);
  const [drafts, setDrafts] = useState<PromoDraft[]>([]);
  const composer = useRef<HTMLElement>(null);
  const preview = useRef<HTMLDivElement>(null);

  const load = useCallback((signal?: AbortSignal) => {
    listPromoPosts(signal)
      .then((list) => setDrafts(Array.isArray(list) ? list : []))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!eventId && upcoming.length) setEventId(upcoming[0].event_id);
  }, [eventId, upcoming]);

  useEffect(() => {
    if (!request) return;
    setEventId(request.eventId);
    setVariants(null);
    setSelected(null);
    setError(null);
    composer.current?.scrollIntoView?.({ block: "start" });
  }, [request]);

  const make = async () => {
    setMaking(true);
    setError(null);
    setVariants(null);
    setSelected(null);
    try {
      const result = await createPromoPosts(eventId, offer.trim());
      setVariants(result.variants);
      setSelected(result.variants[0] ?? null);
      load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Muse couldn't make drafts right now.");
    } finally {
      setMaking(false);
    }
  };

  const open = (draft: PromoDraft) => {
    setSelected(draft);
    requestAnimationFrame(() => preview.current?.scrollIntoView?.({ block: "start", behavior: "smooth" }));
  };

  const event = upcoming.find((e) => e.event_id === eventId);
  return (
    <div className="mz-scroll mz-posts" hidden={hidden}>
      <header className="mz-posts__head">
        <h2 className="mz-plan__goal">Posts</h2>
        <p className="mz-muted">
          Muse drafts a post for an upcoming event. You check it, then download, copy or share it yourself: CaféPilot never
          posts anything.
        </p>
      </header>

      <section className="mz-card mz-compose" ref={composer} aria-label="Make a post">
        {upcoming.length === 0 ? (
          <p className="mz-muted">No upcoming events to promote right now.</p>
        ) : (
          <>
            <div className="mz-compose__row">
              <label className="mz-field">
                <span className="mz-label">Event</span>
                <select value={eventId} onChange={(e) => setEventId(e.target.value)} disabled={making}>
                  {upcoming.map((e) => (
                    <option key={e.event_id} value={e.event_id}>
                      {e.title} · {whenLine(e)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mz-field mz-field--grow">
                <label className="mz-label" htmlFor="mz-offer">
                  Your offer
                </label>
                <input
                  id="mz-offer"
                  type="text"
                  value={offer}
                  maxLength={OFFER_MAX}
                  onChange={(e) => setOffer(e.target.value)}
                  disabled={making}
                  aria-describedby="mz-offer-hint"
                />
                <span id="mz-offer-hint" className="mz-field__hint">
                  {offer.length}/{OFFER_MAX} · plain text
                  {offer.trim() !== SUGGESTED_OFFER && (
                    <>
                      {" · "}
                      <button type="button" className="mz-link" onClick={() => setOffer(SUGGESTED_OFFER)} disabled={making}>
                        Use “{SUGGESTED_OFFER}”
                      </button>
                    </>
                  )}
                </span>
              </div>
            </div>
            <div className="mz-compose__go">
              <button
                type="button"
                className="mz-btn mz-btn--primary mz-btn--lg"
                onClick={make}
                disabled={making || !eventId || !offer.trim()}
              >
                {making ? "Making drafts…" : "Make 2 drafts ✨"}
              </button>
              <span className="mz-muted">
                {making
                  ? `Muse is writing captions and painting backgrounds for ${event?.title ?? "the event"}. About 30 seconds.`
                  : "Muse writes the caption and paints the background; code checks every date and number and sets the text."}
              </span>
            </div>
          </>
        )}
        {error && (
          <p className="mz-error" role="alert">
            {error}
          </p>
        )}
        {making && (
          <div className="mz-thumbs" aria-hidden="true">
            <span className="mz-thumb is-loading" />
            <span className="mz-thumb is-loading" />
          </div>
        )}
        {variants && (
          <div className="mz-thumbs" role="group" aria-label="Drafts to choose from">
            {variants.map((v, i) => (
              <button
                key={v.id}
                type="button"
                className={`mz-thumb${selected?.id === v.id ? " is-on" : ""}`}
                aria-pressed={selected?.id === v.id}
                onClick={() => open(v)}
              >
                <img src={v.image_url} alt="" />
                <span>Draft {i + 1}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <div ref={preview}>{selected && <DraftPreview key={selected.id} draft={selected} />}</div>

      <section className="mz-gallery" aria-label="Drafts">
        <p className="mz-label">
          Drafts · {drafts.length}
        </p>
        <div className="mz-gallery__grid">
          {drafts.map((d) => (
            <button
              key={d.id}
              type="button"
              className={`mz-gal${selected?.id === d.id ? " is-on" : ""}`}
              onClick={() => open(d)}
              aria-label={`${d.event_title}, ${d.label}`}
            >
              <img src={d.image_url} alt="" loading="lazy" />
              <span className={`mz-gal__tag${d.sample ? " is-demo" : ""}`}>{d.label}</span>
              <span className="mz-gal__title">{d.event_title}</span>
              <span className="mz-gal__cap">{d.caption}</span>
            </button>
          ))}
        </div>
        {drafts.length === 0 && <p className="mz-muted">No drafts yet.</p>}
      </section>
    </div>
  );
}
