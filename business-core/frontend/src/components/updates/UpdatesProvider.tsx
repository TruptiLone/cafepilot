import { type ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { type CafeUpdate, getUpdates } from "../../api/updates";

export const POLL_MS = 2500;
export const TOAST_MS = 6000;

type UpdatesState = {
  updates: CafeUpdate[];
  unseen: number;
  markSeen: () => void;
  /** The bell's panel is open: it lists everything, so toasts step aside. */
  setPanelOpen: (open: boolean) => void;
};

const UpdatesContext = createContext<UpdatesState>({
  updates: [], unseen: 0, markSeen: () => undefined, setPanelOpen: () => undefined,
});

export function useUpdates(): UpdatesState {
  return useContext(UpdatesContext);
}

const WHO: Record<CafeUpdate["group"], string> = { staff: "Staff", orders: "Supplier", plans: "Muse" };

/** Sending is not a reply: a sent order is from a simulated supplier; answers are simulated replies. */
export function simulatedLabel(update: CafeUpdate): string {
  return update.kind === "order_sent" ? "simulated supplier" : "simulated reply";
}

export function updateTone(update: CafeUpdate): "good" | "bad" | "wait" {
  if (/rejected|declined/.test(update.kind)) return "bad";
  if (update.kind === "replan_started" || update.kind === "order_sent") return "wait";
  return "good";
}

/** One poller for the whole app: new updates slide in as toasts and add to the bell's badge. */
export function UpdatesProvider({
  children,
  pollMs = POLL_MS,
  toastMs = TOAST_MS,
}: {
  children: ReactNode;
  pollMs?: number;
  toastMs?: number;
}) {
  const [updates, setUpdates] = useState<CafeUpdate[]>([]);
  const [unseen, setUnseen] = useState(0);
  const [toasts, setToasts] = useState<CafeUpdate[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const latest = useRef<string | undefined>(undefined);

  useEffect(() => {
    // Local to this poll loop: an effect that React runs twice in development must not mistake the
    // first load's history for news, and a stopped loop's late answer is dropped.
    let stopped = false;
    let primed = false;
    let timer: number | undefined;
    async function tick() {
      try {
        const fresh = await getUpdates(latest.current);
        if (stopped) return;
        if (fresh.length) {
          latest.current = fresh[0].at; // newest first
          setUpdates((current) => {
            const known = new Set(current.map((u) => u.id));
            return [...fresh.filter((u) => !known.has(u.id)), ...current].slice(0, 100);
          });
          // What happened before the page opened is history, not news.
          if (primed) {
            setUnseen((n) => n + fresh.length);
            setToasts((t) => [...t, ...[...fresh].reverse()]);
            for (const u of fresh) window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== u.id)), toastMs);
          }
        }
        primed = true;
      } catch {
        /* the next poll tries again */
      }
      if (!stopped) timer = window.setTimeout(tick, pollMs);
    }
    void tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [pollMs, toastMs]);

  const markSeen = useCallback(() => setUnseen(0), []);

  return (
    <UpdatesContext.Provider value={{ updates, unseen, markSeen, setPanelOpen }}>
      {children}
      <div className="mz-toasts updates-toasts" aria-live="polite">
        {!panelOpen && toasts.map((u) => (
          <div key={u.id} className={`mz-toast is-${updateTone(u)}`} role="status">
            <span className="mz-toast__who">{WHO[u.group]}</span>
            <div>
              <div className="mz-toast__title">{u.title}</div>
              {u.detail && <div className="mz-toast__text">{u.detail}</div>}
              {u.simulated && <span className="updates-sim">{simulatedLabel(u)}</span>}
            </div>
          </div>
        ))}
      </div>
    </UpdatesContext.Provider>
  );
}
