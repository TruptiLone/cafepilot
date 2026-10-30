import { useEffect, useState } from "react";
import {
  confirmReceipt,
  countLine,
  imageUrl,
  listOpenOrders,
  readPhoto,
  readSlip,
  sendCreditRequest,
  startReceipt,
  type ImageSource,
  type LineStatus,
  type OpenOrder,
  type Receipt,
  type ReceiptLine,
} from "../api/receiving";

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

const STATUS_TONE: Record<LineStatus, "good" | "ask" | "bad"> = {
  match: "good",
  needs_count: "ask",
  short: "bad",
  short_shipped: "bad",
  over: "bad",
  not_on_order: "bad",
  missing: "bad",
  damaged: "bad",
};

function when(iso: string | null) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
  });
}

function units(quantity: string | null, unitSize: string | null): number | null {
  if (quantity == null) return null;
  const size = Number(unitSize ?? 1) || 1;
  return Math.round(Number(quantity) / size);
}

type Busy = null | "start" | "slip" | "photo" | "count" | "confirm" | "credit";

const BUSY_TEXT: Record<Exclude<Busy, null>, string> = {
  start: "Opening the delivery check…",
  slip: "Muse is reading the packing slip…",
  photo: "Muse is counting what it can see…",
  count: "Comparing your count…",
  confirm: "Adding to stock…",
  credit: "Sending the credit request…",
};

