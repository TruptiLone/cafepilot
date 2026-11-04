import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getStockEstimates, type StockEstimate } from "../api/inventory";
import {
  confirmReceipt,
  countLine,
  getCheckForPo,
  getReceipt,
  listReceived,
  imageUrl,
  listOpenOrders,
  readPhoto,
  readSlip,
  sendCreditRequest,
  startReceipt,
  type ImageSource,
  type OpenOrder,
  type Received,
  type Receipt,
  type ReceiptLine,
} from "../api/receiving";
import { lineView, seenSummary, tally } from "./receivingView";

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

function stockAmount(quantity: string, unit: string) {
  const n = Number(quantity);
  if (unit === "mL") return `${(n / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} L`;
  if (unit === "g") return `${(n / 1000).toLocaleString("en-US", { maximumFractionDigits: 2 })} kg`;
  return `${n.toLocaleString("en-US")} ${unit}`;
}

type Busy = null | "start" | "slip" | "photo" | "count" | "confirm" | "credit";
type Run = (kind: Exclude<Busy, null>, action: () => Promise<Receipt>) => Promise<void>;

const BUSY_TEXT: Record<Exclude<Busy, null>, string> = {
  start: "Opening the delivery check…",
  slip: "Muse is reading the packing slip…",
  photo: "Muse is counting what it can see…",
  count: "Comparing your count…",
  confirm: "Adding to stock…",
  credit: "Sending the credit request…",
};

const STEPS = ["Slip", "Delivery", "Count", "Confirm"];

