import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getSaleDetail,
  getSalesData,
  type SaleDetail,
  type SalesData,
  type SalesReport,
} from "../api/sales";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import StatusBadge from "../components/StatusBadge";

type SalesTab = "overview" | "history" | "products";

const tabs: { id: SalesTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "history", label: "Sales History" },
  { id: "products", label: "Menu Products" },
];

const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Los_Angeles",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const numberFormatter = new Intl.NumberFormat("en-US");

function formatMoney(cents: number) {
  return moneyFormatter.format(cents / 100);
}

function formatDate(value: string) {
  return dateFormatter.format(new Date(value));
}

function formatCategory(value: string) {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function formatLocalHour(hour: number) {
  const normalizedHour = ((hour % 24) + 24) % 24;
  const displayHour = normalizedHour % 12 || 12;
  return `${displayHour}:00 ${normalizedHour < 12 ? "AM" : "PM"}`;
}

function SalesPage() {
  const [activeTab, setActiveTab] = useState<SalesTab>("overview");
  const [data, setData] = useState<SalesData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSaleId, setSelectedSaleId] = useState<string | null>(null);
  const [saleDetail, setSaleDetail] = useState<SaleDetail | null>(null);
  const [saleDetailError, setSaleDetailError] = useState<string | null>(null);
  const [isSaleDetailLoading, setIsSaleDetailLoading] = useState(false);
  const saleDetailController = useRef<AbortController | null>(null);

  const loadSales = useCallback(async (signal?: AbortSignal) => {
    setIsLoading(true);
    setError(null);
    try {
      setData(await getSalesData(signal));
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") {
        return;
      }
      setError(
        requestError instanceof Error
          ? `${requestError.message} Confirm the Business Core backend is running on port 8001.`
          : "Confirm the Business Core backend is running on port 8001.",
      );
    } finally {
      if (!signal?.aborted) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadSales(controller.signal);
    return () => {
      controller.abort();
      saleDetailController.current?.abort();
    };
  }, [loadSales]);

  const loadSaleDetail = async (saleId: string, forceReload = false) => {
    if (selectedSaleId === saleId && !forceReload) {
      saleDetailController.current?.abort();
      saleDetailController.current = null;
      setSelectedSaleId(null);
      setSaleDetail(null);
      setSaleDetailError(null);
      setIsSaleDetailLoading(false);
      return;
    }

    saleDetailController.current?.abort();
    const controller = new AbortController();
    saleDetailController.current = controller;
    setSelectedSaleId(saleId);
    setSaleDetail(null);
    setSaleDetailError(null);
    setIsSaleDetailLoading(true);

    try {
      const detail = await getSaleDetail(saleId, controller.signal);
      if (saleDetailController.current === controller && !controller.signal.aborted) {
        setSaleDetail(detail);
      }
    } catch (requestError) {
      if (
        saleDetailController.current === controller &&
        !(requestError instanceof DOMException && requestError.name === "AbortError")
      ) {
        setSaleDetailError(
          requestError instanceof Error
            ? requestError.message
            : "The sale details could not be loaded.",
        );
      }
    } finally {
      if (saleDetailController.current === controller) {
        saleDetailController.current = null;
        setIsSaleDetailLoading(false);
      }
    }
  };

  return (
    <section className="page page--sales" aria-labelledby="page-title">
      <div className="page__heading sales-heading">
        <div>
          <p className="page__eyebrow">Business Management</p>
          <h1 id="page-title">Sales</h1>
          <p className="page__description">
            Review completed transactions, product performance, and café-local
            sales patterns for CAFE001.
          </p>
        </div>
        <div className="demo-label">
          <span aria-hidden="true" />
          Historical Demo Data
        </div>
      </div>

      <div className="historical-notice">
        This workspace shows synthetic completed sales from January 6–12, 2025,
        not today&apos;s activity. Times use America/Los_Angeles.
      </div>

      <div className="inventory-panel sales-panel">
        <div className="inventory-tabs" role="tablist" aria-label="Sales views">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`inventory-tab${activeTab === tab.id ? " is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`sales-panel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
              {data && tab.id !== "overview" && (
                <span className="tab-count">
                  {tab.id === "history" ? data.sales.length : data.products.length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div
          className="inventory-panel__body"
          id={`sales-panel-${activeTab}`}
          role="tabpanel"
        >
          {isLoading && <LoadingState label="sales data" />}
          {!isLoading && error && (
            <ErrorState
              heading="Sales data is unavailable"
              message={error}
              onRetry={() => void loadSales()}
            />
          )}
          {!isLoading && !error && data && (
            <>
              {activeTab === "overview" && <SalesOverview report={data.report} />}
              {activeTab === "history" && (
                <SalesHistory
                  data={data}
                  selectedSaleId={selectedSaleId}
                  saleDetail={saleDetail}
                  detailError={saleDetailError}
                  isDetailLoading={isSaleDetailLoading}
                  onToggleDetail={loadSaleDetail}
                />
              )}
              {activeTab === "products" && <MenuProducts data={data} />}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function SalesOverview({ report }: { report: SalesReport }) {
  const maximumRevenue = useMemo(
    () => Math.max(...report.hourly_sales.map((hour) => hour.revenue_cents), 0),
    [report.hourly_sales],
  );

  return (
    <div className="sales-overview">
      <div className="sales-metrics" aria-label="Sales summary">
        <MetricCard label="Total Revenue" value={formatMoney(report.total_revenue_cents)} />
        <MetricCard label="Completed Transactions" value={numberFormatter.format(report.sales_count)} />
        <MetricCard label="Units Sold" value={numberFormatter.format(report.total_units_sold)} />
        <MetricCard label="Average Transaction" value={formatMoney(report.average_sale_value_cents)} />
      </div>

      <div className="sales-overview-grid">
        <section className="sales-card" aria-labelledby="top-products-title">
          <div className="sales-card__heading">
            <div>
              <p className="page__eyebrow">Product Performance</p>
              <h2 id="top-products-title">Top five products</h2>
            </div>
          </div>
          {!report.top_products.length ? (
            <InlineEmptyState label="product sales" />
          ) : (
            <ol className="top-products-list">
              {report.top_products.map((product, index) => (
                <li key={product.product_id}>
                  <span className="top-products-list__rank">{index + 1}</span>
                  <span className="top-products-list__name">
                    <strong>{product.name}</strong>
                    <small>{numberFormatter.format(product.quantity)} units</small>
                  </span>
                  <strong>{formatMoney(product.revenue_cents)}</strong>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="sales-card" aria-labelledby="hourly-sales-title">
          <div className="sales-card__heading">
            <div>
              <p className="page__eyebrow">Café-Local Time</p>
              <h2 id="hourly-sales-title">Hourly sales distribution</h2>
            </div>
          </div>
          {!report.hourly_sales.length ? (
            <InlineEmptyState label="hourly sales" />
          ) : (
            <div className="hourly-sales-list">
              {report.hourly_sales.map((hour) => {
                const width = maximumRevenue
                  ? Math.max((hour.revenue_cents / maximumRevenue) * 100, 3)
                  : 0;
                return (
                  <div className="hourly-sales-row" key={hour.hour}>
                    <span className="hourly-sales-row__hour">
                      {formatLocalHour(hour.hour)}
                    </span>
                    <span className="hourly-sales-row__track" aria-hidden="true">
                      <span style={{ width: `${width}%` }} />
                    </span>
                    <span className="hourly-sales-row__value">
                      <strong>{formatMoney(hour.revenue_cents)}</strong>
                      <small>
                        {hour.sales_count} sales · {hour.units_sold} units
                      </small>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="sales-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

type SalesHistoryProps = {
  data: SalesData;
  selectedSaleId: string | null;
  saleDetail: SaleDetail | null;
  detailError: string | null;
  isDetailLoading: boolean;
  onToggleDetail: (saleId: string, forceReload?: boolean) => void;
};

function SalesHistory({
  data,
  selectedSaleId,
  saleDetail,
  detailError,
  isDetailLoading,
  onToggleDetail,
}: SalesHistoryProps) {
  if (!data.sales.length) return <EmptyState label="completed sales" />;

  return (
    <>
      <div className="sales-section-heading">
        <div>
          <strong>Completed Sales</strong>
          <span>All available historical transactions</span>
        </div>
      </div>
      {selectedSaleId && (
        <section className="count-detail sales-detail" aria-live="polite">
          <div className="count-detail__heading">
            <div>
              <p className="page__eyebrow">Sale Detail</p>
              <h2>{selectedSaleId}</h2>
            </div>
            {saleDetail && <StatusBadge status={saleDetail.status} />}
          </div>
          {isDetailLoading && <LoadingState label="sale details" />}
          {detailError && (
            <div className="inline-detail-error" role="alert">
              <p>{detailError}</p>
              <button
                className="secondary-button"
                type="button"
                onClick={() => onToggleDetail(selectedSaleId, true)}
              >
                Try again
              </button>
            </div>
          )}
          {saleDetail && (
            <>
              <div className="sale-detail__summary">
                <span>Sold {formatDate(saleDetail.sold_at)}</span>
                <strong>Transaction total {formatMoney(saleDetail.total_cents)}</strong>
              </div>
              <DataTable compact>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Quantity</th>
                    <th>Historical Unit Price</th>
                    <th>Line Total</th>
                  </tr>
                </thead>
                <tbody>
                  {saleDetail.items.map((item) => (
                    <tr key={item.product_id}>
                      <td>
                        <span className="stacked-cell">
                          <span className="primary-cell">{item.product_name}</span>
                          <small>{item.product_id}</small>
                        </span>
                      </td>
                      <td>{item.quantity}</td>
                      <td>{formatMoney(item.unit_price_cents)}</td>
                      <td className="money-cell">{formatMoney(item.line_total_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            </>
          )}
        </section>
      )}
      <DataTable className="sales-history-table">
        <thead>
          <tr>
            <th>Sale ID</th>
            <th>Date and Time</th>
            <th>Status</th>
            <th>Product Lines</th>
            <th>Total Units</th>
            <th>Total Amount</th>
            <th><span className="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {data.sales.map((sale) => (
            <tr key={sale.sale_id}>
              <td className="identifier-cell">{sale.sale_id}</td>
              <td>{formatDate(sale.sold_at)}</td>
              <td><StatusBadge status={sale.status} /></td>
              <td>{sale.item_count}</td>
              <td>{sale.total_units}</td>
              <td className="money-cell">{formatMoney(sale.total_cents)}</td>
              <td className="action-cell">
                <button
                  className="text-button"
                  type="button"
                  aria-expanded={selectedSaleId === sale.sale_id}
                  aria-label={`${selectedSaleId === sale.sale_id ? "Close" : "View"} sale ${sale.sale_id}`}
                  onClick={() => onToggleDetail(sale.sale_id)}
                >
                  {selectedSaleId === sale.sale_id ? "Close" : "View details"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>

    </>
  );
}

function MenuProducts({ data }: { data: SalesData }) {
  if (!data.products.length) return <EmptyState label="menu products" />;

  return (
    <>
      <div className="sales-section-heading">
        <div>
          <strong>Menu Products</strong>
          <span>Live product catalog and current server prices</span>
        </div>
      </div>
      <DataTable>
        <thead>
          <tr>
            <th>Product Name</th>
            <th>Category</th>
            <th>Price</th>
            <th>Active Status</th>
          </tr>
        </thead>
        <tbody>
          {data.products.map((product) => (
            <tr key={product.product_id}>
              <td>
                <span className="stacked-cell">
                  <span className="primary-cell">{product.name}</span>
                  <small>{product.product_id}</small>
                </span>
              </td>
              <td>{formatCategory(product.category)}</td>
              <td className="money-cell">{formatMoney(product.price_cents)}</td>
              <td><StatusBadge status={product.active ? "active" : "inactive"} /></td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </>
  );
}

function InlineEmptyState({ label }: { label: string }) {
  return <p className="sales-empty-inline">No {label} found for this history.</p>;
}

function DataTable({
  children,
  compact = false,
  className = "",
}: {
  children: React.ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={`table-scroll${className ? ` ${className}` : ""}`}>
      <table className={`data-table${compact ? " data-table--compact" : ""}`}>
        {children}
      </table>
    </div>
  );
}

export default SalesPage;
