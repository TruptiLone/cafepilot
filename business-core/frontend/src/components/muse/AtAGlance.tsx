import { useEffect, useState } from "react";
import { type Glance, type GlanceRow, getGlance } from "../../api/agent";

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
              <span className="mz-glance__text">{row.text}</span>
              {row.detail && <span className="mz-glance__detail">{row.detail}</span>}
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