function ReceivingPage() {
  const [orders, setOrders] = useState<OpenOrder[] | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  const loadOrders = () => {
    listOpenOrders()
      .then(setOrders)
      .catch((e: Error) => setError(e.message));
  };

  useEffect(loadOrders, []);

  async function run(kind: Exclude<Busy, null>, action: () => Promise<Receipt>) {
    setBusy(kind);
    setError(null);
    try {
      setReceipt(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  const reset = () => {
    setReceipt(null);
    setError(null);
    loadOrders();
  };

  return (
    <section className="page page--receiving" aria-labelledby="page-title">
      <div className="rcv">
        <header className="rcv-head">
          <p className="page__eyebrow">Receiving</p>
          <h1 id="page-title">Check a delivery</h1>
          <p className="page__description">
            Look at the packing slip, then the delivery. Muse reads both; only what you accept goes into stock.
          </p>
        </header>

        {error && (
          <p className="rcv-error" role="alert">
            {error}
          </p>
        )}

        {!receipt ? (
          <ExpectedDeliveries
            orders={orders}
            busy={busy === "start"}
            onStart={(orderId) => run("start", () => startReceipt(orderId))}
          />
        ) : (
          <DeliveryCheck receipt={receipt} busy={busy} run={run} onDone={reset} />
        )}
      </div>
    </section>
  );
}

function ExpectedDeliveries({
  orders,
  busy,
  onStart,
}: {
  orders: OpenOrder[] | null;
  busy: boolean;
  onStart: (orderId: string | null) => void;
}) {
  if (orders === null) return <p className="rcv-muted">Loading expected deliveries…</p>;
  return (
    <div className="rcv-stack">
      <h2 className="rcv-section-title">Expected deliveries</h2>
      {orders.length === 0 && <p className="rcv-muted">No confirmed orders are waiting to arrive.</p>}
      {orders.map((order) => (
        <article className="rcv-card rcv-order" key={order.order_id}>
          <div className="rcv-order__top">
            <div>
              <p className="rcv-order__supplier">{order.supplier_name}</p>
              <p className="rcv-muted">
                {order.po_number ?? order.order_id} · arriving {when(order.confirmed_delivery_at ?? order.requested_delivery_at)}
              </p>
            </div>
            {order.status === "partially_received" && <span className="rcv-chip rcv-chip--ask">Part received</span>}
          </div>
          <ul className="rcv-order__lines">
            {order.lines.map((line) => (
              <li key={line.line_no}>
                <span>{line.description ?? line.ingredient_id}</span>
                <span className="rcv-muted">
                  {line.packs != null ? `${line.packs} × ${line.pack_name}` : `${line.quantity} ${line.unit}`}
                </span>
              </li>
            ))}
          </ul>
          <button className="rcv-button rcv-button--primary" type="button" disabled={busy} onClick={() => onStart(order.order_id)}>
            Check this delivery
          </button>
        </article>
      ))}
      <button className="rcv-button rcv-button--ghost" type="button" disabled={busy} onClick={() => onStart(null)}>
        Something arrived that isn't listed
      </button>
    </div>
  );
}

function DeliveryCheck({
  receipt,
  busy,
  run,
  onDone,
}: {
  receipt: Receipt;
  busy: Busy;
  run: (kind: Exclude<Busy, null>, action: () => Promise<Receipt>) => Promise<void>;
  onDone: () => void;
}) {
  const id = receipt.receipt_id;
  const confirmed = receipt.status === "confirmed";
  const hasSlip = receipt.slip_number != null || receipt.has_slip_image;
  const step = confirmed ? 4 : !hasSlip ? 0 : !receipt.has_photo_image ? 1 : receipt.summary.needs_count ? 2 : 3;

  return (
    <div className="rcv-stack">
      <ol className="rcv-steps" aria-label="Progress">
        {["Slip", "Delivery", "Count", "Confirm"].map((label, i) => (
          <li key={label} className={i < step ? "is-done" : i === step ? "is-current" : ""}>
            <span className="rcv-steps__dot" aria-hidden="true" />
            {label}
          </li>
        ))}
      </ol>

      <MuseSays text={busy ? BUSY_TEXT[busy] : receipt.muse_message} thinking={busy != null} />

      <Capture
        title="Packing slip"
        done={hasSlip}
        busy={busy === "slip"}
        disabled={busy != null || confirmed}
        image={receipt.has_slip_image ? imageUrl(id, "slip") : null}
        detail={
          hasSlip
            ? [
                receipt.slip_number && `Slip ${receipt.slip_number}`,
                receipt.slip_supplier,
                receipt.order?.po_number
                  ? `matches ${receipt.order.po_number}`
                  : receipt.slip_po_number
                    ? `${receipt.slip_po_number} — no matching order`
                    : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : "Point your glasses or phone at the slip."
        }
        onCapture={(source) => run("slip", () => readSlip(id, source))}
      />

      <Capture
        title="The delivery"
        done={receipt.has_photo_image}
        busy={busy === "photo"}
        disabled={busy != null || confirmed || !hasSlip}
        image={receipt.has_photo_image ? imageUrl(id, "photo") : null}
        detail={
          receipt.has_photo_image
            ? receipt.photo_notes ?? "Muse counted what it could see."
            : hasSlip
              ? "Now look at the cases and crates that came in."
              : "After the slip."
        }
        onCapture={(source) => run("photo", () => readPhoto(id, source))}
      />

      {receipt.lines.length > 0 && (
        <div className="rcv-stack">
          <h2 className="rcv-section-title">
            What arrived
            <span className="rcv-muted">
              {receipt.summary.match} of {receipt.summary.lines} match
            </span>
          </h2>
          {receipt.lines.map((line) => (
            <LineCard
              key={line.line_no}
              line={line}
              locked={confirmed || busy != null}
              onCount={(counted, rejected, reason) => run("count", () => countLine(id, line.line_no, counted, rejected, reason))}
            />
          ))}
        </div>
      )}

      {!confirmed && receipt.lines.length > 0 && (
        <div className="rcv-confirm">
          <p className="rcv-muted">
            {receipt.summary.needs_count
              ? `Count ${receipt.summary.needs_count === 1 ? "1 line" : `${receipt.summary.needs_count} lines`} before adding to stock.`
              : receipt.summary.problems
                ? "Only what's here goes into stock. Muse drafts a credit request for the rest."
                : "Everything matches the slip."}
          </p>
          <button
            className="rcv-button rcv-button--primary"
            type="button"
            disabled={busy != null || receipt.summary.needs_count > 0 || !receipt.order}
            onClick={() => run("confirm", () => confirmReceipt(id))}
          >
            Approve and add to stock
          </button>
        </div>
      )}

      {confirmed && <Done receipt={receipt} busy={busy} run={run} onDone={onDone} />}
    </div>
  );
}

function MuseSays({ text, thinking }: { text: string; thinking: boolean }) {
  return (
    <div className={`rcv-muse${thinking ? " is-thinking" : ""}`} aria-live="polite">
      <span className="rcv-muse__orb" aria-hidden="true" />
      <div>
        <p className="rcv-muse__name">Muse</p>
        <p className="rcv-muse__text">{text}</p>
      </div>
    </div>
  );
}

function Capture({
  title,
  done,
  busy,
  disabled,
  image,
  detail,
  onCapture,
}: {
  title: string;
  done: boolean;
  busy: boolean;
  disabled: boolean;
  image: string | null;
  detail: string;
  onCapture: (source: ImageSource) => void;
}) {
  return (
    <article className={`rcv-card rcv-capture${done ? " is-done" : ""}${busy ? " is-busy" : ""}`}>
      <div className="rcv-capture__thumb">
        {image ? <img src={image} alt={title} /> : <span aria-hidden="true">{busy ? "…" : "＋"}</span>}
      </div>
      <div className="rcv-capture__body">
        <p className="rcv-capture__title">
          {title}
          {done && <span className="rcv-chip rcv-chip--good">Read</span>}
        </p>
        <p className="rcv-muted">{detail}</p>
        {!done && (
          <div className="rcv-capture__actions">
            <button className="rcv-button rcv-button--small" type="button" disabled={disabled} onClick={() => onCapture({ demo: true })}>
              Use demo photo
            </button>
            <label className={`rcv-button rcv-button--small rcv-button--ghost${disabled ? " is-disabled" : ""}`}>
              Take or upload
              <input
                type="file"
                accept="image/*"
                capture="environment"
                disabled={disabled}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onCapture({ file });
                  event.target.value = "";
                }}
              />
            </label>
          </div>
        )}
      </div>
    </article>
  );
}

function LineCard({
  line,
  locked,
  onCount,
}: {
  line: ReceiptLine;
  locked: boolean;
  onCount: (counted: number, rejected: number, reason?: string) => void;
}) {
  const seen = units(line.seen_quantity, line.unit_size);
  const expected = units(line.slip_shipped_quantity ?? line.ordered_quantity, line.unit_size);
  const counted = units(line.counted_quantity, line.unit_size);
  const [count, setCount] = useState<number>(counted ?? seen ?? expected ?? 0);
  const asksForCount = line.status === "needs_count" && line.seen_quantity != null;
  const [open, setOpen] = useState(asksForCount);

  useEffect(() => {
    if (asksForCount) setOpen(true);
    setCount(counted ?? seen ?? expected ?? 0);
  }, [asksForCount, counted, seen, expected]);

  const rows: [string, string | undefined][] = [
    ["Ordered", line.display.ordered],
    ["Slip says", line.display.shipped],
    [line.seen_may_hide_more ? "Muse sees (may hide more)" : "Muse sees", line.display.seen],
    ["You counted", line.display.counted],
    ["Into stock", line.display.accepted],
  ];

  return (
    <article className={`rcv-card rcv-line rcv-line--${STATUS_TONE[line.status]}`}>
      <div className="rcv-line__top">
        <div>
          <p className="rcv-line__name">{line.description}</p>
          {line.supplier_item_number && <p className="rcv-muted">{line.supplier_item_number}</p>}
        </div>
        <span className={`rcv-chip rcv-chip--${STATUS_TONE[line.status]}`}>{STATUS_LABEL[line.status]}</span>
      </div>
      <dl className="rcv-line__facts">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      <p className="rcv-line__message">{line.message}</p>

      {!locked &&
        (open ? (
          <div className="rcv-count">
            <span className="rcv-count__label">Cartons here</span>
            <div className="rcv-stepper">
              <button type="button" aria-label="One fewer" onClick={() => setCount((n) => Math.max(0, n - 1))}>
                −
              </button>
              <output aria-label="Cartons counted">{count}</output>
              <button type="button" aria-label="One more" onClick={() => setCount((n) => n + 1)}>
                +
              </button>
            </div>
            <button className="rcv-button rcv-button--small rcv-button--primary" type="button" onClick={() => onCount(count, 0)}>
              Save count
            </button>
          </div>
        ) : (
          <button className="rcv-link" type="button" onClick={() => setOpen(true)}>
            Count it myself
          </button>
        ))}
    </article>
  );
}

function Done({
  receipt,
  busy,
  run,
  onDone,
}: {
  receipt: Receipt;
  busy: Busy;
  run: (kind: Exclude<Busy, null>, action: () => Promise<Receipt>) => Promise<void>;
  onDone: () => void;
}) {
  const added = receipt.lines.filter((l) => l.accepted_quantity && Number(l.accepted_quantity) > 0);
  const credit = receipt.credit_request;
  return (
    <div className="rcv-stack">
      <article className="rcv-card rcv-done">
        <p className="rcv-done__title">Added to stock</p>
        <ul>
          {added.map((l) => (
            <li key={l.line_no}>
              <span>{l.description.split(",")[0]}</span>
              <strong>{l.display.accepted}</strong>
            </li>
          ))}
        </ul>
        <p className="rcv-muted">
          {receipt.order?.po_number} is now {receipt.order?.status === "received" ? "fully received" : "partly received"}.
        </p>
      </article>

      {credit && (
        <article className="rcv-card rcv-credit">
          <div className="rcv-line__top">
            <p className="rcv-done__title">Credit request {credit.credit_ref}</p>
            <span className={`rcv-chip rcv-chip--${credit.status === "sent" ? "good" : "ask"}`}>
              {credit.status === "sent" ? "Sent" : "Draft"}
            </span>
          </div>
          <p className="rcv-muted">
            To {credit.supplier}, against slip {credit.against_slip} and {credit.against_po}
            {credit.simulated ? " (simulated supplier)" : ""}
          </p>
          <ul>
            {credit.lines.map((l, i) => (
              <li key={i}>
                <span>
                  {l.item_number} {l.description.split(",")[0]}
                </span>
                <strong>
                  {l.display} {l.reason}
                </strong>
              </li>
            ))}
          </ul>
          {credit.status === "drafted" && (
            <button
              className="rcv-button rcv-button--primary"
              type="button"
              disabled={busy != null}
              onClick={() => run("credit", () => sendCreditRequest(receipt.receipt_id))}
            >
              Send credit request
            </button>
          )}
        </article>
      )}

      <button className="rcv-button rcv-button--ghost" type="button" onClick={onDone}>
        Check another delivery
      </button>
    </div>
  );
}

export default ReceivingPage;
