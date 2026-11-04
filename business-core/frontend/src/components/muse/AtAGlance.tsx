import { useEffect, useState } from "react";
import { type Glance, type GlanceRow, getGlance } from "../../api/agent";

// Raw record statuses (e.g. "partially_received") read as plain words; the backend value is unchanged.
const STATUS_WORDS: Record<string, string> = {
  partially_received: "Partly received",
  awaiting_approval: "Waiting for your OK",
  short_shipped: "Back-ordered",
  needs_count: "Needs a count",
  not_on_order: "Not on the order",
};

export function friendlyStatus(text: string): string {
  return text.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (raw) => {
    if (STATUS_WORDS[raw]) return STATUS_WORDS[raw];
    const words = raw.replace(/_/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  });
}

/** "Sun Nov 8, 6 AM · Willow Ridge Dairy Supply PO-123, confirmed" → "Sun Nov 8, 6 AM · Willow Ridge Dairy · PO-123";
 * any other status stays, in plain words ("… · PO-123 · Partly received"). */
export function shortDetail(text: string): string {
  const m = text.match(/^(.*? · )(.+?)(?: Supply)?\s+(PO-\d+),\s*([a-z_]+)$/);
  if (!m) return friendlyStatus(text);
  const [, when, supplier, po, status] = m;
  const word = friendlyStatus(status);
  return `${when}${supplier} · ${po}${status === "confirmed" ? "" : ` · ${word.charAt(0).toUpperCase()}${word.slice(1)}`}`;
}

function Card({ title, sub, rows, empty, tone }: { title: string; sub?: string; rows: GlanceRow[]; empty: string; tone?: "warn" }) {
  return (
    <section className={`mz-card mz-glance__card${tone ? ` is-${tone}` : ""}`} aria-label={sub ? `${title}, ${sub}` : title}>
      <h3 className="mz-glance__title">
        {title}
        {sub && <span className="mz-glance__sub">{sub}</span>}
      </h3>
      {rows.length === 0 ? (
        <p className="mz-glance__empty">{empty}</p>
      ) : (
        <ul>
          {rows.map((row) => (
            <li key={`${row.name}-${row.text}`}>
              <span className="mz-glance__name">{row.name}</span>
              <span className="mz-glance__text">{friendlyStatus(row.text)}</span>
              {row.detail && <span className="mz-glance__detail">{shortDetail(row.detail)}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Plan home, right column: three small cards from the café's records (nothing estimated here). */
function AtAGlance({ asOf, ready }: { asOf?: string; ready: boolean }) {
  const [glance, setGlance] = useState<Glance | null>(null);

  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    getGlance(asOf, controller.signal)
      .then((g) => setGlance(Array.isArray(g?.running_low) && Array.isArray(g.coming_in) && Array.isArray(g.working) ? g : null))
      .catch(() => setGlance(null));
    return () => controller.abort();
  }, [asOf, ready]);

  if (!glance) return null;
  return (
    <aside className="mz-glance" aria-label="At a glance">
      <p className="mz-label">At a glance</p>
      <Card title="Running low" rows={glance.running_low} empty="Nothing is below its reorder point." tone="warn" />
      <Card title="Coming in" rows={glance.coming_in} empty="No deliveries in the next 3 days." />
      <Card title={glance.working_label} sub={glance.working_day} rows={glance.working} empty="No shifts this week." />
    </aside>
  );
}

export default AtAGlance;
