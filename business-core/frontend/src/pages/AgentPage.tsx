import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  type AgentRun,
  type DetectedEvent,
  type Planning,
  type RunSummary,
  AgentApiError,
  getRun,
  listEvents,
  listRuns,
  startEventRun,
  startRun,
} from "../api/agent";
import ApprovalPanel from "../components/agent/ApprovalPanel";
import OutcomeList from "../components/agent/OutcomeList";
import StepDetail from "../components/agent/StepDetail";
import TicketRail from "../components/agent/TicketRail";
import { RUN_STATUS_LABELS, runStatusLabel } from "../components/agent/labels";

const EXAMPLE_REQUEST =
  "Farmers market this Sunday Nov 1, 8 AM-2 PM. Expect about 40 extra whole-milk lattes and 20 extra " +
  "oat-milk lattes. Using stock as of Thursday 6 PM, order milk for a 6 AM Sunday delivery if we'll " +
  "run short, and add baristas for any hours below our event staffing target.";

const POLL_MS = 2000;

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
  }).format(new Date(value));
}

function errorMessage(error: unknown): string {
  return error instanceof AgentApiError ? error.message : "Something went wrong. Try again.";
}

function AgentPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const [request, setRequest] = useState("");
  const [isPlanning, setIsPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [rejectedPlanning, setRejectedPlanning] = useState<Planning | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<DetectedEvent[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    listEvents(controller.signal)
      .then(setEvents)
      .catch(() => setEvents([]));
    return () => controller.abort();
  }, []);

  const refreshRecent = useCallback(() => {
    listRuns()
      .then(setRecent)
      .catch(() => setRecent([]));
  }, []);

  useEffect(refreshRecent, [refreshRecent]);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    if (run?.run_id === runId) return;
    const controller = new AbortController();
    setRunError(null);
    getRun(runId, controller.signal)
      .then(setRun)
      .catch((error) => {
        if (!controller.signal.aborted) setRunError(errorMessage(error));
      });
    return () => controller.abort();
  }, [runId, run?.run_id]);

  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = window.setTimeout(() => {
      getRun(run.run_id).then(setRun).catch(() => undefined);
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [run]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const query = request.trim();
    if (!query || isPlanning) return;
    await plan(() => startRun(query));
  }

  async function plan(start: () => ReturnType<typeof startRun>) {
    setIsPlanning(true);
    setPlanError(null);
    setRejectedPlanning(null);
    setSelectedId(null);
    try {
      const response = await start();
      if (response.run) {
        setRun(response.run);
        setSearchParams({ run: response.run.run_id });
      } else {
        setRejectedPlanning(response.planning);
      }
      refreshRecent();
    } catch (error) {
      setPlanError(errorMessage(error));
    } finally {
      setIsPlanning(false);
    }
  }

  const selected = run?.nodes.find((step) => step.node_id === selectedId) ?? null;
  const waiting = run?.nodes.filter((step) => step.status === "awaiting_approval").length ?? 0;

  return (
    <section className="page page--agent" aria-labelledby="page-title">
      <div className="page__heading">
        <div>
          <h1 id="page-title">Ask CaféPilot</h1>
          <p className="page__description">
            Describe what's coming up. CaféPilot checks stock, sales and staff, lays out the
            steps, and waits for your OK before changing anything.
          </p>
        </div>
      </div>

      {events.length > 0 && (
        <section className="event-strip" aria-labelledby="events-title">
          <h2 id="events-title">
            Muse found {events.length} {events.length === 1 ? "event" : "events"} coming up
          </h2>
          <ul>
            {events.map((item) => (
              <li key={item.event_id} className="event-strip__card">
                <p className="event-strip__message">{item.muse_message}</p>
                <p className="event-strip__basis">
                  {item.expected.basis} Source: {item.source}.
                </p>
                <button
                  type="button"
                  className="primary-button"
                  disabled={isPlanning}
                  onClick={() => plan(() => startEventRun(item.event_id))}
                >
                  Yes, check
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <form className="ask-bar" onSubmit={handleSubmit}>
        <label className="ask-bar__label" htmlFor="agent-request">
          What do you need?
        </label>
        <textarea
          id="agent-request"
          className="ask-bar__input"
          rows={3}
          value={request}
          placeholder="For example: a catering order for 25 lattes on Friday at 9 AM. Do we have enough milk?"
          onChange={(event) => setRequest(event.target.value)}
          disabled={isPlanning}
        />
        <div className="ask-bar__actions">
          <button
            type="button"
            className="text-button"
            onClick={() => setRequest(EXAMPLE_REQUEST)}
            disabled={isPlanning}
          >
            Use the farmers market example
          </button>
          <button type="submit" className="primary-button" disabled={isPlanning || !request.trim()}>
            {isPlanning ? "Planning…" : "Make a plan"}
          </button>
        </div>
        {isPlanning && (
          <p className="ask-bar__hint" role="status">
            Reading your café's data and planning the steps. This usually takes 10 to 20 seconds.
          </p>
        )}
        {planError && (
          <p className="ask-bar__error" role="alert">
            {planError}
          </p>
        )}
      </form>

      {rejectedPlanning && <PlanningNotice planning={rejectedPlanning} />}

      {runError && (
        <p className="ask-bar__error" role="alert">
          {runError}
        </p>
      )}

      {run && (
        <div className="run-view">
          <header className="run-view__header">
            <div>
              <p className="run-view__request">“{run.query}”</p>
              <h2 className="run-view__goal">{run.goal}</h2>
            </div>
            <span className={`run-status run-status--${runStatusLabel(run).tone}`}>
              {runStatusLabel(run).label}
              {waiting > 0 && `: ${waiting} ${waiting === 1 ? "change" : "changes"}`}
            </span>
          </header>

          {run.assumptions.length > 0 && (
            <div className="run-view__assumptions">
              <h3>Assumed for this plan</h3>
              <ul>
                {run.assumptions.map((assumption) => (
                  <li key={assumption}>{assumption}</li>
                ))}
              </ul>
            </div>
          )}

          {run.closing_message && (
            <p className="run-view__closing" role="status">
              {run.closing_message}
            </p>
          )}

          <ApprovalPanel
            run={run}
            onUpdated={(updated) => {
              setRun(updated);
              refreshRecent();
            }}
            onShowStep={setSelectedId}
          />
          <OutcomeList run={run} />

          <div className={`run-view__body${selected ? " has-detail" : ""}`}>
            <TicketRail run={run} selectedId={selectedId} onSelect={setSelectedId} />
            {selected && <StepDetail step={selected} onClose={() => setSelectedId(null)} />}
          </div>

          <p className="run-view__footnote">
            Planned {formatTime(run.created_at)} by {run.model}
            {run.attempts > 1 ? ` after ${run.attempts} attempts` : ""}. Each step shows the date its
            data is from.
          </p>
        </div>
      )}

      {recent.length > 0 && (
        <section className="recent-runs" aria-labelledby="recent-runs-title">
          <h2 id="recent-runs-title">Recent plans</h2>
          <ul>
            {recent.map((item) => (
              <li key={item.run_id}>
                <button
                  type="button"
                  className={`recent-runs__item${item.run_id === runId ? " is-current" : ""}`}
                  onClick={() => {
                    setSelectedId(null);
                    setSearchParams({ run: item.run_id });
                  }}
                >
                  <span className="recent-runs__goal">{item.goal}</span>
                  <span className="recent-runs__meta">
                    {formatTime(item.created_at)},{" "}
                    {item.awaiting_approval > 0
                      ? `${item.awaiting_approval} ${item.awaiting_approval === 1 ? "change" : "changes"} waiting for you`
                      : RUN_STATUS_LABELS[item.status].toLowerCase()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

function PlanningNotice({ planning }: { planning: Planning }) {
  const { plan, validation } = planning;
  if (plan.status === "needs_clarification") {
    return (
      <div className="planning-notice" role="status">
        <h2>One question first</h2>
        <p>{plan.question}</p>
        <p className="planning-notice__hint">Add the answer to your request and ask again.</p>
      </div>
    );
  }
  if (plan.status === "out_of_scope") {
    return (
      <div className="planning-notice" role="status">
        <h2>That's outside what CaféPilot handles</h2>
        <p>Ask about stock, sales, staff, deliveries or supplier orders.</p>
      </div>
    );
  }
  return (
    <div className="planning-notice planning-notice--rejected" role="alert">
      <h2>The plan didn't pass CaféPilot's safety checks</h2>
      <p>Nothing was run. The checks found:</p>
      <ul>
        {validation.issues.map((issue, index) => (
          <li key={index}>{issue.message}</li>
        ))}
      </ul>
      <p className="planning-notice__hint">Try rephrasing the request with dates and quantities.</p>
    </div>
  );
}

export default AgentPage;
