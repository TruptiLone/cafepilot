import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import Brand from "./Brand";
import {
  AgentIcon,
  CloseIcon,
  DashboardIcon,
  InventoryIcon,
  MenuIcon,
  ReceivingIcon,
  SalesIcon,
  StaffingIcon,
} from "./Icons";

const navigation = [
  { label: "Ask CaféPilot", path: "/agent", icon: AgentIcon },
  { label: "Dashboard", path: "/dashboard", icon: DashboardIcon },
  { label: "Inventory", path: "/inventory", icon: InventoryIcon },
  { label: "Receive delivery", path: "/receiving", icon: ReceivingIcon },
  { label: "Sales", path: "/sales", icon: SalesIcon },
  { label: "Staffing", path: "/staffing", icon: StaffingIcon },
];

function AppShell() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setIsMenuOpen(false);
  }, [location.pathname]);

  return (
    <div className="app-frame">
      <header className="app-header">
        <Brand />
        <button
          className="icon-button mobile-menu-button"
          type="button"
          aria-label="Open navigation"
          aria-expanded={isMenuOpen}
          onClick={() => setIsMenuOpen(true)}
        >
          <MenuIcon />
        </button>
      </header>

      <div className="app-body">
        <button
          className={`navigation-scrim${isMenuOpen ? " is-visible" : ""}`}
          type="button"
          aria-label="Close navigation"
          onClick={() => setIsMenuOpen(false)}
        />

        <aside className={`sidebar${isMenuOpen ? " is-open" : ""}`}>
          <div className="sidebar__brand" aria-hidden="true">
            <span className="sidebar__mono">CP</span>CaféPilot
          </div>
          <div className="sidebar__mobile-header">
            <Brand compact />
            <button
              className="icon-button"
              type="button"
              aria-label="Close navigation"
              onClick={() => setIsMenuOpen(false)}
            >
              <CloseIcon />
            </button>
          </div>

          <nav className="navigation" aria-label="Primary navigation">
            {navigation.map(({ label, path, icon: Icon }) => (
              <NavLink
                key={path}
                className={({ isActive }) =>
                  `navigation__link${isActive ? " is-active" : ""}`
                }
                to={path}
              >
                <Icon />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="sidebar__footer">
            <strong>CafePilot Demo Cafe</strong>
            <span>Santa Clara, CA</span>
          </div>
        </aside>

        <main className={`main-content${location.pathname.startsWith("/agent") ? " main-content--flush" : ""}`}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default AppShell;
