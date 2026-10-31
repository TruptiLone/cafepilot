import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  type AgentRun,
  type CafeStatus,
  type DetectedEvent,
  type Planning,
  type RunStep,
  AgentApiError,
  decideAll,
  decideStep,
  getCafeStatus,
  getRun,
  listEvents,
  replanStep,
  startEventRun,
  startRun,
} from "../api/agent";
import StepDetail from "../components/agent/StepDetail";
import CafeStatusPanel, { PLAN_STATE } from "../components/muse/CafeStatusPanel";
import PlanMap from "../components/muse/PlanMap";
import StoryGraph from "../components/muse/StoryGraph";
import { planCounts } from "../components/muse/stages";
import { buildStory, storyClosing } from "../components/muse/story";

const POLL_MS = 2000;
const VIEW_KEY = "cafepilot-plan-view";
const FOLD_KEY = "cafepilot-status-folded";

type View = "owner" | "technical";
type Toast = { id: number; tone: "good" | "bad" | "wait"; who: string; title: string; text: string };

function errorMessage(error: unknown): string {
  return error instanceof AgentApiError ? error.message : "Something went wrong. Try again.";
}

function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePref(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* per-viewer convenience only */
  }
}

const fmt = (value: string, options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-US", { ...options, timeZone: "America/Los_Angeles" }).format(new Date(value));

function eventChip(event: DetectedEvent) {
  return fmt(event.starts_at, { month: "short", day: "numeric" }).toUpperCase();
}

function eventLine(event: DetectedEvent) {
  const extra = event.expected.extra_drinks_typical;
  const feel =
    event.expected.confidence === "none"
      ? "no past events like this"
      : extra > 15
        ? "usually much busier"
        : extra > 0
          ? "usually a little busier"
          : extra < 0
            ? "usually a little quieter"
            : "usually a normal day";
  const miles = event.distance_m ? ` · ${(event.distance_m / 1609).toFixed(1)} miles away` : "";
  return `${fmt(event.starts_at, { weekday: "short", month: "short", day: "numeric" })} · ${fmt(event.starts_at, { hour: "numeric" })}–${fmt(event.ends_at, { hour: "numeric" })}${miles} · ${feel}`;
}

/** Everything that can change what the café panel shows or what a box says. */
function signature(runs: (AgentRun | null | undefined)[]) {
  return runs
    .filter(Boolean)
    .map((r) => (r as AgentRun).nodes.map((n) => `${n.node_id}:${n.status}:${n.external_status ?? ""}:${n.replanned_in ?? ""}`).join("|") + (r as AgentRun).status)
    .join("#");
}

type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function speechRecognition(): (new () => SpeechRecognitionLike) | null {
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as (new () => SpeechRecognitionLike) | null;
}

const FINAL_STAGES = /^(Confirmed|Rejected|Accepted|Declined)/;

function AgentPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const eventId = searchParams.get("event");

  const [events, setEvents] = useState<DetectedEvent[]>([]);
  const [status, setStatus] = useState<CafeStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [children, setChildren] = useState<Record<string, AgentRun>>({});
  const [runError, setRunError] = useState<string | null>(null);
  const [planningFor, setPlanningFor] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [rejectedPlanning, setRejectedPlanning] = useState<Planning | null>(null);
  const [request, setRequest] = useState("");
  const [view, setView] = useState<View>(() => (readPref(VIEW_KEY) === "technical" ? "technical" : "owner"));
  const [folded, setFolded] = useState(() => readPref(FOLD_KEY) === "1");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [replanning, setReplanning] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [listening, setListening] = useState(false);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const attempted = useRef(new Set<string>());
  const stages = useRef(new Map<string, string>());

  const refreshEvents = useCallback(() => {
    listEvents()
      .then(setEvents)
      .catch(() => setEvents([]));
  }, []);

  const statusRequest = useRef(0);
  const refreshStatus = useCallback(() => {
    const ticket = ++statusRequest.current;
    getCafeStatus()
      .then((next) => {
        if (ticket !== statusRequest.current) return; // a newer refresh is on its way
        setStatus(next);
        setStatusError(null);
      })
      .catch((error) => {
        if (ticket === statusRequest.current) setStatusError(errorMessage(error));
      });
  }, []);

  useEffect(refreshEvents, [refreshEvents]);

  // Load the run named in the address (only when the address changes: a plan Muse just
  // returned is already in state while the address catches up).
  const runRef = useRef<AgentRun | null>(null);
  runRef.current = run;
  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    if (runRef.current?.run_id === runId) return;
    const controller = new AbortController();
    setRunError(null);
    setReveal(false);
    getRun(runId, controller.signal)
      .then((loaded) => {
        if (!controller.signal.aborted) setRun(loaded);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setRunError(errorMessage(error));
      });
    return () => controller.abort();
  }, [runId]);

  // Plans Muse made after a change fell through: they grow under that change.
  const childIds = useMemo(
    () => (run?.nodes ?? []).map((n) => n.replanned_in).filter((id): id is string => !!id),
    [run],
  );
  useEffect(() => {
    for (const id of childIds) {
      if (children[id]) continue;
      getRun(id)
        .then((child) => setChildren((c) => ({ ...c, [id]: child })))
        .catch(() => undefined);
    }
  }, [childIds, children]);

  // Keep refreshing while Muse is working or a supplier / employee hasn't answered yet.
  useEffect(() => {
    const active = [run, ...childIds.map((id) => children[id])].filter(Boolean) as AgentRun[];
    const live = active.filter((r) => r.status === "running" || (r.waiting_on && r.waiting_on.length > 0));
    if (!live.length) return;
    const timer = window.setTimeout(() => {
      for (const r of live) {
        getRun(r.run_id)
          .then((next) => {
            if (next.run_id === runRef.current?.run_id) setRun(next);
            else setChildren((c) => ({ ...c, [next.run_id]: next }));
          })
          .catch(() => undefined);
      }
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [run, children, childIds]);

  // The café panel follows the plan.
  const sig = signature([run, ...childIds.map((id) => children[id])]);
  useEffect(() => {
    refreshStatus();
    if (sig) refreshEvents();
  }, [sig, refreshStatus, refreshEvents]);

  // Supplier and staff replies become notifications.
  useEffect(() => {
    const all = [run, ...childIds.map((id) => children[id])].filter(Boolean) as AgentRun[];
    const fresh: Toast[] = [];
    for (const r of all) {
      for (const n of r.nodes) {
        const last = n.lifecycle?.[n.lifecycle.length - 1];
        if (!last) continue;
        const key = `${r.run_id}:${n.node_id}`;
        const before = stages.current.get(key);
        stages.current.set(key, last.stage);
        if (before !== undefined && before !== last.stage && FINAL_STAGES.test(last.stage)) {
          const bad = /Rejected|Declined/.test(last.stage);
          fresh.push({
            id: Date.now() + fresh.length,
            tone: bad ? "bad" : "good",
            who: n.action === "schedule_shift" ? "Staff" : "Supplier",
            title: n.acknowledgement?.headline ?? last.stage,
            text: n.acknowledgement?.details[0] ?? "",
          });
        }
      }
    }
    if (fresh.length) {
      setToasts((t) => [...t, ...fresh]);
      for (const toast of fresh) window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== toast.id)), 7000);
    }
  }, [run, children, childIds]);

  // When a supplier says no (or someone declines), Muse looks for another way right away.
  // It only proposes: the new change still waits for the owner's OK.
  useEffect(() => {
    if (!run || replanning) return;
    const next = run.nodes.find(
      (n) => n.acknowledgement?.next_step && !n.replanned_in && !attempted.current.has(`${run.run_id}:${n.node_id}`),
    );
    if (!next) return;
    attempted.current.add(`${run.run_id}:${next.node_id}`);
    setReplanning(next.node_id);
    replanStep(run.run_id, next.node_id)
      .then(async (response) => {
        if (response.run) setChildren((c) => ({ ...c, [response.run!.run_id]: response.run! }));
        else setRejectedPlanning(response.planning);
        const refreshed = await getRun(run.run_id);
        if (refreshed.run_id === runRef.current?.run_id) setRun(refreshed);
      })
      .catch((error) => setPlanError(errorMessage(error)))
      .finally(() => setReplanning(null));
  }, [run, replanning]);

  function chooseView(next: View) {
    setView(next);
    writePref(VIEW_KEY, next);
  }

  function toggleFold() {
    setFolded((f) => {
      writePref(FOLD_KEY, f ? "0" : "1");
      return !f;
    });
  }

  async function plan(start: () => ReturnType<typeof startRun>, forEvent: string | null) {
    setPlanningFor(forEvent ?? "request");
    setPlanError(null);
    setRejectedPlanning(null);
    setSelectedId(null);
    setRun(null);
    setChildren({});
    try {
      const response = await start();
      if (response.run) {
        setReveal(true);
        setRun(response.run);
        setSearchParams(forEvent ? { event: forEvent, run: response.run.run_id } : { run: response.run.run_id });
      } else {
        setRejectedPlanning(response.planning);
      }
      refreshEvents();
    } catch (error) {
      setPlanError(errorMessage(error));
    } finally {
      setPlanningFor(null);
    }
  }

  function openEvent(id: string) {
    const event = events.find((e) => e.event_id === id);
    setSelectedId(null);
    setRejectedPlanning(null);
    setPlanError(null);
    setChildren({});
    if (event?.run_id) setSearchParams({ event: id, run: event.run_id });
    else setSearchParams({ event: id });
  }

  function openRun(id: string) {
    setSelectedId(null);
    setChildren({});
    setSearchParams({ ...(eventId ? { event: eventId } : {}), run: id });
  }

  function backToEvents() {
    setSelectedId(null);
    setChildren({});
    setRejectedPlanning(null);
    setSearchParams({});
  }

  async function onDecide(targetRun: string, step: RunStep, decision: "approved" | "rejected", edited?: Record<string, unknown>) {
    setBusy(`${targetRun}:${step.node_id}`);
    setPlanError(null);
    try {
      const updated = await decideStep(targetRun, step.node_id, decision, edited);
      if (updated.run_id === run?.run_id) setRun(updated);
      else setChildren((c) => ({ ...c, [updated.run_id]: updated }));
    } catch (error) {
      setPlanError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function approveAll() {
    if (!run) return;
    setBusy("all");
    setPlanError(null);
    try {
      setRun(await decideAll(run.run_id, "approved"));
    } catch (error) {
      setPlanError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function handleAsk(event: FormEvent) {
    event.preventDefault();
    const query = request.trim();
    if (!query || planningFor) return;
    await plan(() => startRun(query), null);
    setRequest("");
  }

  function toggleMic() {
    const Recognition = speechRecognition();
    if (!Recognition) return;
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const r = new Recognition();
    r.lang = "en-US";
    r.interimResults = true;
    r.onresult = (e) => {
      setRequest(
        Array.from(e.results)
          .map((result) => result[0]?.transcript ?? "")
          .join(""),
      );
    };
    r.onend = () => setListening(false);
    recognition.current = r;
    setListening(true);
    r.start();
  }

  const counts = run ? planCounts(run) : null;
  const story = useMemo(() => (run ? buildStory(run, children, replanning) : null), [run, children, replanning]);
  const runsById = useMemo(() => ({ ...(run ? { [run.run_id]: run } : {}), ...children }), [run, children]);
  const closing = run ? storyClosing(run, children) : null;
  const waitingChild = Object.values(children).some((c) => c.status === "awaiting_approval");
  const selectedStep = run?.nodes.find((n) => n.node_id === selectedId) ?? null;
  const selectedEvent = events.find((e) => e.event_id === eventId) ?? null;

  const anyWaiting = run?.status === "awaiting_approval" || waitingChild;
  const live: [string, string] = planningFor || run?.status === "running" || replanning
    ? ["working", replanning ? "Finding another way" : "Working"]
    : anyWaiting
      ? ["waiting", "Waiting for you"]
      : run?.waiting_on && run.waiting_on.length > 0
        ? ["waiting", `Waiting on ${run.waiting_on.join(" and ")}`]
        : ["watching", "Watching for events"];
  const highlight = planningFor || run?.status === "running" ? "running" : anyWaiting ? "waiting" : "none";
  const showPlan = !!run || !!planningFor || !!rejectedPlanning;

  return (
    <section className={`page page--muse mz${folded ? " is-folded" : ""}`} aria-label="Ask CaféPilot">
      <div className="mz-muse">
        <header className="mz-pane-head">
          <div className="mz-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 11h11v3.5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z" />
              <path d="M16 12h1.5a2.25 2.25 0 0 1 0 4.5H16" />
              <path d="M10.5 3v4.5M8.25 5.25h4.5" />
            </svg>
          </div>
          <div>
            <h1 className="mz-head-title">Muse</h1>
            <div className="mz-muted">Powered by Muse Spark</div>
          </div>
          <div className="mz-live" data-live={live[0]} role="status">
            <i />
            <span>{live[1]}</span>
          </div>
        </header>

        <nav className="mz-tabs" aria-label="Muse">
          <button type="button" className="mz-tab is-on" aria-current="page" onClick={backToEvents}>
            Events <span className="mz-tab__n">{events.length}</span>
          </button>
          <span className="mz-tab is-soon" aria-disabled="true">
            Chats
          </span>
          <span className="mz-tab is-soon" aria-disabled="true">
            Insta post
          </span>
          <Link className="mz-tab" to="/receiving">
            Deliveries
          </Link>
          <span className="mz-tab is-soon" aria-disabled="true">
            Fix-it
          </span>
        </nav>

        <div className="mz-scroll">
          {!showPlan && (
            <section className="mz-events" aria-label="Events Muse found">
              <p className="mz-label">
                Muse found {events.length} {events.length === 1 ? "event" : "events"} coming up
              </p>
              <p className="mz-purpose">
                Muse watches what's happening near the café and tells you before a day gets busier (or quieter) than usual, so
                you can get stock and staff ready in time.
              </p>
              {events.map((event) => {
                const state = event.plan_state ?? "new";
                return (
                  <article key={event.event_id} className={`mz-ev${selectedEvent?.event_id === event.event_id ? " is-sel" : ""}`}>
                    <span className="mz-ev__chip">{eventChip(event)}</span>
                    <span className="mz-ev__main">
                      <span className="mz-ev__title">{event.title}</span>
                      <span className="mz-ev__meta">{eventLine(event)}</span>
                    </span>
                    {state !== "new" && <span className={`mz-pill is-${PLAN_STATE[state][0]}`}>{PLAN_STATE[state][1]}</span>}
                    {state === "new" ? (
                      <button
                        type="button"
                        className="mz-btn mz-btn--primary"
                        disabled={planningFor !== null}
                        onClick={() => plan(() => startEventRun(event.event_id), event.event_id)}
                      >
                        Let's plan ✨
                      </button>
                    ) : (
                      <button type="button" className="mz-btn mz-btn--ghost" onClick={() => openEvent(event.event_id)}>
                        Open plan
                      </button>
                    )}
                  </article>
                );
              })}
              {events.length === 0 && <p className="mz-empty">No events coming up. Ask Muse anything below.</p>}
            </section>
          )}

          {showPlan && (
            <button type="button" className="mz-back" onClick={backToEvents}>
              ← All events
            </button>
          )}

          {planError && (
            <p className="mz-error" role="alert">
              {planError}
            </p>
          )}
          {runError && (
            <p className="mz-error" role="alert">
              {runError}
            </p>
          )}
          {rejectedPlanning && <PlanningNotice planning={rejectedPlanning} />}
          {planningFor && <PlanningSkeleton title={events.find((e) => e.event_id === planningFor)?.title ?? "your request"} />}

          {run && !planningFor && counts && story && (
            <section className="mz-plan" aria-label="Muse's plan">
              {run.parent_run_id && (
                <p className="mz-parent">
                  This plan replaces a change that fell through.{" "}
                  <button type="button" className="mz-link" onClick={() => openRun(run.parent_run_id as string)}>
                    See the earlier plan
                  </button>
                </p>
              )}
              <div className="mz-plan__head">
                <div>
                  <h2 className="mz-plan__goal">Muse's plan{run.event ? ` for the ${run.event.title.replace(/^Santa Clara /, "").toLowerCase()}` : ""}</h2>
                  <p className="mz-muted">
                    {counts.checksDone} of {counts.checks} checks done
                    {counts.waiting > 0 ? ` · ${counts.waiting} ${counts.waiting === 1 ? "decision" : "decisions"} waiting` : ""}
                    {view === "owner" ? " · hover a step for details" : ""}
                  </p>
                </div>
                <div className="mz-plan__tools">
                  <div className="mz-toggle" role="group" aria-label="Plan view">
                    <button type="button" aria-pressed={view === "owner"} onClick={() => chooseView("owner")}>
                      Owner view
                    </button>
                    <button type="button" aria-pressed={view === "technical"} onClick={() => chooseView("technical")}>
                      Technical view
                    </button>
                  </div>
                  {counts.waiting > 1 && (
                    <button type="button" className="mz-btn mz-btn--primary" disabled={busy !== null} onClick={approveAll}>
                      {busy === "all" ? "Approving…" : "Approve all"}
                    </button>
                  )}
                </div>
              </div>

              <div className="mz-card mz-graph-card">
                {view === "owner" ? (
                  <StoryGraph story={story} runs={runsById} busy={busy} reveal={reveal} onDecide={onDecide} onOpenRun={openRun} />
                ) : (
                  <PlanMap
                    run={run}
                    view="graph"
                    selectedId={selectedId}
                    onSelect={(id) => setSelectedId((current) => (current === id ? null : id))}
                  />
                )}
                {view === "owner" && (
                  <div className="mz-legend2" aria-hidden="true">
                    <span><i className="d" />Done</span>
                    <span><i className="w" />Needs your OK</span>
                    <span><i className="x" />Fell through</span>
                    <span><i className="a" />After you approve</span>
                  </div>
                )}
              </div>
              {view === "technical" && selectedStep && (
                <div className="mz-detail">
                  <StepDetail step={selectedStep} onClose={() => setSelectedId(null)} />
                </div>
              )}

              {closing && <p className="mz-muse-line">{closing}</p>}

              {story.summary.length > 0 && (
                <div className="mz-card mz-short">
                  <p className="mz-label">In short</p>
                  <ul>
                    {story.summary.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}

              <p className="mz-foot">
                Planned by {run.model}
                {run.attempts > 1 ? ` after ${run.attempts} attempts` : ""}. Every number comes from the café's data, worked out
                in code; nothing changes until you approve.
              </p>
            </section>
          )}
        </div>

        <form className="mz-cmd" onSubmit={handleAsk}>
          <div className="mz-cmd__bar">
            <input
              type="text"
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              placeholder="Ask Muse… e.g. a catering order for 25 lattes Friday 9 AM"
              aria-label="Ask Muse"
              disabled={planningFor !== null}
            />
            {speechRecognition() && (
              <button
                type="button"
                className={`mz-mic${listening ? " is-on" : ""}`}
                aria-label={listening ? "Stop listening" : "Speak to Muse"}
                aria-pressed={listening}
                onClick={toggleMic}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="9" y="2" width="6" height="12" rx="3" />
                  <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" />
                </svg>
              </button>
            )}
            <button type="submit" className="mz-send" aria-label="Send" disabled={!request.trim() || planningFor !== null}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
          <div className="mz-audit">
            <i />
            {counts ? `${counts.checks} ${counts.checks === 1 ? "check" : "checks"} · ` : ""}nothing changes without your OK
          </div>
        </form>
      </div>

      <CafeStatusPanel status={status} error={statusError} highlight={highlight} collapsed={folded} onToggle={toggleFold} />

      <div className="mz-toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`mz-toast is-${t.tone}`} role="status">
            <span className="mz-toast__who">{t.who}</span>
            <div>
              <div className="mz-toast__title">{t.title}</div>
              {t.text && <div className="mz-toast__text">{t.text}</div>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PlanningSkeleton({ title }: { title: string }) {
  return (
    <div className="mz-card mz-graph-card mz-planning" aria-live="polite">
      <div className="mz-plan__goal">Muse is planning for {title}…</div>
      <p className="mz-muted">Checking the forecast, stock, suppliers and the roster. Usually 10 to 20 seconds.</p>
      <div className="mz-skel">
        <i className="w" />
        <div>
          <i />
          <i />
        </div>
        <div>
          <i />
          <i />
        </div>
        <div>
          <i className="o" />
          <i className="o" />
        </div>
      </div>
    </div>
  );
}

function PlanningNotice({ planning }: { planning: Planning }) {
  const { plan, validation } = planning;
  if (plan.status === "needs_clarification") {
    return (
      <div className="mz-notice" role="status">
        <h3>One question first</h3>
        <p>{plan.question}</p>
        <p className="mz-muted">Add the answer to your request and ask again.</p>
      </div>
    );
  }
  if (plan.status === "out_of_scope") {
    return (
      <div className="mz-notice" role="status">
        <h3>That's outside what CaféPilot handles</h3>
        <p>Ask about stock, sales, staff, deliveries or supplier orders.</p>
      </div>
    );
  }
  return (
    <div className="mz-notice mz-notice--bad" role="alert">
      <h3>The plan didn't pass CaféPilot's safety checks</h3>
      <p>Nothing was run. The checks found:</p>
      <ul>
        {validation.issues.map((issue, index) => (
          <li key={index}>{issue.message}</li>
        ))}
      </ul>
      <p className="mz-muted">Try rephrasing the request with dates and quantities.</p>
    </div>
  );
}

export default AgentPage;
