import { ACTIVE_CAFE_ID, AgentApiError } from "./agent";

export type OrderLine = {
  line_no: number;
  ingredient_id: string;
  supplier_item_number: string | null;
  description: string | null;
  quantity: string;
  unit: string;
  packs: number | null;
  pack_name: string | null;
};

export type OpenOrder = {
  order_id: string;
  po_number: string | null;
  supplier_name: string;
  status: string;
  requested_delivery_at: string;
  confirmed_delivery_at: string | null;
  simulated: boolean;
  lines: OrderLine[];
};

export type Received = {
  receipt_id: string;
  po_number: string | null;
  supplier_name: string | null;
  order_status: string | null;
  slip_number: string | null;
  confirmed_at: string | null;
  credit_ref: string | null;
};

export type LineStatus =
  | "match"
  | "needs_count"
  | "short"
  | "short_shipped"
  | "over"
  | "not_on_order"
  | "missing"
  | "damaged";

export type ReceiptLine = {
  line_no: number;
  ingredient_id: string | null;
  supplier_item_number: string | null;
  description: string;
  unit: string | null;
  unit_size: string | null;
  ordered_quantity: string | null;
  slip_shipped_quantity: string | null;
  seen_quantity: string | null;
  seen_may_hide_more: boolean;
  counted_quantity: string | null;
  rejected_quantity: string;
  rejected_reason: string | null;
  accepted_quantity: string | null;
  status: LineStatus;
  message: string;
  display: Partial<Record<"ordered" | "shipped" | "seen" | "counted" | "accepted", string>>;
};

export type CreditRequest = {
  credit_ref: string;
  supplier: string;
  against_slip: string | null;
  against_po: string | null;
  lines: { item_number: string | null; description: string; display: string; reason: string }[];
  status: "drafted" | "sent";
  simulated: boolean;
  note?: string;
};

export type Receipt = {
  receipt_id: string;
  cafe_id: string;
  status: "draft" | "confirmed" | "cancelled";
  order: OpenOrder | null;
  slip_number: string | null;
  slip_supplier: string | null;
  slip_po_number: string | null;
  slip_delivery: string | null;
  has_slip_image: boolean;
  has_photo_image: boolean;
  photo_notes: string | null;
  model: string | null;
  lines: ReceiptLine[];
  summary: { lines: number; match: number; needs_count: number; problems: number };
  credit_request: CreditRequest | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
  muse_message: string;
};

export type ImageSource = { demo: true } | { sample: string } | { file: File };

/** A sample slip or delivery photo the visitor can pick instead of their own. */
export type SamplePhoto = { sample_id: string; kind: "slip" | "photo"; po_number: string; title: string; image_url: string };

const BASE = "/api/v1/receiving";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...options,
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    });
  } catch {
    throw new AgentApiError("CaféPilot did not respond. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = response.statusText || "The request could not be completed.";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new AgentApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const post = (body: unknown = {}): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export function listOpenOrders(cafeId = ACTIVE_CAFE_ID): Promise<OpenOrder[]> {
  return request(`/orders?cafe_id=${encodeURIComponent(cafeId)}`);
}

export function listReceived(cafeId = ACTIVE_CAFE_ID): Promise<Received[]> {
  return request(`/received?cafe_id=${encodeURIComponent(cafeId)}`);
}

export function getReceipt(receiptId: string): Promise<Receipt> {
  return request(`/receipts/${encodeURIComponent(receiptId)}`);
}

/** The delivery check for a PO (checked in, else the newest glasses check): the fixed link /receiving?po=PO-123. */
export function getCheckForPo(poNumber: string, cafeId = ACTIVE_CAFE_ID): Promise<Receipt> {
  return request(`/orders/${encodeURIComponent(poNumber)}/check?cafe_id=${encodeURIComponent(cafeId)}`);
}

export function startReceipt(orderId: string | null, cafeId = ACTIVE_CAFE_ID): Promise<Receipt> {
  return request("/receipts", post({ cafe_id: cafeId, order_id: orderId }));
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new AgentApiError("Couldn't read that photo."));
    reader.readAsDataURL(file);
  });
}

const MAX_SIDE = 1600;

/** Phone photos can be 5–10 MB: shrink to 1600 px on the long side (JPEG) before sending. Falls back to the original. */
async function shrink(file: File): Promise<{ data: string; mime: string } | null> {
  try {
    if (typeof createImageBitmap !== "function") return null;
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 2_000_000) return null;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const url = canvas.toDataURL("image/jpeg", 0.85);
    return { data: url.split(",")[1] ?? "", mime: "image/jpeg" };
  } catch {
    return null;
  }
}

async function imageBody(source: ImageSource) {
  if ("demo" in source) return { use_demo_image: true };
  if ("sample" in source) return { sample_id: source.sample };
  const small = await shrink(source.file);
  if (small) return { image_base64: small.data, mime: small.mime };
  const mime = ["image/png", "image/webp"].includes(source.file.type) ? source.file.type : "image/jpeg";
  return { image_base64: await fileToBase64(source.file), mime };
}

export function listSamples(): Promise<SamplePhoto[]> {
  return request("/samples");
}

export async function readSlip(receiptId: string, source: ImageSource): Promise<Receipt> {
  return request(`/receipts/${receiptId}/slip`, post(await imageBody(source)));
}

export async function readPhoto(receiptId: string, source: ImageSource): Promise<Receipt> {
  return request(`/receipts/${receiptId}/photo`, post(await imageBody(source)));
}

export function countLine(receiptId: string, lineNo: number, countedUnits: number, rejectedUnits = 0, reason?: string): Promise<Receipt> {
  return request(
    `/receipts/${receiptId}/lines/${lineNo}`,
    post({ counted_units: countedUnits, rejected_units: rejectedUnits, reason: reason || null }),
  );
}

export function confirmReceipt(receiptId: string): Promise<Receipt> {
  return request(`/receipts/${receiptId}/confirm`, post({ confirmed_by: "owner", channel: "web" }));
}

export function sendCreditRequest(receiptId: string): Promise<Receipt> {
  return request(`/receipts/${receiptId}/credit-request/send`, post());
}

export const imageUrl = (receiptId: string, kind: "slip" | "photo") => `${BASE}/receipts/${receiptId}/images/${kind}`;
