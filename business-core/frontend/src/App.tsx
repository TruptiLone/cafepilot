import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import InventoryPage from "./pages/InventoryPage";
import PagePlaceholder from "./pages/PagePlaceholder";

const pages = {
  dashboard: {
    title: "Dashboard",
    description:
      "A clear overview of café performance across inventory, sales, and staffing.",
  },
  sales: {
    title: "Sales",
    description:
      "Review completed sales, product performance, and business trends.",
  },
  staffing: {
    title: "Staffing",
    description:
      "Manage employees, scheduled shifts, and hourly staffing coverage.",
  },
};

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="inventory" element={<InventoryPage />} />
        {Object.entries(pages).map(([path, page]) => (
          <Route
            key={path}
            path={path}
            element={<PagePlaceholder {...page} />}
          />
        ))}
        <Route index element={<Navigate replace to="/dashboard" />} />
        <Route path="*" element={<Navigate replace to="/dashboard" />} />
      </Route>
    </Routes>
  );
}

export default App;