function ReceivingPage() {
  const [orders, setOrders] = useState<OpenOrder[] | null>(null);
  const [received, setReceived] = useState<Received[]>([]);
  const [receipt, setReceiptState] = useState<Receipt | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const openId = searchParams.get("receipt");
  const openPo = searchParams.get("po");

  // Keep the delivery in the address, so going back (e.g. from Inventory) returns to it.
  const setReceipt = (next: Receipt | null) => {
    setReceiptState(next);
    if ((next?.receipt_id ?? null) !== openId) {
      setSearchParams(next ? { ...(openPo ? { po: openPo } : {}), receipt: next.receipt_id } : {}, { replace: true });
    }
  };

  useEffect(() => {
    if (openId && receipt?.receipt_id !== openId) {
      getReceipt(openId)
        .then(setReceiptState)
        .catch(() => setSearchParams({}, { replace: true }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId]);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  // A fixed link per order for the demo (/receiving?po=PO-123): it opens that order's delivery check.
  useEffect(() => {
    if (!openPo || openId) return;
    getCheckForPo(openPo)
      .then((found) => {
        setReceiptState(found);
        setSearchParams({ po: openPo, receipt: found.receipt_id }, { replace: true });
      })
      .catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openPo, openId]);

  const loadOrders = () => {
    listOpenOrders()
      .then(setOrders)
      .catch((e: Error) => setError(e.message));
    listReceived()
      .then(setReceived)
      .catch(() => setReceived([]));
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

  const bothRead = receipt != null && receipt.has_photo_image && (receipt.slip_number != null || receipt.has_slip_image);

  return (
    <section className="page page--receiving" aria-labelledby="page-title">
      <div className="rcv">
        <header className="rcv-head">
          <div>
            <p className="rcv-eyebrow">Receiving</p>
            <h1 id="page-title">Check a delivery</h1>
            <p className="rcv-subtitle">
              {bothRead
                ? "Muse read the slip and the photo. Only what you accept goes into stock."
                : "Look at the packing slip, then the delivery. Muse reads both; only what you accept goes into stock."}
            </p>
          </div>
          {receipt && <PoCard receipt={receipt} />}
        </header>

        {error && (
          <p className="rcv-error" role="alert">
            {error}
          </p>
        )}

        {!receipt ? (
          <div className="rcv-lists">
            <ExpectedDeliveries
              orders={orders}
              busy={busy === "start"}
              onStart={(orderId) => run("start", () => startReceipt(orderId))}
            />
            <ReceivedDeliveries received={received} busy={busy === "start"} onOpen={(id) => run("start", () => getReceipt(id))} />
          </div>
        ) : (
          <DeliveryCheck receipt={receipt} busy={busy} run={run} onDone={reset} />
        )}
      </div>
    </section>
  );
}

function PoCard({ receipt }: { receipt: Receipt }) {
  const order = receipt.order;
  if (!order) {
    return (
      <div className="rcv-po rcv-po--missing">
        <p className="rcv-po__title">{receipt.slip_po_number ? `${receipt.slip_po_number} · not found` : "Not on an order yet"}</p>
        <p className="rcv-po__when">Nothing goes into stock without an order</p>
      </div>
    );
  }
  return (
    <div className="rcv-po">
      <p className="rcv-po__title">
        {order.po_number ?? order.order_id} · {order.supplier_name}
      </p>
      <p className="rcv-po__when">Expected {when(order.confirmed_delivery_at ?? order.requested_delivery_at)}</p>
    </div>
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

function ReceivedDeliveries({
  received,
  busy,
  onOpen,
}: {
  received: Received[];
  busy: boolean;
  onOpen: (receiptId: string) => void;
}) {
  if (received.length === 0) return null;
  return (
    <div className="rcv-stack">
      <h2 className="rcv-section-title">Received</h2>
      {received.map((r) => (
        <article className="rcv-card rcv-order" key={r.receipt_id}>
          <div className="rcv-order__top">
            <div>
              <p className="rcv-order__supplier">{r.supplier_name ?? "Not on an order"}</p>
              <p className="rcv-muted">
                {[r.po_number, r.slip_number && `slip ${r.slip_number}`, r.confirmed_at && `checked in ${when(r.confirmed_at)}`]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {r.credit_ref && <p className="rcv-muted">Credit request {r.credit_ref}</p>}
            </div>
            {r.order_status === "received" && <span className="rcv-chip rcv-chip--good">Delivered</span>}
            {r.order_status === "partially_received" && <span className="rcv-chip rcv-chip--ask">Part received</span>}
          </div>
          <button className="rcv-button rcv-button--ghost" type="button" disabled={busy} onClick={() => onOpen(r.receipt_id)}>
            View check-in
          </button>
        </article>
      ))}
    </div>
  );
}

function DeliveryCheck({ receipt, busy, run, onDone }: { receipt: Receipt; busy: Busy; run: Run; onDone: () => void }) {
  const id = receipt.receipt_id;
  const confirmed = receipt.status === "confirmed";
  const hasSlip = receipt.slip_number != null || receipt.has_slip_image;
  const step = confirmed ? 4 : !hasSlip ? 0 : !receipt.has_photo_image ? 1 : receipt.summary.needs_count ? 2 : 3;
  const needsCount = receipt.summary.needs_count;

  return (
    <div className="rcv-check">
      <ol className="rcv-steps" aria-label="Progress">
        {STEPS.map((label, i) => (
          <li
            key={label}
            className={i < step ? "is-done" : i === step ? "is-current" : ""}
            aria-current={i === step ? "step" : undefined}
          >
            <span className="rcv-steps__bar" aria-hidden="true" />
            <span className="rcv-steps__label">
              {i + 1} · {label}
            </span>
          </li>
        ))}
      </ol>

      <MuseBanner receipt={receipt} busy={busy} />

      <div className="rcv-body">
        <div className="rcv-photos">
          <PhotoCard
            title="Packing slip"
            placeholder="Slip photo"
            fit="contain"
            done={hasSlip}
            busy={busy === "slip"}
            disabled={busy != null}
            locked={confirmed}
            image={receipt.has_slip_image ? imageUrl(id, "slip") : null}
            facts={
              hasSlip ? (
                <>
                  <p>
                    {receipt.slip_number && <strong>{receipt.slip_number}</strong>}
                    {receipt.slip_number && receipt.slip_supplier && " · "}
                    {receipt.slip_supplier}
                  </p>
                  <p>
                    {receipt.order?.po_number
                      ? `Matches ${receipt.order.po_number}`
                      : receipt.slip_po_number
                        ? `${receipt.slip_po_number}: no matching order`
                        : "No PO number on the slip"}
                  </p>
                </>
              ) : (
                <p>Point your glasses or phone at the slip.</p>
              )
            }
            onCapture={(source) => run("slip", () => readSlip(id, source))}
          />
          <PhotoCard
            title="The delivery"
            placeholder="Delivery photo"
            fit="contain"
            done={receipt.has_photo_image}
            busy={busy === "photo"}
            disabled={busy != null || !hasSlip}
            locked={confirmed}
            image={receipt.has_photo_image ? imageUrl(id, "photo") : null}
            facts={
              receipt.has_photo_image ? (
                <>
                  <p>
                    {receipt.photo_notes?.startsWith("Counted with SAM 3.1") ? (
                      <>
                        Counted with <strong>SAM 3.1</strong> + <strong>Muse Spark</strong>
                      </>
                    ) : (
                      <>
                        Read by <strong>Muse Spark</strong>
                      </>
                    )}
                  </p>
                  {receipt.lines.length > 0 && <p>{seenSummary(receipt.lines)}</p>}
                </>
              ) : (
                <p>{hasSlip ? "Now look at the cases and crates that came in." : "After the slip."}</p>
              )
            }
            onCapture={(source) => run("photo", () => readPhoto(id, source))}
          />
        </div>

        <div className="rcv-arrived">
          {receipt.lines.length > 0 ? (
            <>
              <h2 className="rcv-section-title">
                What arrived
                <span className="rcv-muted">
                  {receipt.summary.match} of {receipt.summary.lines} match
                </span>
              </h2>
              {receipt.lines.map((line) => (
                <LineRow
                  key={line.line_no}
                  line={line}
                  photoRead={receipt.has_photo_image}
                  locked={confirmed || busy != null}
                  onCount={(counted) => run("count", () => countLine(id, line.line_no, counted, 0))}
                />
              ))}
            </>
          ) : (
            <OnTheOrder order={receipt.order} />
          )}

          {!confirmed && receipt.lines.length > 0 && (
            <div className="rcv-card rcv-confirm">
              <p className="rcv-muted">
                {!receipt.order
                  ? "This slip doesn't match an order yet, so nothing can go into stock."
                  : needsCount
                    ? `Count ${needsCount === 1 ? "1 item" : `${needsCount} items`} first. Nothing goes into stock until you confirm.`
                    : "Nothing goes into stock until you confirm. Short items can become a credit request."}
              </p>
              <button
                className="rcv-button rcv-button--primary"
                type="button"
                disabled={busy != null || needsCount > 0 || !receipt.order}
                onClick={() => run("confirm", () => confirmReceipt(id))}
              >
                Confirm what arrived
              </button>
            </div>
          )}

          {confirmed && <Done receipt={receipt} busy={busy} run={run} onDone={onDone} />}
        </div>
      </div>
    </div>
  );
}

/** A viewfinder with a check in it: Muse looked at the delivery. */
function ViewfinderIcon() {
  return (
    <svg viewBox="0 0 32 32" width="30" height="30" fill="none" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 11V7a3 3 0 0 1 3-3h4M21 4h4a3 3 0 0 1 3 3v4M28 21v4a3 3 0 0 1-3 3h-4M11 28H7a3 3 0 0 1-3-3v-4" stroke="currentColor" />
      <path d="M10.5 16.5l3.6 3.6 7.4-7.6" stroke="var(--rcv-glow)" />
    </svg>
  );
}

const TALLY = [
  ["match", "match"],
  ["check", "check"],
  ["count", "count it"],
] as const;

function MuseBanner({ receipt, busy }: { receipt: Receipt; busy: Busy }) {
  const counts = tally(receipt.lines);
  const label = busy
    ? "Muse is working"
    : receipt.status === "confirmed"
      ? "Checked in"
      : receipt.has_photo_image
        ? "Muse checked the delivery"
        : "Muse";
  return (
    <div className={`rcv-muse${busy ? " is-thinking" : ""}`}>
      <span className="rcv-muse__icon" aria-hidden="true">
        <ViewfinderIcon />
      </span>
      <div className="rcv-muse__body" aria-live="polite">
        <p className="rcv-muse__name">{label}</p>
        <p className="rcv-muse__text">{busy ? BUSY_TEXT[busy] : receipt.muse_message}</p>
      </div>
      {receipt.has_photo_image && receipt.lines.length > 0 && (
        <dl className="rcv-tally" aria-label="Items by what they need">
          {TALLY.map(([need, text]) => (
            <div key={need} className={`rcv-tally__item rcv-tally__item--${need}`} data-testid={`tally-${need}`}>
              <dt>{text}</dt>
              <dd>{counts[need]}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function PhotoCard({
  title,
  placeholder,
  fit,
  done,
  busy,
  disabled,
  locked,
  image,
  facts,
  onCapture,
}: {
  title: string;
  placeholder: string;
  fit: "contain" | "cover";
  done: boolean;
  busy: boolean;
  disabled: boolean;
  locked: boolean;
  image: string | null;
  facts: React.ReactNode;
  onCapture: (source: ImageSource) => void;
}) {
  const upload = (label: string) => (
    <label className={`rcv-button rcv-button--small rcv-button--ghost${disabled ? " is-disabled" : ""}`}>
      {label}
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
  );
  return (
    <article className={`rcv-card rcv-photo${busy ? " is-busy" : ""}`}>
      <div className="rcv-photo__head">
        <h3 className="rcv-photo__title">{title}</h3>
        {busy ? <span className="rcv-chip rcv-chip--ask">Reading…</span> : done && <span className="rcv-chip rcv-chip--good">Read</span>}
      </div>
      <div className={`rcv-photo__frame${image ? "" : " is-empty"}`}>
        {image ? <img src={image} alt={title} style={{ objectFit: fit }} /> : <span>{placeholder}</span>}
      </div>
      <div className="rcv-photo__facts">{facts}</div>
      <div className="rcv-photo__actions">
        {!locked &&
          (done ? (
            upload("Retake")
          ) : (
            <>
              <button className="rcv-button rcv-button--small" type="button" disabled={disabled} onClick={() => onCapture({ demo: true })}>
                Use demo photo
              </button>
              {upload("Take or upload")}
            </>
          ))}
        {image && (
          <a className="rcv-button rcv-button--small rcv-button--ghost" href={image} target="_blank" rel="noreferrer">
            View full size
          </a>
        )}
      </div>
    </article>
  );
}

/** Before the slip is read: what the order says is coming. */
function OnTheOrder({ order }: { order: OpenOrder | null }) {
  if (!order) {
    return (
      <>
        <h2 className="rcv-section-title">What arrived</h2>
        <p className="rcv-card rcv-muted">Muse lists the items once it has read the slip.</p>
      </>
    );
  }
  return (
    <>
      <h2 className="rcv-section-title">
        On the order
        <span className="rcv-muted">waiting for the slip</span>
      </h2>
      {order.lines.map((line) => (
        <article className="rcv-card rcv-row rcv-row--waiting" key={line.line_no}>
          <div className="rcv-row__name">
            <p className="rcv-row__title">{line.description ?? line.ingredient_id}</p>
            {line.supplier_item_number && <p className="rcv-muted">{line.supplier_item_number}</p>}
          </div>
          <dl className="rcv-row__nums">
            <Num label="Ordered" value={line.packs ?? "—"} />
            <Num label="Slip says" value="—" />
            <Num label="Muse saw" value="—" />
          </dl>
        </article>
      ))}
    </>
  );
}

function Num({ label, value, note }: { label: string; value: number | string; note?: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {value}
        {note && <small>{note}</small>}
      </dd>
    </div>
  );
}

function LineRow({
  line,
  photoRead,
  locked,
  onCount,
}: {
  line: ReceiptLine;
  photoRead: boolean;
  locked: boolean;
  onCount: (counted: number) => void;
}) {
  const view = lineView(line, photoRead);
  const start = view.counted ?? view.seen ?? view.slip ?? view.ordered ?? 0;
  const [count, setCount] = useState<number>(start);
  const asksForCount = line.status === "needs_count" && view.tone !== "wait";
  const [open, setOpen] = useState(asksForCount);
  const name = line.description.split(",")[0];

  useEffect(() => {
    if (asksForCount) setOpen(true);
    setCount(start);
  }, [asksForCount, start]);

  return (
    <article className={`rcv-card rcv-row rcv-row--${view.tone}`}>
      <div className="rcv-row__name">
        <p className="rcv-row__title">{line.description}</p>
        {line.supplier_item_number && <p className="rcv-muted">{line.supplier_item_number}</p>}
      </div>
      <dl className="rcv-row__nums">
        <Num label="Ordered" value={view.ordered ?? "—"} />
        <Num label="Slip says" value={view.slip ?? "—"} />
        <Num label="Muse saw" value={view.tone === "wait" ? "—" : view.seen ?? "?"} note={view.counted != null ? `you counted ${view.counted}` : undefined} />
      </dl>
      <div className="rcv-row__action">
        <span className={`rcv-chip rcv-chip--${view.tone}`}>{view.badge}</span>
        {!locked &&
          (open ? (
            <div className="rcv-count">
              <div className="rcv-stepper">
                <button type="button" aria-label={`One fewer ${name}`} onClick={() => setCount((n) => Math.max(0, n - 1))}>
                  −
                </button>
                <output aria-label={`${name} counted`}>{count}</output>
                <button type="button" aria-label={`One more ${name}`} onClick={() => setCount((n) => n + 1)}>
                  +
                </button>
              </div>
              <button className="rcv-button rcv-button--small rcv-button--primary" type="button" onClick={() => onCount(count)}>
                Save count
              </button>
            </div>
          ) : (
            <button className="rcv-link" type="button" onClick={() => setOpen(true)}>
              Count it myself
            </button>
          ))}
      </div>
      {line.status !== "match" && view.tone !== "wait" && <p className="rcv-row__message">{line.message}</p>}
    </article>
  );
}

function Done({ receipt, busy, run, onDone }: { receipt: Receipt; busy: Busy; run: Run; onDone: () => void }) {
  const added = receipt.lines.filter((l) => l.accepted_quantity && Number(l.accepted_quantity) > 0);
  const credit = receipt.credit_request;
  const [stock, setStock] = useState<Map<string, StockEstimate>>(new Map());
  useEffect(() => {
    getStockEstimates()
      .then((rows) => setStock(new Map(rows.map((row) => [row.ingredient_id, row]))))
      .catch(() => setStock(new Map()));
  }, [receipt.confirmed_at]);
  const link = (tab: "movements" | "stock") =>
    `/inventory?${new URLSearchParams({
      tab,
      receipt: receipt.receipt_id,
      items: added.map((l) => l.ingredient_id ?? "").join(","),
      ...(receipt.slip_number ? { slip: receipt.slip_number } : {}),
    })}`;
  return (
    <div className="rcv-stack">
      <article className="rcv-card rcv-done">
        <p className="rcv-done__title">Added to stock</p>
        <ul>
          {added.map((l) => {
            const now = l.ingredient_id ? stock.get(l.ingredient_id) : undefined;
            return (
              <li key={l.line_no}>
                <span>
                  {l.description.split(",")[0]}
                  {now?.estimated_quantity != null && (
                    <small className="rcv-done__now">Now in stock: {stockAmount(now.estimated_quantity, now.base_unit)}</small>
                  )}
                </span>
                <strong>+{l.display.accepted}</strong>
              </li>
            );
          })}
        </ul>
        <p className="rcv-muted">
          {receipt.order?.po_number} is now {receipt.order?.status === "received" ? "fully received" : "partly received"}.
        </p>
        <div className="rcv-done__links">
          <Link className="rcv-button rcv-button--small" to={link("movements")}>
            See stock movements
          </Link>
          <Link className="rcv-button rcv-button--small" to={link("stock")}>
            See current stock
          </Link>
        </div>
      </article>

      {credit && (
        <article className="rcv-card rcv-credit">
          <div className="rcv-order__top">
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
