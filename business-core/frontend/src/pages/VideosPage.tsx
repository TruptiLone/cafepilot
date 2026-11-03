import { useEffect, useRef, useState } from "react";
import { listVideos, retryVideo, videoRequest, VideoApiError, type FrameResult, type VideoRecord } from "../api/videos";

const TOKEN_KEY = "cafepilot-video-token";
const STATUS = { queued: "Queued", extracting: "Extracting frames", processing: "Muse is reading frames", complete: "Processed", partial: "Partly processed", failed: "Processing failed" };
const SOURCE = { "meta-glasses": "Meta glasses", "mock-glasses": "Mock glasses", test: "Test clip · not from glasses" };
const message = (error: unknown) => error instanceof Error ? error.message : "The service could not be reached.";

export default function VideosPage() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [draft, setDraft] = useState("");
  const [videos, setVideos] = useState<VideoRecord[]>([]);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let active = true;
    async function poll() {
      setSyncing(true);
      let denied = false;
      try {
        const result = await listVideos(token, controller.signal);
        if (active) { setVideos(result); setError(""); }
      } catch (cause) {
        if (active && !controller.signal.aborted) {
          setError(message(cause));
          if (cause instanceof VideoApiError && cause.status === 401) {
            denied = true;
            sessionStorage.removeItem(TOKEN_KEY);
            setToken("");
          }
        }
      } finally {
        if (active) {
          setSyncing(false);
          if (!denied) timer = setTimeout(poll, 2000);
        }
      }
    }
    void poll();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [token, refresh]);

  return (
    <section className="page page--videos" aria-labelledby="page-title">
      <div className="videos-heading">
        <div>
          <p className="page__eyebrow">Glasses capture</p>
          <h1 id="page-title">Barista clips</h1>
          <p className="page__description">Your recordings, read by Muse. New clips and results appear here automatically.</p>
        </div>
        {token && <div className="videos-actions">
          <span className={`videos-live${error ? " is-offline" : ""}`}>{error ? "Reconnecting" : syncing ? "Updating…" : "Live · every 2 seconds"}</span>
          <button className="rcv-button rcv-button--small" onClick={() => setRefresh(n => n + 1)}>Refresh</button>
          <button className="text-button" onClick={() => { sessionStorage.removeItem(TOKEN_KEY); setToken(""); setVideos([]); }}>Disconnect</button>
        </div>}
      </div>
      {error && <p className="videos-error" role="alert">{error}</p>}
      {!token ? <form className="videos-connect" onSubmit={event => {
        event.preventDefault();
        const value = draft.trim();
        if (!value) return;
        sessionStorage.setItem(TOKEN_KEY, value);
        setToken(value); setDraft(""); setError("");
      }}>
        <h2>Connect to your captures</h2>
        <p>Enter the same upload token used by the iPhone bridge. It stays in this browser tab’s session.</p>
        <label htmlFor="video-token">Upload token</label>
        <input id="video-token" type="password" autoComplete="off" value={draft} onChange={event => setDraft(event.target.value)} required />
        <button className="rcv-button rcv-button--primary" type="submit">Connect</button>
      </form> : videos.length === 0 ? <div className="videos-empty">
        <h2>{syncing ? "Checking for clips…" : "Ready for your first clip"}</h2>
        <p>New recordings will appear here as soon as they reach the service.</p>
      </div> : <div className="videos-grid">
        {videos.map((video, index) => <VideoCard key={video.id} video={video} token={token} loadInitially={index === 0} onRetry={() => setRefresh(n => n + 1)} />)}
      </div>}
    </section>
  );
}

