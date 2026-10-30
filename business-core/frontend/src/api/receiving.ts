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

export type ImageSource = { demo: true } | { file: File };

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

async function imageBody(source: ImageSource) {
  if ("demo" in source) return { use_demo_image: true };
  const mime = ["image/png", "image/webp"].includes(source.file.type) ? source.file.type : "image/jpeg";
  return { image_base64: await fileToBase64(source.file), mime };
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
