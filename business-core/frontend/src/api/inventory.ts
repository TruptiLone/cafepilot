export const ACTIVE_CAFE_ID = "CAFE001";

export type InventoryItem = {
  ingredient_id: string;
  cafe_id: string;
  name: string;
  category: string;
  base_unit: string;
  reorder_point: string | null;
  par_level: string | null;
  is_perishable: boolean;
  active: boolean;
};

export type StockCountSummary = {
  count_id: string;
  cafe_id: string;
  status: string;
  counted_at: string | null;
  recorded_at: string;
  counted_by: string | null;
  is_synthetic: boolean;
  item_count: number;
};

export type StockCountLine = {
  count_line_id: string;
  ingredient_id: string;
  counted_quantity: string;
};

export type StockCountDetail = Omit<StockCountSummary, "item_count"> & {
  lines: StockCountLine[];
};

export type InventoryMovement = {
  movement_id: string;
  cafe_id: string;
  ingredient_id: string;
  base_unit: string;
  movement_type: string;
  quantity_delta: string;
  is_estimated: boolean;
  source_type: string;
  source_id: string;
  occurred_at: string;
  recorded_at: string;
  recorded_by: string | null;
  reason: string | null;
};

export type StockEstimate = {
  ingredient_id: string;
  name: string;
  base_unit: string;
  stock_status: "known" | "unknown" | "inconsistent";
  estimated_quantity: string | null;
  latest_verified_count_id: string | null;
  latest_verified_counted_at: string | null;
  movement_delta: string | null;
  included_movement_count: number;
  anchor_is_synthetic: boolean | null;
  has_estimated_movements: boolean;
  calculated_at: string;
  reason: string | null;
};

export type InventoryData = {
  items: InventoryItem[];
  counts: StockCountSummary[];
  movements: InventoryMovement[];
  stock: StockEstimate[];
};

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    let detail = "The request could not be completed.";
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

const cafeQuery = `cafe_id=${encodeURIComponent(ACTIVE_CAFE_ID)}`;

export async function getInventoryData(signal?: AbortSignal): Promise<InventoryData> {
  const [items, counts, movements, stock] = await Promise.all([
    requestJson<InventoryItem[]>(`/api/v1/inventory/items?${cafeQuery}`, signal),
    requestJson<StockCountSummary[]>(`/api/v1/inventory/counts?${cafeQuery}`, signal),
    requestJson<InventoryMovement[]>(
      `/api/v1/inventory/movements?${cafeQuery}`,
      signal,
    ),
    requestJson<StockEstimate[]>(`/api/v1/inventory/stock?${cafeQuery}`, signal),
  ]);

  return { items, counts, movements, stock };
}

export function getStockCountDetail(
  countId: string,
  signal?: AbortSignal,
): Promise<StockCountDetail> {
  return requestJson<StockCountDetail>(
    `/api/v1/inventory/counts/${encodeURIComponent(countId)}?${cafeQuery}`,
    signal,
  );
}
