import { Link } from "react-router-dom";
import type { CafeStatus, InventoryRow, PlanState } from "../../api/agent";

function amount(value: string | null, unit: string): string {
  if (value === null) return "—";
  const n = Number(value);
  if (unit === "mL") return `${(n / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} L`;
  if (unit === "g") return `${(n / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} kg`;
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

const FLAG: Record<InventoryRow["status"], (row: InventoryRow) => [string, string] | null> = {
  short: (row) => ["wait", `Short ${amount(row.shortfall, row.unit)}`],
  ordered: (row) => (row.on_order_status === "confirmed" ? ["done", "Order confirmed"] : ["wait", "Ordered"]),
  unknown: () => ["neutral", "Count needed"],
  covered: () => null,
};

export const PLAN_STATE: Record<PlanState, [string, string]> = {
  new: ["neutral", "New"],
  checking: ["run", "Checking"],
  needs_ok: ["wait", "Needs OK"],
  waiting: ["wait", "Waiting on reply"],
  planned: ["done", "Planned"],
  issues: ["no", "Needs attention"],
};

type CafeStatusPanelProps = {
  status: CafeStatus | null;
  error: string | null;
  highlight: "none" | "running" | "waiting";
  onOpenEvent: (eventId: string) => void;
};

function CafeStatusPanel({ status, error, highlight, onOpenEvent }: CafeStatusPanelProps) {
  const hl = highlight === "running" ? " is-hl" : highlight === "waiting" ? " is-hl-warn" : "";
  const updated = status
    ? new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" }).format(
        new Date(status.as_of),
      )
    : null;
  return (
    <section className="mz-status" aria-label="Café status">
      <header className="mz-pane-head">
        <span className="mz-label">Café status</span>
        {updated && <span className="mz-muted mz-pane-head__right">Read-only · as of {updated}</span>}
      </header>
      <div className="mz-status__scroll">
        {error && <p className="mz-error">{error}</p>}
        {!status && !error && <p className="mz-muted">Loading the café…</p>}
        {status && (
          <>
            <div>
              <h2 className="mz-greeting">{status.greeting}</h2>
              <p className="mz-muted">{status.date_label}</p>
            </div>

            <div className="mz-kpis">
              {(["stock", "coverage", "approvals", "sales"] as const).map((key) => {
                const kpi = status.kpis[key];
                if (!kpi) return null;
                const lit = key !== "sales" && kpi.tone === "warn" ? hl : "";
                return (
                  <div key={key} className={`mz-card mz-kpi${lit}`}>
                    <div className="mz-kpi__label">{kpi.label}</div>
                    <div className="mz-kpi__value">
                      {kpi.value} {kpi.of && <small>{kpi.of}</small>}
                    </div>
                    <div className={`mz-kpi__note is-${kpi.tone}`}>{kpi.note}</div>
                  </div>
                );
              })}
            </div>

            {status.inventory.length > 0 && (
              <div>
                <div className="mz-sec-head">
                  <span className="mz-label">Stock for {status.focus_event_name}</span>
                  <Link className="mz-link" to="/inventory?tab=stock">
                    View all
                  </Link>
                </div>
                <div className="mz-card mz-inv">
                  {status.inventory.map((row) => {
                    const flag = FLAG[row.status](row);
                    const have = Number(row.on_hand ?? 0) + Number(row.arriving);
                    const need = Math.max(Number(row.needed), 1);
                    const pct = Math.min(100, (have / need) * 100);
                    const ordered = Math.min(100 - pct, (Number(row.on_order) / need) * 100);
                    return (
                      <div key={row.ingredient_id} className={`mz-inv__row${row.status === "short" ? hl : ""}`}>
                        <div>
                          <div className="mz-inv__name">{row.name}</div>
                          <div className="mz-inv__sub">{row.note}</div>
                        </div>
                        <div className="mz-bar" aria-hidden="true">
                          <i className={row.status === "short" ? "low" : ""} style={{ width: `${pct}%` }} />
                          {ordered > 0 && <b style={{ left: `${pct}%`, width: `${ordered}%` }} />}
                        </div>
                        <div className="mz-inv__qty">
                          {amount(row.on_hand, row.unit)}
                          <span> on hand</span>
                        </div>
                        <div className="mz-inv__flag">{flag && <span className={`mz-pill is-${flag[0]}`}>{flag[1]}</span>}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            <div>
              <div className="mz-sec-head">
                <span className="mz-label">Staff coverage · next 3 days</span>
                <Link className="mz-link" to="/staffing">
                  Schedule
                </Link>
              </div>
              <div className="mz-cover">
                {status.coverage.map((day) => {
                  const short = day.halves.some((h) => h.staffed < h.required);
                  return (
                    <div key={day.date} className={`mz-card mz-day${day.is_event_day && short ? hl : ""}`}>
                      <div className="mz-day__top">
                        <span className="mz-day__name">{day.label}</span>
                        <span className="mz-muted">
                          {day.shifts} {day.shifts === 1 ? "shift" : "shifts"}
                        </span>
                      </div>
                      {day.halves.map((half) => (
                        <div key={half.label} className="mz-shift">
                          <span>{half.label}</span>
                          <span className="mz-pips" aria-hidden="true">
                            {Array.from({ length: half.required }, (_, i) => (
                              <i key={i} className={i < half.staffed ? "on" : "gap"} />
                            ))}
                          </span>
                          <span className="n">
                            {Math.min(half.staffed, half.required)}/{half.required}
                          </span>
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            </div>

            <div>
              <div className="mz-sec-head">
                <span className="mz-label">Upcoming events</span>
              </div>
              <div className="mz-card mz-events">
                {status.events.map((event) => {
                  const [tone, label] = PLAN_STATE[event.plan_state];
                  const start = new Date(event.starts_at);
                  const day = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "America/Los_Angeles" }).format(start);
                  const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/Los_Angeles" }).format(start);
                  return (
                    <button key={event.event_id} type="button" className="mz-ev-row" onClick={() => onOpenEvent(event.event_id)}>
                      <span className="mz-ev-row__date">
                        <b>{day}</b>
                        {date}
                      </span>
                      <span className="mz-ev-row__main">
                        <span className="mz-ev-row__title">{event.name}</span>
                        <span className="mz-ev-row__sub">{event.note}</span>
                      </span>
                      <span className={`mz-pill is-${tone}`}>{label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export default CafeStatusPanel;
