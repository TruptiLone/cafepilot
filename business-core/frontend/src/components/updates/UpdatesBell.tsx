import { useEffect, useRef, useState } from "react";
import { type AICallRow, type CafeUpdate, getAICalls } from "../../api/updates";
import { modelName } from "../muse/story";
import { simulatedLabel, useUpdates } from "./UpdatesProvider";

const GROUPS: { group: CafeUpdate["group"]; label: string }[] = [
  { group: "staff", label: "Staff covered" },
  { group: "orders", label: "Orders placed" },
  { group: "plans", label: "Muse's plans" },
];

const PURPOSE: Record<AICallRow["purpose"], string> = { plan: "Plan", replan: "Replan", chat: "Ask", other: "Photo read" };

const time = (value: string) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" }).format(new Date(value));

/** The Updates bell: badge for what's new, a panel with Updates (grouped) and AI activity. */
function UpdatesBell({ className = "" }: { className?: string }) {
  const { updates, unseen, markSeen, setPanelOpen } = useUpdates();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"updates" | "ai">("updates");
  const [calls, setCalls] = useState<AICallRow[] | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || tab !== "ai") return;
    getAICalls(20)
      .then(setCalls)
      .catch(() => setCalls([]));
  }, [open, tab]);

  useEffect(() => {
    setPanelOpen(open);
    return () => setPanelOpen(false);
  }, [open, setPanelOpen]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => root.current && !root.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  function toggle() {
    setOpen((o) => !o);
    markSeen();
  }

  return (
    <div className={`updates ${className}`} ref={root}>
      <button
        type="button"
        className="updates-bell"
        aria-label={unseen ? `Updates, ${unseen} new` : "Updates"}
        aria-expanded={open}
        onClick={toggle}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unseen > 0 && <span className="updates-badge">{unseen > 9 ? "9+" : unseen}</span>}
      </button>
      {open && (
        <div className="updates-panel" role="dialog" aria-label="Updates">
          <div className="updates-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "updates"} onClick={() => setTab("updates")}>
              Updates
            </button>
            <button type="button" role="tab" aria-selected={tab === "ai"} onClick={() => setTab("ai")}>
              AI activity
            </button>
          </div>
          {tab === "updates" ? (
            <div className="updates-list">
              {updates.length === 0 && <p className="updates-empty">No updates yet. Replies from staff and suppliers show up here.</p>}
              {GROUPS.map(({ group, label }) => {
                const items = updates.filter((u) => u.group === group);
                if (!items.length) return null;
                return (
                  <section key={group} aria-label={label}>
                    <h4 className="updates-group">{label}</h4>
                    {items.map((u) => (
                      <article key={u.id} className={`updates-item is-${/declined|rejected|partly/.test(u.kind) ? "bad" : "ok"}`}>
                        <p className="updates-item__title">{u.title}</p>
                        {u.detail && <p className="updates-item__detail">{u.detail}</p>}
                        <p className="updates-item__meta">
                          {time(u.at)}
                          {u.model ? ` · ${modelName(u.model)}` : ""}
                          {u.simulated && <span className="updates-sim">{simulatedLabel(u)}</span>}
                        </p>
                      </article>
                    ))}
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="updates-list">
              {calls === null && <p className="updates-empty">Loading…</p>}
              {calls?.length === 0 && <p className="updates-empty">No AI calls yet.</p>}
              {calls?.map((c) => (
                <article key={c.id} className={`updates-item is-${c.outcome === "ok" || c.outcome === "retried" ? "ok" : "bad"}`}>
                  <p className="updates-item__title">{c.request_summary || PURPOSE[c.purpose]}</p>
                  <p className="updates-item__detail">
                    {modelName(c.model)} · {(c.latency_ms / 1000).toFixed(1)} s
                    {c.attempts > 1 ? ` · ${c.attempts} tries` : ""}
                    <span className={`updates-outcome is-${c.outcome}`}>{c.outcome}</span>
                  </p>
                  <p className="updates-item__meta">{time(c.created_at)}</p>
                </article>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default UpdatesBell;
