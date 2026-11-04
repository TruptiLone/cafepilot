import type { LineStatus, ReceiptLine } from "../api/receiving";

/** What a line asks of the owner: nothing (match), a look (check), or a count (count). */
export type Need = "match" | "check" | "count";
export type Tone = "good" | "ask" | "bad" | "wait";

const STATUS_LABEL: Record<LineStatus, string> = {
  match: "Matches",
  needs_count: "Please count",
  short: "Short",
  short_shipped: "Back-ordered",
  over: "Too many",
  not_on_order: "Not ordered",
  missing: "Missing",
  damaged: "Damaged",
};

export function units(quantity: string | null, unitSize: string | null): number | null {
  if (quantity == null) return null;
  const size = Number(unitSize ?? 1) || 1;
  return Math.round(Number(quantity) / size);
}

export type LineView = {
  ordered: number | null;
  slip: number | null;
  /** Units Muse saw; null when it couldn't count them (shown as "?"). */
  seen: number | null;
  counted: number | null;
  need: Need;
  tone: Tone;
  badge: string;
};

/** Everything a row shows, worked out from the delivery-check data (never from Muse's wording). */
export function lineView(line: ReceiptLine, photoRead = true): LineView {
  const ordered = units(line.ordered_quantity, line.unit_size);
  const slip = units(line.slip_shipped_quantity, line.unit_size);
  const counted = units(line.counted_quantity, line.unit_size);
  const unsure = line.status === "needs_count";
  const raw = units(line.seen_quantity, line.unit_size);
  // An unsure zero means Muse couldn't count it, not that nothing came.
  const seen = raw == null || (unsure && raw === 0) ? null : raw;
  const base = { ordered, slip, seen, counted };

  if (line.status === "match") return { ...base, need: "match", tone: "good", badge: STATUS_LABEL.match };
  if (!unsure) return { ...base, need: "check", tone: "bad", badge: STATUS_LABEL[line.status] };
  // Slip read, no delivery photo yet: nothing to count against so far.
  if (!photoRead && seen == null) return { ...base, need: "count", tone: "wait", badge: "Waiting for photo" };
  if (seen == null) return { ...base, need: "count", tone: "bad", badge: STATUS_LABEL.needs_count };
  const expected = slip ?? ordered;
  if (line.seen_may_hide_more && expected != null && expected > seen) {
    return { ...base, need: "check", tone: "ask", badge: `${expected - seen} may be hidden` };
  }
  return { ...base, need: "count", tone: "ask", badge: STATUS_LABEL.needs_count };
}

export function tally(lines: ReceiptLine[]): Record<Need, number> {
  const counts: Record<Need, number> = { match: 0, check: 0, count: 0 };
  for (const line of lines) counts[lineView(line).need] += 1;
  return counts;
}

/** "Coffee Beans 1 · Whole Milk 3 · Butter Croissants not counted", from the lines. */
export function seenSummary(lines: ReceiptLine[]): string {
  return lines
    .map((line) => {
      const { seen } = lineView(line);
      const name = line.description.split(",")[0];
      return seen == null ? `${name} not counted` : `${name} ${seen}`;
    })
    .join(" · ");
}