function VideoCard({ video, token, loadInitially, onRetry }: { video: VideoRecord; token: string; loadInitially: boolean; onRetry: () => void }) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState("");
  const initialLoad = useRef(loadInitially);
  const controller = useRef<AbortController | null>(null);
  const objectURL = useRef("");
  const player = useRef<HTMLVideoElement>(null);
  const processing = video.processing;
  const items = [...new Set(processing.frames.flatMap(frame => frame.result?.items.map(item => item.product).filter(Boolean) ?? []))];

  async function loadClip() {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true); setError("");
    try {
      // Auth goes in a header, never a URL. Load only the latest clip automatically;
      // older clips load on demand. The browser then plays the local blob normally.
      const response = await videoRequest(video.url, token, { signal: request.signal });
      const blob = await response.blob();
      if (request.signal.aborted) return;
      if (objectURL.current) URL.revokeObjectURL(objectURL.current);
      objectURL.current = URL.createObjectURL(blob);
      setUrl(objectURL.current);
    } catch (cause) {
      if (!request.signal.aborted) setError(message(cause));
    } finally { if (!request.signal.aborted) setLoading(false); }
  }

  useEffect(() => {
    if (initialLoad.current) void loadClip();
    return () => {
      controller.current?.abort();
      if (objectURL.current) URL.revokeObjectURL(objectURL.current);
    };
  }, [video.id, token]);

  return <article className="video-card">
    <div className="video-card__player">
      <video ref={player} controls playsInline preload="metadata" src={url || undefined} aria-label={`Clip recorded ${new Date(video.captured_at).toLocaleString()}`} />
      {!url && <button className="rcv-button" disabled={loading} onClick={() => void loadClip()}>{loading ? "Loading clip…" : "Load clip"}</button>}
    </div>
    <div className="video-card__body">
      <div className="video-card__top"><span className="video-source">{SOURCE[video.source]}</span><span className={`video-state video-state--${processing.status}`}>{STATUS[processing.status]}</span></div>
      <h2>{new Date(video.captured_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</h2>
      <p className="video-meta">
        {processing.duration_seconds != null && `${processing.duration_seconds.toFixed(1)} seconds · `}
        {processing.width && processing.height && `${processing.width} × ${processing.height} · `}
        {(video.size_bytes / 1_048_576).toFixed(2)} MB
      </p>
      {processing.total_frames > 0 && <p className="video-progress">{processing.completed_frames} / {processing.total_frames} frames read · approximately one per second</p>}
      {processing.model && <p className="video-model">Model: {processing.model}</p>}
      {items.length > 0 && <p><strong>Seen in this clip:</strong> {items.join(", ")}</p>}
      <p className="video-caveat">Counts are observations in individual frames. Repeated views are not added together as stock.</p>
      {processing.error && <p className="videos-error">{processing.error}</p>}
      {error && <p className="videos-error" role="alert">{error}</p>}
      {processing.frames.length > 0 && <details className="video-results"><summary>Frame-by-frame results</summary>
        {processing.frames.map(frame => <FrameObservation key={frame.number} frame={frame} onSeek={() => {
          if (player.current && url) player.current.currentTime = frame.offset_seconds;
        }} />)}
      </details>}
      {processing.status !== "complete" && <button className="rcv-button rcv-button--small" disabled={retrying} onClick={async () => {
        setRetrying(true); setError("");
        try { await retryVideo(video.id, token); onRetry(); }
        catch (cause) { setError(message(cause)); }
        finally { setRetrying(false); }
      }}>{retrying ? "Requesting…" : "Retry unfinished processing"}</button>}
      <p className="video-filename">{video.filename}</p>
    </div>
  </article>;
}

function FrameObservation({ frame, onSeek }: { frame: FrameResult; onSeek: () => void }) {
  return <details className="video-frame-result">
    <summary>Sample {frame.number + 1} · around {frame.offset_seconds}s {frame.error ? "· needs retry" : ""}</summary>
    <button className="text-button" onClick={onSeek}>Seek to this point in the clip</button>
    {frame.error && <p className="videos-error">{frame.error}</p>}
    {frame.result && <>
      {frame.result.notes && <p>{frame.result.notes}</p>}
      {frame.result.visible_damage && <p><strong>Visible damage:</strong> {frame.result.visible_damage}</p>}
      {frame.result.items.length > 0 ? <div className="video-table-wrap"><table><thead><tr><th>Product</th><th>Visible units</th><th>Confidence</th></tr></thead><tbody>
        {frame.result.items.map((item, index) => <tr key={index}><td>{item.product || item.label_text || "Unidentified item"}{item.possibly_hidden_units && <small>More may be hidden</small>}</td><td>{item.visible_units}</td><td>{item.confidence || "Not given"}</td></tr>)}
      </tbody></table></div> : <p>No delivery items identified in this frame.</p>}
    </>}
  </details>;
}
