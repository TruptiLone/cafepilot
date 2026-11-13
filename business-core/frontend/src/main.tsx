import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { watchForLockedApi } from "./api/demo";
import AccessGate from "./components/AccessGate";
import "./fonts.css";
import "./styles.css";

watchForLockedApi();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AccessGate>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AccessGate>
  </StrictMode>,
);
