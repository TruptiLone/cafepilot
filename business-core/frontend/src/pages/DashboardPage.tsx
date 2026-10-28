import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getInventoryItems, getStockEstimates, type InventoryItem, type StockEstimate } from "../api/inventory";
import { getSalesReport, type SalesReport } from "../api/sales";
import { getCoverage, getEmployees, type CoverageReport, type Employee } from "../api/staffing";
import StatusBadge from "../components/StatusBadge";
import { CAFE_TIME_ZONE, resolveCafeTimestamp } from "../utils/cafeTime";

type Resource<T> = {
  data: T | null;
  isLoading: boolean;
  error: string | null;
  retry: () => void;
};

type MetricState = "ready" | "loading" | "error";
type SummaryMetric = {
  label: string;
  detail: string;
  state: MetricState;
  value: string;
  retry?: () => void;
};

const HISTORY_LABEL = "January 6–12, 2025";
const HISTORY_START_AT = requireCafeTimestamp("2025-01-06T00:00");
const HISTORY_END_AT = requireCafeTimestamp("2025-01-13T00:00");
const REQUIRED_STAFF = 2;

const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});
const numberFormatter = new Intl.NumberFormat("en-US");
const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});
const dateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  month: "short",
  day: "numeric",
  year: "numeric",
});
const dayLabelFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  weekday: "short",
  month: "short",
  day: "numeric",
});
const dayKeyFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: CAFE_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function requireCafeTimestamp(value: string) {
  const resolution = resolveCafeTimestamp(value);
  if (resolution.status !== "valid") throw new Error(`Invalid dashboard date: ${value}`);
  return resolution.timestamp;
}

function loadHistoricalSales(signal?: AbortSignal) {
  return getSalesReport(signal, HISTORY_START_AT, HISTORY_END_AT);
}

function loadHistoricalCoverage(signal?: AbortSignal) {
  return getCoverage(HISTORY_START_AT, HISTORY_END_AT, REQUIRED_STAFF, signal);
}

function formatMoney(cents: number) {
  return moneyFormatter.format(cents / 100);
}

function formatLocalHour(hour: number) {
  const normalizedHour = ((hour % 24) + 24) % 24;
  const displayHour = normalizedHour % 12 || 12;
  return `${displayHour} ${normalizedHour < 12 ? "AM" : "PM"}`;
}

function useResource<T>(
  loader: (signal?: AbortSignal) => Promise<T>,
  fallbackMessage: string,
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await loader(signal);
        if (!signal?.aborted) setData(result);
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        if (!signal?.aborted) {
          setError(requestError instanceof Error ? requestError.message : fallbackMessage);
        }
      } finally {
        if (!signal?.aborted) setIsLoading(false);
      }
    },
    [fallbackMessage, loader],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return { data, isLoading, error, retry: () => void load() };
}

function metricValue<T>(
  resource: Resource<T>,
  select: (data: T) => string,
): { state: MetricState; value: string } {
  if (resource.isLoading) return { state: "loading", value: "Loading…" };
  if (resource.error || !resource.data) return { state: "error", value: "Unavailable" };
  return { state: "ready", value: select(resource.data) };
}

function DashboardPage() {
  const sales = useResource<SalesReport>(loadHistoricalSales, "Sales summary is unavailable.");
  const inventoryItems = useResource<InventoryItem[]>(getInventoryItems, "Ingredient catalog is unavailable.");
  const stock = useResource<StockEstimate[]>(getStockEstimates, "Stock estimates are unavailable.");
  const employees = useResource<Employee[]>(getEmployees, "Employee data is unavailable.");
  const coverage = useResource<CoverageReport>(loadHistoricalCoverage, "Coverage data is unavailable.");

  return (
    <section className="page page--dashboard" aria-labelledby="page-title">
      <div className="page__heading dashboard-heading">
        <div>
          <p className="page__eyebrow">Business Management</p>
          <h1 id="page-title">Operations Dashboard</h1>
          <p className="page__description">
            A café-owner overview of historical sales, latest-known inventory estimates,
            and scheduled staffing coverage for CAFE001.
          </p>
        </div>
        <div className="demo-label">
          <span aria-hidden="true" />
          Synthetic Demo Operations
        </div>
      </div>

      <div className="dashboard-context" role="note">
        <strong>Historical operations period: {HISTORY_LABEL}</strong>
        <span>
          Sales and staffing use this fixed café-local period. Inventory is a separate
          latest-known accounting estimate and is not a historical snapshot for these dates.
        </span>
      </div>

      <BusinessSummary sales={sales} inventoryItems={inventoryItems} employees={employees} />
      <div className="dashboard-section-grid">
        <SalesPerformance resource={sales} />
        <StaffingCoverage resource={coverage} />
      </div>
      <InventoryHealth resource={stock} />
    </section>
  );
}

