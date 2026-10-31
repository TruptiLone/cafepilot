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
  needs_ok: ["wait", "Needs your OK"],
  waiting: ["wait", "Waiting on reply"],
  planned: ["done", "Planned"],
  issues: ["no", "Needs attention"],
};

type CafeStatusPanelProps = {
  status: CafeStatus | null;
  error: string | null;
  highlight: "none" | "running" | "waiting";
  collapsed: boolean;
  onToggle: () => void;
};

const SHORT_LABEL: Record<string, string> = { stock: "short", coverage: "cover", approvals: "to approve", sales: "today" };

function CafeStatusPanel({ status, error, highlight, collapsed, onToggle }: CafeStatusPanelProps) {
  const hl = highlight === "running" ? " is-hl" : highlight === "waiting" ? " is-hl-warn" : "";
  const keys = ["stock", "coverage", "approvals", "sales"] as const;

  if (collapsed) {
    return (
      <section className="mz-status mz-status--mini" aria-label="Café status">
        <button type="button" className="mz-fold" aria-label="Show café status" onClick={onToggle}>
          ⇤
        </button>
        {status &&
          keys.map((key) => {
            const kpi = status.kpis[key];
            if (!kpi) return null;
            return (
              <div key={key} className={`mz-badge${kpi.tone === "warn" ? " is-warn" : ""}`}>
                <b>
                  {kpi.value}
                  {kpi.of ? kpi.of.replace(" ", "") : ""}
                </b>
                {SHORT_LABEL[key]}
              </div>
            );
          })}
      </section>
    );
  }

  const updated = status
    ? new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" }).format(new Date(status.as_of))
    : null;
  const attention = status?.inventory.filter((r) => r.status !== "covered") ?? [];
  const covered = (status?.inventory.length ?? 0) - attention.length;

  return (
    <section className="mz-status" aria-label="Café status">
      <header className="mz-pane-head">
        <span className="mz-label">Café status</span>
        {updated && <span className="mz-muted mz-pane-head__right">as of {updated}</span>}
        <button type="button" className="mz-fold" aria-label="Hide café status" onClick={onToggle}>
          ⇥
        </button>
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
              {keys.map((key) => {
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
                  {attention.map((row) => {
                    const flag = FLAG[row.status](row);
                    return (
                      <div key={row.ingredient_id} className={`mz-inv__row${row.status === "short" ? hl : ""}`}>
                        <div>
                          <div className="mz-inv__name">{row.name}</div>
                          <div className="mz-inv__sub">{row.note}</div>
                        </div>
                        <div className="mz-inv__qty">
                          {amount(row.on_hand, row.unit)}
                          <span> on hand</span>
                        </div>
                        <div className="mz-inv__flag">{flag && <span className={`mz-pill is-${flag[0]}`}>{flag[1]}</span>}</div>
                      </div>
                    );
                  })}
                  {covered > 0 && (
                    <div className="mz-inv__row mz-inv__row--ok">
                      <span className="mz-muted">
                        {covered} more {covered === 1 ? "item" : "items"} covered
                      </span>
                      <span className="mz-pill is-done">OK</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div>
              <div className="mz-sec-head">
                <span className="mz-label">Staff cover · next 3 days</span>
                <Link className="mz-link" to="/staffing">
                  Schedule
                </Link>
              </div>
              <div className="mz-card mz-cover-list">
                {status.coverage.map((day) => {
                  const short = day.halves.some((h) => h.staffed < h.required);
                  return (
                    <div key={day.date} className={`mz-cover-line${day.is_event_day && short ? " is-short" : ""}`}>
                      <b>{day.label}</b>
                      <span className="mz-pips" aria-hidden="true">
                        {day.halves.map((half) => (
                          <span key={half.label}>
                            {Array.from({ length: half.required }, (_, i) => (
                              <i key={i} className={i < half.staffed ? "on" : "gap"} />
                            ))}
                          </span>
                        ))}
                      </span>
                      <span className="mz-muted">
                        {day.halves.map((h) => `${h.label} ${Math.min(h.staffed, h.required)}/${h.required}`).join(" · ")}
                      </span>
                    </div>
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
