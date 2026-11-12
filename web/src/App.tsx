import { useEffect, useState } from "react";
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { CasePage } from "./pages/CasePage";
import { QueuePage } from "./pages/QueuePage";
import { OwnerRoutes } from "./pages/owner/OwnerRoutes";

function Mark() {
  return (
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#f4f6f2" />
      <path d="M7 11h18v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1z" fill="none" stroke="#1f4d3f" strokeWidth="2" />
      <path d="m7.5 11.5 8.5 7 8.5-7" fill="none" stroke="#1f4d3f" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Rail() {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const location = useLocation();

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .counts()
        .then((c) => alive && setCounts(c))
        .catch(() => undefined);
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [location.pathname]);

  const badge = (status: string) =>
    counts[status] ? <span className="badge">{counts[status]}</span> : null;

  return (
    <nav className="rail" aria-label="Main">
      <Link to="/review" className="brand">
        <Mark />
        <div>
          <strong>Ops Inbox</strong>
          <span>Larkspur Classic Car Hire</span>
        </div>
      </Link>
      <h2>Operations</h2>
      <NavLink to="/review">To review {badge("review")}</NavLink>
      <NavLink to="/ready">Ready to send {badge("ready")}</NavLink>
      <NavLink to="/processing">In progress {badge("processing")}</NavLink>
      <NavLink to="/done">Done</NavLink>
      <NavLink to="/rejected">Rejected</NavLink>
      <h2>Owner</h2>
      <NavLink to="/owner/dashboard">Dashboard</NavLink>
      <NavLink to="/owner/rules">Rules</NavLink>
      <NavLink to="/owner/thresholds">Automation level</NavLink>
      <NavLink to="/owner/templates">Templates</NavLink>
      <NavLink to="/owner/records">Records</NavLink>
      <p className="hint">
        Press <kbd>?</kbd> for shortcuts
      </p>
    </nav>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <div className="shell">
        <Rail />
        <main>
          <Routes>
            <Route path="/" element={<Navigate to="/review" replace />} />
            <Route path="/review" element={<QueuePage status="review" />} />
            <Route path="/ready" element={<QueuePage status="ready" />} />
            <Route path="/processing" element={<QueuePage status="processing" />} />
            <Route path="/done" element={<QueuePage status="done" />} />
            <Route path="/rejected" element={<QueuePage status="rejected" />} />
            <Route path="/cases/:id" element={<CasePage />} />
            <Route path="/owner/*" element={<OwnerRoutes />} />
            <Route path="*" element={<Navigate to="/review" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
