import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import InventoryPage from "./pages/InventoryPage";
import PagePlaceholder from "./pages/PagePlaceholder";
import SalesPage from "./pages/SalesPage";
import StaffingPage from "./pages/StaffingPage";

const pages = {
  dashboard: {
    title: "Dashboard",
    description:
      "A clear overview of café performance across inventory, sales, and staffing.",
  },
};

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="sales" element={<SalesPage />} />
        <Route path="staffing" element={<StaffingPage />} />
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
