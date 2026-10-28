import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getInventoryData,
  getInventoryMovements,
  getStockCountDetail,
  getStockEstimates,
  type InventoryData,
  type InventoryMovement,
  type StockCountDetail,
} from "../api/inventory";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import MovementEntryModal from "../components/MovementEntryModal";
import StatusBadge from "../components/StatusBadge";

type InventoryTab = "ingredients" | "counts" | "movements" | "stock";

const tabs: { id: InventoryTab; label: string }[] = [
  { id: "ingredients", label: "Ingredients" },
  { id: "counts", label: "Stock Counts" },
  { id: "movements", label: "Stock Movements" },
  { id: "stock", label: "Current Stock" },
];

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Los_Angeles",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const quantityFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 3,
});

function formatDate(value: string | null) {
  return value ? dateFormatter.format(new Date(value)) : "Not recorded";
}

function formatQuantity(value: string) {
  return quantityFormatter.format(Number(value));
}

function formatCategory(value: string) {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function InventoryPage() {
  const [activeTab, setActiveTab] = useState<InventoryTab>("ingredients");
  const [data, setData] = useState<InventoryData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCountId, setSelectedCountId] = useState<string | null>(null);
  const [countDetail, setCountDetail] = useState<StockCountDetail | null>(null);
  const [countDetailError, setCountDetailError] = useState<string | null>(null);
  const [isCountDetailLoading, setIsCountDetailLoading] = useState(false);
  const [isMovementEntryOpen, setIsMovementEntryOpen] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const countDetailController = useRef<AbortController | null>(null);

  const loadInventory = useCallback(async (signal?: AbortSignal) => {
    setIsLoading(true);
    setError(null);
    try {
      setData(await getInventoryData(signal));
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
      if (!signal?.aborted) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadInventory(controller.signal);
    return () => {
      controller.abort();
      countDetailController.current?.abort();
    };
  }, [loadInventory]);

  const itemLookup = useMemo(
    () => new Map(data?.items.map((item) => [item.ingredient_id, item]) ?? []),
    [data?.items],
  );

  const toggleCountDetail = async (countId: string) => {
    if (selectedCountId === countId) {
      countDetailController.current?.abort();
      countDetailController.current = null;
      setSelectedCountId(null);
      setCountDetail(null);
      setIsCountDetailLoading(false);
      return;
    }

    countDetailController.current?.abort();
    const controller = new AbortController();
    countDetailController.current = controller;
    setSelectedCountId(countId);
    setCountDetail(null);
    setCountDetailError(null);
    setIsCountDetailLoading(true);
    try {
      const detail = await getStockCountDetail(countId, controller.signal);
      if (countDetailController.current === controller && !controller.signal.aborted) {
        setCountDetail(detail);
      }
    } catch (requestError) {
      if (!(requestError instanceof DOMException && requestError.name === "AbortError")) {
        setCountDetailError(
          requestError instanceof Error
            ? requestError.message
            : "The count details could not be loaded.",
        );
      }
    } finally {
      if (countDetailController.current === controller) {
        countDetailController.current = null;
        setIsCountDetailLoading(false);
      }
    }
  };

  const handleMovementSaved = async (_movement: InventoryMovement) => {
    const [movements, stock] = await Promise.all([
      getInventoryMovements(),
      getStockEstimates(),
    ]);
    setData((currentData) =>
      currentData ? { ...currentData, movements, stock } : currentData,
    );
    setSuccessMessage(
      "Movement saved. Stock Movements and Current Stock have been refreshed.",
    );
    setIsMovementEntryOpen(false);
  };

  return (
    <section className="page page--inventory" aria-labelledby="page-title">
      <div className="page__heading inventory-heading">
        <div>
          <p className="page__eyebrow">Business Management</p>
          <h1 id="page-title">Inventory</h1>
          <p className="page__description">
            Review ingredients, physical observations, stock movements, and
            accounting estimates for CAFE001.
          </p>
        </div>
        <div className="demo-label">
          <span aria-hidden="true" />
          Local Demo Database
        </div>
      </div>

      <div className="historical-notice">
        Seeded history is synthetic and dated January 6–12, 2025. New entries are
        saved separately to this local database. Times use America/Los_Angeles.
      </div>

      {successMessage && (
        <div className="success-message" role="status">
          <span>{successMessage}</span>
          <button
            type="button"
            aria-label="Dismiss success message"
            onClick={() => setSuccessMessage(null)}
          >
            ×
          </button>
        </div>
      )}

      <div className="inventory-panel">
        <div className="inventory-tabs" role="tablist" aria-label="Inventory views">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`inventory-tab${activeTab === tab.id ? " is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`inventory-panel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
              {data && (
                <span className="tab-count">
                  {tab.id === "ingredients" && data.items.length}
                  {tab.id === "counts" && data.counts.length}
                  {tab.id === "movements" && data.movements.length}
                  {tab.id === "stock" && data.stock.length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div
          className="inventory-panel__body"
          id={`inventory-panel-${activeTab}`}
          role="tabpanel"
        >
          {!isLoading && !error && data && activeTab === "movements" && (
            <div className="inventory-toolbar">
              <div>
                <strong>Stock Movements</strong>
                <span>Append-only inventory events</span>
              </div>
              <button
                className="primary-button"
                type="button"
                onClick={() => {
                  setSuccessMessage(null);
                  setIsMovementEntryOpen(true);
                }}
              >
                <span aria-hidden="true">+</span> New Entry
              </button>
            </div>
          )}
          {isLoading && <LoadingState />}
          {!isLoading && error && (
            <ErrorState message={error} onRetry={() => void loadInventory()} />
          )}
          {!isLoading && !error && data && (
            <>
              {activeTab === "ingredients" && (
                <IngredientsTable data={data} />
              )}
              {activeTab === "counts" && (
                <CountsTable
                  data={data}
                  selectedCountId={selectedCountId}
                  countDetail={countDetail}
                  detailError={countDetailError}
                  isDetailLoading={isCountDetailLoading}
                  itemLookup={itemLookup}
                  onToggleDetail={toggleCountDetail}
                />
              )}
              {activeTab === "movements" && (
                <MovementsTable data={data} itemLookup={itemLookup} />
              )}
              {activeTab === "stock" && <StockTable data={data} />}
            </>
          )}
        </div>
      </div>

      {isMovementEntryOpen && data && (
        <MovementEntryModal
          items={data.items.filter((item) => item.active)}
          onCancel={() => setIsMovementEntryOpen(false)}
          onSaved={handleMovementSaved}
        />
      )}
    </section>
  );
}

function IngredientsTable({ data }: { data: InventoryData }) {
  if (!data.items.length) return <EmptyState label="ingredients" />;
  return (
    <DataTable>
      <thead>
        <tr>
          <th>Ingredient ID</th>
          <th>Name</th>
          <th>Category</th>
          <th>Base Unit</th>
          <th>Active Status</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((item) => (
          <tr key={item.ingredient_id}>
            <td className="identifier-cell">{item.ingredient_id}</td>
            <td className="primary-cell">{item.name}</td>
            <td>{formatCategory(item.category)}</td>
            <td>{item.base_unit}</td>
            <td><StatusBadge status={item.active ? "active" : "inactive"} /></td>
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}

type CountTableProps = {
  data: InventoryData;
  selectedCountId: string | null;
  countDetail: StockCountDetail | null;
  detailError: string | null;
  isDetailLoading: boolean;
  itemLookup: Map<string, InventoryData["items"][number]>;
  onToggleDetail: (countId: string) => void;
};

function CountsTable({
  data,
  selectedCountId,
  countDetail,
  detailError,
  isDetailLoading,
  itemLookup,
  onToggleDetail,
}: CountTableProps) {
  if (!data.counts.length) return <EmptyState label="stock counts" />;
  return (
    <>
      <DataTable>
        <thead>
          <tr>
            <th>Count ID</th>
            <th>Observation Date</th>
            <th>Status</th>
            <th>Counted By</th>
            <th>Number of Items</th>
            <th><span className="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {data.counts.map((count) => (
            <tr key={count.count_id}>
              <td className="identifier-cell">{count.count_id}</td>
              <td>{formatDate(count.counted_at)}</td>
              <td><StatusBadge status={count.status} /></td>
              <td>{count.counted_by ?? "—"}</td>
              <td>{count.item_count}</td>
              <td className="action-cell">
                <button
                  className="text-button"
                  type="button"
                  aria-expanded={selectedCountId === count.count_id}
                  onClick={() => onToggleDetail(count.count_id)}
                >
                  {selectedCountId === count.count_id ? "Close" : "View details"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>

      {selectedCountId && (
        <div className="count-detail" aria-live="polite">
          <div className="count-detail__heading">
            <div>
              <p className="page__eyebrow">Count Detail</p>
              <h2>{selectedCountId}</h2>
            </div>
            {countDetail && <span>{countDetail.lines.length} ingredients</span>}
          </div>
          {isDetailLoading && <LoadingState />}
          {detailError && <p className="inline-error">{detailError}</p>}
          {countDetail && (
            <DataTable compact>
              <thead>
                <tr>
                  <th>Ingredient</th>
                  <th>Ingredient ID</th>
                  <th>Counted Quantity</th>
                  <th>Unit</th>
                </tr>
              </thead>
              <tbody>
                {countDetail.lines.map((line) => {
                  const item = itemLookup.get(line.ingredient_id);
                  return (
                    <tr key={line.count_line_id}>
                      <td className="primary-cell">{item?.name ?? line.ingredient_id}</td>
                      <td className="identifier-cell">{line.ingredient_id}</td>
                      <td>{formatQuantity(line.counted_quantity)}</td>
                      <td>{item?.base_unit ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </DataTable>
          )}
        </div>
      )}
    </>
  );
}

function MovementsTable({
  data,
  itemLookup,
}: {
  data: InventoryData;
  itemLookup: Map<string, InventoryData["items"][number]>;
}) {
  if (!data.movements.length) return <EmptyState label="stock movements" />;
  return (
    <DataTable>
      <thead>
        <tr>
          <th>Ingredient</th>
          <th>Movement Type</th>
          <th>Quantity</th>
          <th>Unit</th>
          <th>Event Date</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {data.movements.map((movement) => {
          const quantity = Number(movement.quantity_delta);
          return (
            <tr key={movement.movement_id}>
              <td className="primary-cell">
                {itemLookup.get(movement.ingredient_id)?.name ?? movement.ingredient_id}
              </td>
              <td><StatusBadge status={movement.movement_type} /></td>
              <td className={quantity > 0 ? "quantity-positive" : "quantity-negative"}>
                {quantity > 0 ? "+" : ""}{formatQuantity(movement.quantity_delta)}
              </td>
              <td>{movement.base_unit}</td>
              <td>{formatDate(movement.occurred_at)}</td>
              <td>{movement.reason ?? "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}

function StockTable({ data }: { data: InventoryData }) {
  if (!data.stock.length) return <EmptyState label="stock estimates" />;
  return (
    <DataTable>
      <thead>
        <tr>
          <th>Ingredient</th>
          <th>Estimated Quantity</th>
          <th>Unit</th>
          <th>Stock Status</th>
          <th>Last Verified Count</th>
        </tr>
      </thead>
      <tbody>
        {data.stock.map((stock) => (
          <tr key={stock.ingredient_id}>
            <td className="primary-cell">{stock.name}</td>
            <td className={stock.stock_status === "unknown" ? "unknown-value" : ""}>
              {stock.estimated_quantity === null
                ? "Unknown"
                : formatQuantity(stock.estimated_quantity)}
            </td>
            <td>{stock.estimated_quantity === null ? "—" : stock.base_unit}</td>
            <td><StatusBadge status={stock.stock_status} /></td>
            <td>
              {stock.latest_verified_counted_at ? (
                <span className="stacked-cell">
                  <span>{formatDate(stock.latest_verified_counted_at)}</span>
                  <small>{stock.latest_verified_count_id}</small>
                </span>
              ) : "No verified count"}
            </td>
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}

function DataTable({
  children,
  compact = false,
}: {
  children: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <div className="table-scroll">
      <table className={`data-table${compact ? " data-table--compact" : ""}`}>
        {children}
      </table>
    </div>
  );
}

export default InventoryPage;
