export const ACTIVE_CAFE_ID = "CAFE001";

export type MenuProduct = {
  product_id: string;
  cafe_id: string;
  name: string;
  category: string;
  price_cents: number;
  active: boolean;
};

export type SaleSummary = {
  sale_id: string;
  cafe_id: string;
  source_id: string;
  sold_at: string;
  recorded_at: string;
  status: string;
  total_cents: number;
  item_count: number;
  total_units: number;
};

export type SaleItem = {
  product_id: string;
  product_name: string;
  quantity: number;
  unit_price_cents: number;
  line_total_cents: number;
};

export type SaleDetail = {
  sale_id: string;
  cafe_id: string;
  source_id: string;
  sold_at: string;
  recorded_at: string;
  status: string;
  total_cents: number;
  items: SaleItem[];
};

export type TopProduct = {
  product_id: string;
  name: string;
  quantity: number;
  revenue_cents: number;
};

export type HourlySales = {
  hour: number;
  sales_count: number;
  units_sold: number;
  revenue_cents: number;
};

export type SalesReport = {
  cafe_id: string;
  start_at: string | null;
  end_at: string | null;
  total_revenue_cents: number;
  sales_count: number;
  total_units_sold: number;
  average_sale_value_cents: number;
  top_products: TopProduct[];
  hourly_sales: HourlySales[];
};

export type SalesData = {
  products: MenuProduct[];
  sales: SaleSummary[];
  report: SalesReport;
};

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    signal,
    headers: { Accept: "application/json" },
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

export function getMenuProducts(signal?: AbortSignal): Promise<MenuProduct[]> {
  return requestJson<MenuProduct[]>(`/api/v1/sales/products?${cafeQuery}`, signal);
}

export function getSalesHistory(signal?: AbortSignal): Promise<SaleSummary[]> {
  return requestJson<SaleSummary[]>(`/api/v1/sales?${cafeQuery}`, signal);
}

export function getSalesReport(
  signal?: AbortSignal,
  startAt?: string,
  endAt?: string,
): Promise<SalesReport> {
  const reportQuery = new URLSearchParams({ cafe_id: ACTIVE_CAFE_ID });
  if (startAt) reportQuery.set("start_at", startAt);
  if (endAt) reportQuery.set("end_at", endAt);
  return requestJson<SalesReport>(
    `/api/v1/sales/reports/summary?${reportQuery.toString()}`,
    signal,
  );
}

export function getSaleDetail(
  saleId: string,
  signal?: AbortSignal,
): Promise<SaleDetail> {
  return requestJson<SaleDetail>(
    `/api/v1/sales/${encodeURIComponent(saleId)}?${cafeQuery}`,
    signal,
  );
}

export async function getSalesData(signal?: AbortSignal): Promise<SalesData> {
  const [products, sales, report] = await Promise.all([
    getMenuProducts(signal),
    getSalesHistory(signal),
    getSalesReport(signal),
  ]);

  return { products, sales, report };
}
