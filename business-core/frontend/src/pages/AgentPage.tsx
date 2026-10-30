import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  type AgentRun,
  type CafeStatus,
  type DetectedEvent,
  type Planning,
  AgentApiError,
  getCafeStatus,
  getRun,
  listEvents,
  replanStep,
  startEventRun,
  startRun,
} from "../api/agent";
import StepDetail from "../components/agent/StepDetail";
import ApprovalList from "../components/muse/ApprovalList";
import CafeStatusPanel, { PLAN_STATE } from "../components/muse/CafeStatusPanel";
import PlanMap, { type PlanView, useNarrow } from "../components/muse/PlanMap";
import PlanReceipt from "../components/muse/PlanReceipt";
import { planCounts } from "../components/muse/stages";

const POLL_MS = 2000;
const VIEW_KEY = "cafepilot-plan-view";

function errorMessage(error: unknown): string {
  return error instanceof AgentApiError ? error.message : "Something went wrong. Try again.";
}

function readView(): PlanView {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "graph" ? "graph" : "tiles";
  } catch {
    return "tiles";
  }
}

function eventChip(value: string) {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/Los_Angeles" })
    .format(new Date(value))
    .toUpperCase();
}

function eventWhen(event: DetectedEvent) {
  const fmt = (value: string, options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-US", { ...options, timeZone: "America/Los_Angeles" }).format(new Date(value));
  return `${fmt(event.starts_at, { weekday: "short", month: "short", day: "numeric" })} · ${fmt(event.starts_at, {
    hour: "numeric",
  })}–${fmt(event.ends_at, { hour: "numeric" })}`;
}

/** Signature of everything on a run that can change what the café panel shows. */
function runSignature(run: AgentRun | null) {
  if (!run) return "";
  return run.nodes.map((n) => `${n.node_id}:${n.status}:${n.external_status ?? ""}`).join("|") + run.status;
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

function AgentPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const eventId = searchParams.get("event");

  const [events, setEvents] = useState<DetectedEvent[]>([]);
  const [status, setStatus] = useState<CafeStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [planningFor, setPlanningFor] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [rejectedPlanning, setRejectedPlanning] = useState<Planning | null>(null);
  const [request, setRequest] = useState("");
  const [view, setView] = useState<PlanView>(readView);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [replanning, setReplanning] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const narrow = useNarrow();

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
    getRun(runId, controller.signal)
      .then((loaded) => {
        if (!controller.signal.aborted) setRun(loaded);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setRunError(errorMessage(error));
      });
    return () => controller.abort();
  }, [runId]);

  // Keep refreshing while Muse is working or a supplier / employee hasn't answered yet.
  useEffect(() => {
    if (!run || (run.status !== "running" && !(run.waiting_on && run.waiting_on.length > 0))) return;
    const timer = window.setTimeout(() => {
      getRun(run.run_id).then(setRun).catch(() => undefined);
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [run]);

  // The café panel follows the plan: refresh whenever a step or a supplier answer changes.
  const signature = runSignature(run);
  useEffect(() => {
    refreshStatus();
    if (signature) refreshEvents();
  }, [signature, refreshStatus, refreshEvents]);

  function chooseView(next: PlanView) {
    setView(next);
    try {
      window.localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* per-viewer convenience only */
    }
  }

  async function plan(start: () => ReturnType<typeof startRun>, forEvent: string | null) {
    setPlanningFor(forEvent ?? "request");
    setPlanError(null);
    setRejectedPlanning(null);
    setSelectedId(null);
    setRun(null);
    try {
      const response = await start();
      if (response.run) {
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
    if (event?.run_id) setSearchParams({ event: id, run: event.run_id });
    else setSearchParams({ event: id });
  }

  async function handleReplan(nodeId: string) {
    if (!run) return;
    setReplanning(nodeId);
    setPlanError(null);
    try {
      const response = await replanStep(run.run_id, nodeId);
      if (response.run) {
        setSelectedId(null);
        setRun(response.run);
        setSearchParams({ ...(eventId ? { event: eventId } : {}), run: response.run.run_id });
      } else {
        setRejectedPlanning(response.planning);
      }
      refreshEvents();
    } catch (error) {
      setPlanError(errorMessage(error));
    } finally {
      setReplanning(null);
    }
  }

  function openRun(id: string) {
    setSelectedId(null);
    setSearchParams({ ...(eventId ? { event: eventId } : {}), run: id });
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
      const text = Array.from(e.results)
        .map((result) => result[0]?.transcript ?? "")
        .join("");
      setRequest(text);
    };
    r.onend = () => setListening(false);
    recognition.current = r;
    setListening(true);
    r.start();
  }

  const selectedEvent =
    events.find((e) => e.event_id === eventId) ??
    (run ? events.find((e) => e.run_id === run.run_id || e.latest_run_id === run.run_id) : undefined);
  const counts = run ? planCounts(run) : null;
  const selectedStep = run?.nodes.find((n) => n.node_id === selectedId && n.status !== "awaiting_approval") ?? null;

  const live = useMemo((): [string, string] => {
    if (planningFor || run?.status === "running") return ["working", "Working"];
    if (run?.status === "awaiting_approval") return ["waiting", "Waiting for you"];
    if (run?.waiting_on && run.waiting_on.length > 0) return ["waiting", `Waiting on ${run.waiting_on.join(" and ")}`];
    return ["watching", "Watching for events"];
  }, [planningFor, run]);

  const highlight = planningFor || run?.status === "running" ? "running" : run?.status === "awaiting_approval" ? "waiting" : "none";

  let museLine: string | null = null;
  if (planningFor) museLine = "Checking stock, sales history and the roster. This usually takes 10 to 20 seconds.";
  else if (run?.closing_message) museLine = run.closing_message;
  else if (run && counts?.waiting)
    museLine = `${counts.waiting === 1 ? "1 change needs" : `${counts.waiting} changes need`} your OK. Nothing changes until you approve.`;
  else if (run) museLine = run.goal;

  return (
    <section className="page page--muse mz" aria-label="Ask CaféPilot">
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

        <div className="mz-scroll">
          <section className="mz-events" aria-label="Events Muse found">
            <p className="mz-label">
              Muse found {events.length} {events.length === 1 ? "event" : "events"} coming up
            </p>
            {events.map((event) => {
              const state = event.plan_state ?? "new";
              const isSelected = selectedEvent?.event_id === event.event_id;
              const busy = planningFor === event.event_id;
              return (
                <article key={event.event_id} className={`mz-ev${isSelected ? " is-sel" : ""}`}>
                  <button type="button" className="mz-ev__open" onClick={() => openEvent(event.event_id)} aria-label={`Open ${event.title}`}>
                    <span className="mz-ev__chip">{eventChip(event.starts_at)}</span>
                    <span className="mz-ev__main">
                      <span className="mz-ev__title">{event.title}</span>
                      <span className="mz-ev__meta">
                        {eventWhen(event)} · {event.is_demo ? "demo data" : event.source}
                      </span>
                    </span>
                    {state !== "new" && <span className={`mz-pill is-${PLAN_STATE[state][0]}`}>{PLAN_STATE[state][1]}</span>}
                  </button>
                  {state === "new" ? (
                    <button
                      type="button"
                      className="mz-btn mz-btn--primary"
                      disabled={planningFor !== null}
                      onClick={() => plan(() => startEventRun(event.event_id), event.event_id)}
                    >
                      {busy ? "Checking…" : "Check if we're ready"}
                    </button>
                  ) : (
                    <button type="button" className="mz-btn mz-btn--ghost" onClick={() => openEvent(event.event_id)}>
                      {isSelected && run ? "Showing plan" : "Open plan"}
                    </button>
                  )}
                  {isSelected && (
                    <p className="mz-ev__muse">
                      {event.muse_message}
                      <span className="mz-ev__basis">{event.expected.basis}</span>
                    </p>
                  )}
                </article>
              );
            })}
          </section>

          {museLine && <p className="mz-muse-line">{museLine}</p>}
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

          {planningFor && <PlanningSkeleton />}

          {run && !planningFor && counts && (
            <section className="mz-plan" aria-label="Muse's plan">
              {run.parent_run_id && (
                <p className="mz-parent">
                  Muse planned this because a change in an earlier plan fell through.{" "}
                  <button type="button" className="mz-link" onClick={() => openRun(run.parent_run_id as string)}>
                    See the earlier plan
                  </button>
                </p>
              )}
              <div className="mz-plan__head">
                <div>
                  <h2 className="mz-plan__goal">{run.goal}</h2>
                  <p className="mz-muted">
                    {counts.checksDone} of {counts.checks} checks done
                    {counts.waiting > 0 ? ` · ${counts.waiting} waiting for you` : ""}
                  </p>
                </div>
                {!narrow && <div className="mz-toggle" role="group" aria-label="Plan view">
                  <button type="button" aria-pressed={view === "tiles"} onClick={() => chooseView("tiles")}>
                    Steps
                  </button>
                  <button type="button" aria-pressed={view === "graph"} onClick={() => chooseView("graph")}>
                    Dependencies
                  </button>
                </div>}
              </div>
              {run.assumptions.length > 0 && (
                <details className="mz-assume">
                  <summary>Muse assumed {run.assumptions.length === 1 ? "1 thing" : `${run.assumptions.length} things`}</summary>
                  <ul>
                    {run.assumptions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </details>
              )}

              <PlanMap
                run={run}
                view={view}
                selectedId={selectedId}
                onSelect={(id) => setSelectedId((current) => (current === id ? null : id))}
              />

              {selectedStep && (
                <div className="mz-detail">
                  <StepDetail step={selectedStep} onClose={() => setSelectedId(null)} />
                </div>
              )}

              <ApprovalList run={run} selectedId={selectedId} onSelect={setSelectedId} onUpdated={setRun} />
              <PlanReceipt run={run} onReplan={handleReplan} onOpenRun={openRun} replanning={replanning} />

              <p className="mz-foot">
                Planned by {run.model}
                {run.attempts > 1 ? ` after ${run.attempts} attempts` : ""}. Every number comes from the café's data, worked
                out in code.
              </p>
            </section>
          )}

          {!run && !planningFor && !rejectedPlanning && !runError && (
            <div className="mz-empty">
              {selectedEvent ? (
                <p>Say “Check if we're ready” and Muse will plan for {selectedEvent.title}.</p>
              ) : (
                <p>Pick an event, or ask Muse anything below.</p>
              )}
            </div>
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

      <CafeStatusPanel status={status} error={statusError} highlight={highlight} onOpenEvent={openEvent} />
    </section>
  );
}

function PlanningSkeleton() {
  return (
    <div className="mz-groups mz-groups--skeleton" aria-hidden="true">
      {["Look up", "Work out", "Needs your OK"].map((label, i) => (
        <section key={label} className="mz-group">
          <h4 className="mz-col-label">{label}</h4>
          <div className="mz-group__tiles">
            {Array.from({ length: i === 0 ? 3 : 2 }, (_, j) => (
              <div key={j} className="mz-tile" data-s={i === 0 && j === 0 ? "running" : "pending"}>
                <span className="mz-tile__top">
                  <span className="mz-tile__ico" />
                  <span className="mz-tile__title mz-shimmer" />
                </span>
                <span className="mz-tile__res mz-shimmer" />
              </div>
            ))}
          </div>
        </section>
      ))}
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
