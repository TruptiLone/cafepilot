import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import AgentPage from "./pages/AgentPage";
import DashboardPage from "./pages/DashboardPage";
import InventoryPage from "./pages/InventoryPage";
import ReceivingPage from "./pages/ReceivingPage";
import SalesPage from "./pages/SalesPage";
import StaffingPage from "./pages/StaffingPage";

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="agent" element={<AgentPage />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="receiving" element={<ReceivingPage />} />
        <Route path="sales" element={<SalesPage />} />
        <Route path="staffing" element={<StaffingPage />} />
        <Route index element={<Navigate replace to="/dashboard" />} />
        <Route path="*" element={<Navigate replace to="/dashboard" />} />
      </Route>
    </Routes>
  );
}

export default App;