function BusinessSummary({
  sales,
  inventoryItems,
  employees,
}: {
  sales: Resource<SalesReport>;
  inventoryItems: Resource<InventoryItem[]>;
  employees: Resource<Employee[]>;
}) {
  const metrics: SummaryMetric[] = [
    {
      label: "Historical Revenue",
      detail: HISTORY_LABEL,
      retry: sales.retry,
      ...metricValue(sales, (report) => formatMoney(report.total_revenue_cents)),
    },
    {
      label: "Completed Sales",
      detail: HISTORY_LABEL,
      ...metricValue(sales, (report) => numberFormatter.format(report.sales_count)),
    },
    {
      label: "Units Sold",
      detail: HISTORY_LABEL,
      ...metricValue(sales, (report) => numberFormatter.format(report.total_units_sold)),
    },
    {
      label: "Average Sale Value",
      detail: HISTORY_LABEL,
      ...metricValue(sales, (report) => formatMoney(report.average_sale_value_cents)),
    },
    {
      label: "Ingredient Catalog",
      detail: "Current active and inactive records",
      retry: inventoryItems.retry,
      ...metricValue(inventoryItems, (items) => numberFormatter.format(items.length)),
    },
    {
      label: "Employees",
      detail: "Current café records",
      retry: employees.retry,
      ...metricValue(employees, (records) => numberFormatter.format(records.length)),
    },
  ];

  return (
    <section className="dashboard-section" aria-labelledby="business-summary-title">
      <DashboardSectionHeading
        eyebrow="Live API Summary"
        title="Business summary"
        id="business-summary-title"
        detail="Each metric remains independent when another business domain is unavailable."
      />
      <div className="dashboard-kpis" aria-label="Business summary metrics">
        {metrics.map((metric) => (
          <article className={`dashboard-kpi dashboard-kpi--${metric.state}`} key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            <small>{metric.detail}</small>
            {metric.state === "error" && metric.retry && (
              <button className="dashboard-kpi__retry" type="button" onClick={metric.retry}>
                Retry {metric.label}
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function SalesPerformance({ resource }: { resource: Resource<SalesReport> }) {
  const maximumRevenue = useMemo(
    () => Math.max(...(resource.data?.hourly_sales.map((hour) => hour.revenue_cents) ?? []), 0),
    [resource.data],
  );

  return (
    <section className="dashboard-section dashboard-section--domain" aria-labelledby="dashboard-sales-title">
      <DashboardSectionHeading
        eyebrow="Historical Performance"
        title="Sales performance"
        id="dashboard-sales-title"
        detail={`${HISTORY_LABEL} · ${CAFE_TIME_ZONE}`}
        link={{ label: "Open Sales", to: "/sales" }}
      />
      <DashboardResourceState resource={resource} label="sales performance">
        {(report) => (
          <div className="dashboard-domain-content">
            <div>
              <h3>Top-selling products</h3>
              {!report.top_products.length ? (
                <DashboardEmpty label="top-selling products" />
              ) : (
                <ol className="dashboard-ranking">
                  {report.top_products.slice(0, 5).map((product, index) => (
                    <li key={product.product_id}>
                      <span>{index + 1}</span>
                      <div>
                        <strong>{product.name}</strong>
                        <small>{numberFormatter.format(product.quantity)} units</small>
                      </div>
                      <strong>{formatMoney(product.revenue_cents)}</strong>
                    </li>
                  ))}
                </ol>
              )}
            </div>
            <div>
              <h3>Hourly sales distribution</h3>
              {!report.hourly_sales.length ? (
                <DashboardEmpty label="hourly sales" />
              ) : (
                <div className="dashboard-bars" aria-label="Hourly sales distribution">
                  {report.hourly_sales.map((hour) => (
                    <div
                      className="dashboard-bar-row"
                      key={hour.hour}
                      aria-label={`${formatLocalHour(hour.hour)}: ${formatMoney(hour.revenue_cents)}, ${hour.sales_count} sales`}
                    >
                      <span>{formatLocalHour(hour.hour)}</span>
                      <div aria-hidden="true"><span style={{ width: `${maximumRevenue ? (hour.revenue_cents / maximumRevenue) * 100 : 0}%` }} /></div>
                      <strong>{formatMoney(hour.revenue_cents)}</strong>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </DashboardResourceState>
    </section>
  );
}

function InventoryHealth({ resource }: { resource: Resource<StockEstimate[]> }) {
  const latestCalculation = useMemo(() => {
    if (!resource.data?.length) return null;
    return resource.data.reduce((latest, item) =>
      new Date(item.calculated_at).getTime() > new Date(latest).getTime()
        ? item.calculated_at
        : latest,
    resource.data[0].calculated_at);
  }, [resource.data]);

  return (
    <section className="dashboard-section dashboard-section--inventory" aria-labelledby="dashboard-inventory-title">
      <DashboardSectionHeading
        eyebrow="Latest Accounting Estimate"
        title="Inventory health"
        id="dashboard-inventory-title"
        detail={latestCalculation
          ? `Calculated ${dateTimeFormatter.format(new Date(latestCalculation))} · not a historical snapshot`
          : "Latest-known stock basis · not a historical snapshot"}
        link={{ label: "Open Inventory", to: "/inventory" }}
      />
      <DashboardResourceState resource={resource} label="inventory estimates">
        {(estimates) => {
          const known = estimates.filter((item) => item.stock_status === "known").length;
          const unknown = estimates.filter((item) => item.stock_status === "unknown").length;
          const inconsistent = estimates.filter((item) => item.stock_status === "inconsistent").length;
          const estimatedMovements = estimates.filter((item) => item.has_estimated_movements).length;
          return (
            <div className="dashboard-inventory-content">
              <div className="inventory-health-metrics" aria-label="Inventory stock status counts">
                <MiniMetric label="Known" value={known} />
                <MiniMetric label="Unknown" value={unknown} tone={unknown ? "warning" : undefined} />
                <MiniMetric label="Inconsistent" value={inconsistent} tone={inconsistent ? "danger" : undefined} />
                <MiniMetric label="Estimated Movement" value={estimatedMovements} />
              </div>
              <p className="dashboard-disclaimer">
                Estimates are not proof that stock is physically usable or reserved. No low-stock claim is made without a configured reorder threshold.
              </p>
              {!estimates.length ? (
                <DashboardEmpty label="inventory estimates" />
              ) : (
                <div className="dashboard-stock-list">
                  {estimates.map((item) => (
                    <article className="dashboard-stock-row" key={item.ingredient_id}>
                      <div>
                        <strong>{item.name}</strong>
                        <small>
                          {item.latest_verified_counted_at
                            ? `Verified observation ${dateFormatter.format(new Date(item.latest_verified_counted_at))}`
                            : "No verified physical count"}
                        </small>
                      </div>
                      <div className="dashboard-stock-row__badges">
                        <StatusBadge status={item.stock_status} />
                        {item.anchor_is_synthetic && <span className="data-badge">Synthetic count</span>}
                        {item.has_estimated_movements && <span className="data-badge">Estimated movements</span>}
                      </div>
                      <strong className={item.stock_status === "unknown" ? "unknown-value" : undefined}>
                        {item.estimated_quantity === null ? "Unknown" : `${item.estimated_quantity} ${item.base_unit}`}
                      </strong>
                    </article>
                  ))}
                </div>
              )}
            </div>
          );
        }}
      </DashboardResourceState>
    </section>
  );
}

function StaffingCoverage({ resource }: { resource: Resource<CoverageReport> }) {
  return (
    <section className="dashboard-section dashboard-section--domain" aria-labelledby="dashboard-staffing-title">
      <DashboardSectionHeading
        eyebrow="Scheduled Coverage"
        title="Staffing coverage"
        id="dashboard-staffing-title"
        detail={`${HISTORY_LABEL} · fixed threshold, not demand optimization`}
        link={{ label: "Open Staffing", to: "/staffing" }}
      />
      <DashboardResourceState resource={resource} label="staffing coverage">
        {(report) => {
          const adequateHours = report.hours.filter((hour) => hour.coverage_status === "adequate").length;
          const understaffedHours = report.hours.length - adequateHours;
          const dailyCoverage = summarizeCoverageByDay(report);
          return (
            <div className="dashboard-coverage-content">
              <div className="coverage-summary-inline" aria-label="Staffing coverage totals">
                <MiniMetric label="Adequate Hours" value={adequateHours} />
                <MiniMetric label="Understaffed Hours" value={understaffedHours} tone={understaffedHours ? "danger" : undefined} />
                <MiniMetric label="Required Staff" value={report.required_staff} />
              </div>
              {!report.hours.length ? (
                <DashboardEmpty label="coverage hours" />
              ) : (
                <div className="daily-coverage" aria-label="Daily coverage distribution">
                  {dailyCoverage.map((day) => (
                    <div
                      className="daily-coverage__row"
                      key={day.key}
                      aria-label={`${day.label}: ${day.adequate} adequate hours, ${day.understaffed} understaffed hours`}
                    >
                      <strong>{day.label}</strong>
                      <div className="daily-coverage__track" aria-hidden="true">
                        <span style={{ width: `${(day.adequate / day.total) * 100}%` }} />
                        <span style={{ width: `${(day.understaffed / day.total) * 100}%` }} />
                      </div>
                      <span>{day.adequate}/{day.total} adequate</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        }}
      </DashboardResourceState>
    </section>
  );
}

function summarizeCoverageByDay(report: CoverageReport) {
  const days = new Map<string, { key: string; label: string; adequate: number; understaffed: number; total: number }>();
  report.hours.forEach((hour) => {
    const date = new Date(hour.hour_start);
    const key = dayKeyFormatter.format(date);
    const day = days.get(key) ?? {
      key,
      label: dayLabelFormatter.format(date),
      adequate: 0,
      understaffed: 0,
      total: 0,
    };
    day.total += 1;
    if (hour.coverage_status === "adequate") day.adequate += 1;
    else day.understaffed += 1;
    days.set(key, day);
  });
  return [...days.values()].sort((left, right) => left.key.localeCompare(right.key));
}

function DashboardResourceState<T>({
  resource,
  label,
  children,
}: {
  resource: Resource<T>;
  label: string;
  children: (data: T) => React.ReactNode;
}) {
  if (resource.isLoading) {
    return <DashboardLoading label={label} />;
  }
  if (resource.error || !resource.data) {
    return (
      <div className="dashboard-state dashboard-state--error" role="alert">
        <strong>{label.charAt(0).toUpperCase() + label.slice(1)} unavailable</strong>
        <p>{resource.error ?? "The requested data could not be loaded."}</p>
        <button className="secondary-button" type="button" onClick={resource.retry}>Try again</button>
      </div>
    );
  }
  return <>{children(resource.data)}</>;
}

function DashboardLoading({ label }: { label: string }) {
  return (
    <div className="dashboard-state" role="status">
      <span className="loading-indicator" aria-hidden="true" />
      <strong>Loading {label}</strong>
    </div>
  );
}

function DashboardEmpty({ label }: { label: string }) {
  return <p className="dashboard-empty">No {label} found for CAFE001.</p>;
}

function DashboardSectionHeading({
  eyebrow,
  title,
  id,
  detail,
  link,
}: {
  eyebrow: string;
  title: string;
  id: string;
  detail: string;
  link?: { label: string; to: string };
}) {
  return (
    <div className="dashboard-section__heading">
      <div>
        <p className="page__eyebrow">{eyebrow}</p>
        <h2 id={id}>{title}</h2>
        <span>{detail}</span>
      </div>
      {link && <Link className="dashboard-link" to={link.to}>{link.label}<span aria-hidden="true">→</span></Link>}
    </div>
  );
}

function MiniMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning" | "danger";
}) {
  return (
    <div className={`dashboard-mini-metric${tone ? ` dashboard-mini-metric--${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{numberFormatter.format(value)}</strong>
    </div>
  );
}

export default DashboardPage;
export { HISTORY_END_AT, HISTORY_START_AT, formatMoney, summarizeCoverageByDay };
