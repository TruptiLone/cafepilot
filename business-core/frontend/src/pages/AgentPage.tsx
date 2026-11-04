import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ACTIVE_CAFE_NAME,
  type AgentRun,
  type DetectedEvent,
  type PlanState,
  type Planning,
  type RunStep,
  AgentApiError,
  decideAll,
  decideStep,
  getRun,
  listEvents,
  replanStep,
  startEventRun,
  startRun,
} from "../api/agent";
import StepDetail from "../components/agent/StepDetail";
import { employeeName } from "../components/agent/changes";
import ChatPanel, { EXAMPLES } from "../components/muse/ChatPanel";
import UpdatesBell from "../components/updates/UpdatesBell";
import AtAGlance from "../components/muse/AtAGlance";
import { type PostRequest, PostsPanel, isUpcoming } from "../components/promo/PostsPanel";
import { demoDateLabel } from "../components/demoDate";
import PlanMap from "../components/muse/PlanMap";
import { type SpeechRecognitionLike, speechRecognition } from "../components/muse/speech";
import StoryGraph from "../components/muse/StoryGraph";
import { planCounts } from "../components/muse/stages";
import { buildStory, modelName, storyClosing } from "../components/muse/story";

const POLL_MS = 2000;
const VIEW_KEY = "cafepilot-plan-view";

const PLAN_STATE: Record<PlanState, [string, string]> = {
  new: ["neutral", "New"],
  checking: ["run", "Checking"],
  needs_ok: ["wait", "Needs your OK"],
  waiting: ["wait", "Waiting on reply"],
  planned: ["done", "Planned"],
  issues: ["no", "Needs attention"],
};

type View = "owner" | "technical";

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

/** "Good morning / afternoon / evening" by the café's clock. */
function greeting(now = new Date()): string {
  const hour = Number(new Intl.DateTimeFormat("en-US", { hour: "numeric", hourCycle: "h23", timeZone: "America/Los_Angeles" }).format(now));
  return hour < 12 ? "Good morning." : hour < 17 ? "Good afternoon." : "Good evening.";
}

/** The owner-voice line for an event card, from its history ([what to expect, the question]). */
function eventPitch(event: DetectedEvent): [string, string] {
  const day = fmt(event.starts_at, { weekday: "long" });
  const subject = event.category === "farmers_market" ? `${day}'s market` : `${event.title} on ${day}`;
  const extra = event.expected.extra_drinks_typical;
  if (event.expected.confidence === "none") return [`${subject} is new to us, with no history yet.`, "Want me to check we're ready?"];
  if (extra > 15) return [`${subject} usually brings a crowd.`, "Want me to get ready?"];
  if (extra > 0) return [`${subject} is usually a little busier.`, "Want me to check we're ready?"];
  if (extra < 0) return [`${subject} is usually quieter.`, "Want me to check anyway?"];
  return [`${subject} is usually a normal day.`, "Want me to check we're ready?"];
}

/** When and where: "Sun, Nov 8 · 8 AM–2 PM · 0.2 miles away" (how busy it gets is in eventPitch). */
function eventLine(event: DetectedEvent) {
  const miles = event.distance_m ? ` · ${(event.distance_m / 1609).toFixed(1)} miles away` : "";
  return `${fmt(event.starts_at, { weekday: "short", month: "short", day: "numeric" })} · ${fmt(event.starts_at, { hour: "numeric" })}–${fmt(event.ends_at, { hour: "numeric" })}${miles}`;
}

/** Everything that can change an event's plan state or what a box says. */
function signature(runs: (AgentRun | null | undefined)[]) {
  return runs
    .filter(Boolean)
    .map((r) => (r as AgentRun).nodes.map((n) => `${n.node_id}:${n.status}:${n.external_status ?? ""}:${n.replanned_in ?? ""}`).join("|") + (r as AgentRun).status)
    .join("#");
}

function AgentPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const eventId = searchParams.get("event");

  const [events, setEvents] = useState<DetectedEvent[]>([]);
  const [eventsLoaded, setEventsLoaded] = useState(false);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [children, setChildren] = useState<Record<string, AgentRun>>({});
  const [runError, setRunError] = useState<string | null>(null);
  const [planningFor, setPlanningFor] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [rejectedPlanning, setRejectedPlanning] = useState<Planning | null>(null);
  // The typed request behind the last plan: Muse plans each message on its own, so an answer to its
  // question goes back together with this request.
  const [sentRequest, setSentRequest] = useState<string | null>(null);
  // How long the model took, per run: the API reports it only when the plan comes back.
  const [planTimes, setPlanTimes] = useState<Record<string, number>>({});
  const [request, setRequest] = useState("");
  const [tab, setTab] = useState<"events" | "chat" | "posts">("events");
  const [postRequest, setPostRequest] = useState<PostRequest | null>(null);
  const makePost = (eventId: string) => {
    setPostRequest({ eventId, nonce: Date.now() });
    setTab("posts");
  };
  const [chatQuestion, setChatQuestion] = useState<{ id: number; text: string } | null>(null);
  const [view, setView] = useState<View>(() => (readPref(VIEW_KEY) === "technical" ? "technical" : "owner"));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [replanning, setReplanning] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [listening, setListening] = useState(false);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const attempted = useRef(new Set<string>());

  const refreshEvents = useCallback(() => {
    listEvents()
      .then(setEvents)
      .catch(() => setEvents([]))
      .finally(() => setEventsLoaded(true));
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

  // Keep refreshing while Muse is working, a supplier / employee hasn't answered yet, or a confirmed
  // order is awaiting delivery (the glasses' delivery check turns it into "Delivered").
  useEffect(() => {
    const active = [run, ...childIds.map((id) => children[id])].filter(Boolean) as AgentRun[];
    const live = active.filter(
      (r) =>
        r.status === "running" ||
        (r.waiting_on && r.waiting_on.length > 0) ||
        r.nodes.some((n) => n.acknowledgement?.tone === "awaiting"),
    );
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

  // Event plan states follow the plan.
  const sig = signature([run, ...childIds.map((id) => children[id])]);
  useEffect(() => {
    if (sig) refreshEvents();
  }, [sig, refreshEvents]);

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
        if (response.run) {
          setChildren((c) => ({ ...c, [response.run!.run_id]: response.run! }));
          setPlanTimes((t) => ({ ...t, [response.run!.run_id]: response.planning.latency_ms }));
        } else setRejectedPlanning(response.planning);
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

  async function plan(start: () => ReturnType<typeof startRun>, forEvent: string | null, query: string | null = null) {
    setSentRequest(query);
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
        setPlanTimes((t) => ({ ...t, [response.run!.run_id]: response.planning.latency_ms }));
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

  // A change asked in chat goes to the normal plan flow (it still waits for the owner's OK).
  function planFromChat(text: string) {
    setTab("events");
    void plan(() => startRun(text), null, text);
  }

  async function handleAsk(event: FormEvent) {
    event.preventDefault();
    const text = request.trim();
    if (!text || planningFor) return;
    const question = rejectedPlanning?.plan.status === "needs_clarification" ? rejectedPlanning.plan.question : null;
    const query = question && sentRequest ? `${sentRequest}\nAnswer to "${question}": ${text}` : text;
    await plan(() => startRun(query), null, query);
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
  // Chat uses the same reference time as the demo event's plan ("today" = its knowledge time).
  const chatAsOf = events.find((e) => e.is_demo && e.knowledge_as_of)?.knowledge_as_of;

  const anyWaiting = run?.status === "awaiting_approval" || waitingChild;
  // Suppliers and staff we're waiting on, in the plan and in any replan that took over.
  const waitingOn = [...new Set([run, ...childIds.map((id) => children[id])].flatMap((r) => r?.waiting_on ?? []))];
  const live: [string, string] = planningFor || run?.status === "running" || replanning
    ? ["working", replanning ? "Finding another way" : "Working"]
    : anyWaiting
      ? ["waiting", "Waiting for you"]
      : waitingOn.length > 0
        ? ["waiting", `Waiting on ${waitingOn.join(" and ")}`]
        : ["watching", "Watching for events"];
  const showPlan = !!run || !!planningFor || !!rejectedPlanning;
  const replanningStep = replanning ? run?.nodes.find((n) => n.node_id === replanning) : undefined;
  const replanBanner =
    run && replanningStep ? replanLine(replanningStep, run, modelName(selectedEvent?.planner_model ?? run.model)) : null;

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
            <div className="mz-muted mz-powered">
              <span className="mz-powered__by">Powered by </span>
              {tab === "events" && run ? modelName(run.model) : "Meta Muse models"}
            </div>
          </div>
          <div className="mz-live" data-live={live[0]} role="status">
            <i />
            <span>{live[1]}</span>
          </div>
          <UpdatesBell className="updates--pane" />
        </header>

        <nav className="mz-tabs" aria-label="Muse">
          <button
            type="button"
            className={`mz-tab${tab === "events" ? " is-on" : ""}`}
            aria-current={tab === "events" ? "page" : undefined}
            onClick={() => {
              setTab("events");
              backToEvents();
            }}
          >
            Plan <span className="mz-tab__n">{events.length}</span>
          </button>
          <button
            type="button"
            className={`mz-tab${tab === "chat" ? " is-on" : ""}`}
            aria-current={tab === "chat" ? "page" : undefined}
            onClick={() => setTab("chat")}
          >
            Ask
          </button>
          <button
            type="button"
            className={`mz-tab${tab === "posts" ? " is-on" : ""}`}
            aria-current={tab === "posts" ? "page" : undefined}
            onClick={() => setTab("posts")}
          >
            Posts
          </button>
        </nav>

        <ChatPanel hidden={tab !== "chat"} asOf={chatAsOf} onPlan={planFromChat} question={chatQuestion} />

        <PostsPanel hidden={tab !== "posts"} events={events} request={postRequest} />

        <div className={`mz-scroll${showPlan ? "" : " mz-scroll--home"}`} hidden={tab !== "events"}>
          {!showPlan && (
            <section className="mz-events" aria-label="Events Muse found">
              <header className="mz-greet">
                <span className="mz-greet__mark" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M5 11h11v3.5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z" />
                    <path d="M16 12h1.5a2.25 2.25 0 0 1 0 4.5H16" />
                    <path d="M10.5 3v4.5M8.25 5.25h4.5" />
                  </svg>
                </span>
                <div>
                  <h2 className="mz-greet__hi">{greeting()}</h2>
                  <p className="mz-greet__sub">Here's what's coming up for {ACTIVE_CAFE_NAME}. Nothing changes without your OK.</p>
                </div>
              </header>
              <p className="mz-label">
                {events.length} {events.length === 1 ? "event" : "events"} coming up
              </p>
              {events.map((event) => {
                const state = event.plan_state ?? "new";
                const [expect, question] = eventPitch(event);
                return (
                  <article key={event.event_id} className={`mz-ev${selectedEvent?.event_id === event.event_id ? " is-sel" : ""}`}>
                    <span className="mz-ev__chip" aria-hidden="true">
                      <b>{fmt(event.starts_at, { weekday: "short" }).toUpperCase()}</b>
                      {eventChip(event)}
                    </span>
                    <div className="mz-ev__main">
                      <h3 className="mz-ev__title">{event.title}</h3>
                      <p className="mz-ev__pitch">
                        {expect}
                        {state === "new" ? ` ${question}` : ""}
                      </p>
                      <p className="mz-ev__meta">{eventLine(event)}</p>
                    </div>
                    <div className="mz-ev__act">
                      {state !== "new" && <span className={`mz-pill is-${PLAN_STATE[state][0]}`}>{PLAN_STATE[state][1]}</span>}
                      {state === "new" ? (
                        <button
                          type="button"
                          className="mz-btn mz-btn--primary mz-btn--lg"
                          disabled={planningFor !== null}
                          onClick={() => plan(() => startEventRun(event.event_id), event.event_id)}
                        >
                          Let's plan ✨
                        </button>
                      ) : (
                        <button type="button" className="mz-btn mz-btn--ghost mz-btn--lg" onClick={() => openEvent(event.event_id)}>
                          Open plan
                        </button>
                      )}
                      {isUpcoming(event) && (
                        <button type="button" className="mz-btn mz-btn--text mz-ev__post" onClick={() => makePost(event.event_id)}>
                          Make a post
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
              {events.length === 0 && <p className="mz-empty">No events coming up. Ask Muse anything below.</p>}
              <div className="mz-quick">
                <p className="mz-label">Or ask Muse</p>
                <div className="mz-chat__ex">
                  {EXAMPLES.map((q) => (
                    <button
                      key={q}
                      type="button"
                      className="mz-chip-btn"
                      onClick={() => {
                        setChatQuestion({ id: Date.now(), text: q });
                        setTab("chat");
                      }}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            </section>
          )}
          {!showPlan && <AtAGlance asOf={chatAsOf} ready={eventsLoaded} />}

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

              {replanBanner && (
                <p className="mz-replan" role="status">
                  <i aria-hidden="true" />
                  {replanBanner}
                </p>
              )}

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
                    <span><i className="w" />Needs your OK or in progress</span>
                    <span><i className="x" />Fell through</span>
                  </div>
                )}
              </div>
              {view === "technical" && selectedStep && (
                <div className="mz-detail">
                  <StepDetail step={selectedStep} onClose={() => setSelectedId(null)} />
                </div>
              )}

              {closing && !story.allSet && <p className="mz-muse-line">{closing}</p>}

              {story.summary.length > 0 && (
                <div className={`mz-card mz-short${story.allSet ? " is-done" : ""}`}>
                  <p className="mz-label">{story.summaryTitle}</p>
                  <ul>
                    {story.summary.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                  {story.allSet && run.event && isUpcoming(run.event) && (
                    <div className="mz-short__next">
                      <span>Spread the word for the {run.event.title.replace(/^Santa Clara /, "").toLowerCase()}?</span>
                      <button type="button" className="mz-btn mz-btn--ghost" onClick={() => makePost(run.event!.event_id)}>
                        Make a post
                      </button>
                    </div>
                  )}
                </div>
              )}

              <p className="mz-foot">
                Planned by {run.model}
                {planTiming(run.attempts, planTimes[run.run_id])}. Every number comes from the café's data, worked out
                in code; nothing changes until you approve.
              </p>
            </section>
          )}
        </div>

        <form className="mz-cmd" onSubmit={handleAsk} hidden={tab !== "events"}>
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
            {chatAsOf && <span className="mz-audit__demo">Demo date: {demoDateLabel(chatAsOf)}</span>}
          </div>
        </form>
      </div>

    </section>
  );
}

/** Shown while Muse replans, until the new plan is ready (the model call itself is not delayed). */
function replanLine(step: RunStep, run: AgentRun, model: string): string {
  if (step.action === "schedule_shift") {
    const args = (step.resolved_args ?? step.args) as Record<string, unknown>;
    const who = employeeName(run, String(args.employee_id)).split(" ")[0];
    return `${who} declined. Muse is looking for someone else… · ${model}`;
  }
  const supplier = String((step.result as Record<string, unknown> | null)?.supplier_name ?? "The supplier");
  return `${supplier} declined. Muse is looking for another supplier… · ${model}`;
}

/** "in 27 s", or "after 2 attempts; the last took 27 s": latency_ms covers the final model call only. */
function planTiming(attempts: number, latencyMs: number | undefined): string {
  const took = latencyMs !== undefined ? Math.round(latencyMs / 1000) : null;
  if (attempts > 1) return ` after ${attempts} attempts${took !== null ? `; the last took ${took} s` : ""}`;
  return took !== null ? ` in ${took} s` : "";
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
        <p className="mz-muted">Type your answer below. Muse gets it together with your request.</p>
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
