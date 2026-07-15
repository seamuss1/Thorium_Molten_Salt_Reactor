import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Atom,
  Box,
  BookOpen,
  Boxes,
  Gauge,
  Menu,
  Monitor,
  Moon,
  PlaySquare,
  ShieldCheck,
  Sun,
  UserCircle2,
  X
} from "lucide-react";
import { api } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Truncate } from "./components/Truncate";
import { useTheme, type ThemeMode } from "./theme";

const Dashboard = lazy(() => import("./pages/Dashboard").then((module) => ({ default: module.Dashboard })));
const Cases = lazy(() => import("./pages/Cases").then((module) => ({ default: module.Cases })));
const Builder = lazy(() => import("./pages/Builder").then((module) => ({ default: module.Builder })));
const Runs = lazy(() => import("./pages/Runs").then((module) => ({ default: module.Runs })));
const Docs = lazy(() => import("./pages/Docs").then((module) => ({ default: module.Docs })));
const Viewer = lazy(() => import("./pages/Viewer").then((module) => ({ default: module.Viewer })));
const Admin = lazy(() => import("./pages/Admin").then((module) => ({ default: module.Admin })));

const navigation = [
  { to: "/", label: "Dashboard", icon: Gauge },
  { to: "/cases", label: "Simulations", icon: Atom },
  { to: "/builder", label: "Builder", icon: PlaySquare },
  { to: "/runs", label: "Runs", icon: Activity },
  { to: "/docs", label: "Science", icon: BookOpen },
  { to: "/viewer", label: "3D viewer", icon: Box }
];

export function App() {
  const session = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const items = session.data?.is_admin ? [...navigation, { to: "/admin", label: "Admin", icon: ShieldCheck }] : navigation;
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <div className="app-shell">
      <aside className={`sidebar${menuOpen ? " menu-open" : ""}`}>
        <div className="brand">
          <Boxes aria-hidden="true" />
          <div>
            <strong>Thorium Lab</strong>
            <span>MSR simulation console</span>
          </div>
          <button
            type="button"
            className="menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="primary-navigation"
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
          </button>
        </div>
        <nav id="primary-navigation" className="nav-list" aria-label="Primary navigation">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) => (isActive ? "active" : "")}
                aria-label={item.label}
              >
                <Icon aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="account-block">
          <ThemeToggle />
          <AccountCard
            status={session.status}
            email={session.data?.email}
            isAdmin={session.data?.is_admin ?? false}
          />
        </div>
      </aside>
      <main className="main-panel">
        <ErrorBoundary resetKey={location.pathname}>
          <Suspense fallback={<div className="route-loading">Loading…</div>}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/cases" element={<Cases />} />
              <Route path="/cases/:caseName" element={<Cases />} />
              <Route path="/builder" element={<Builder />} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/:caseName/:runId" element={<Runs />} />
              <Route path="/docs" element={<Docs />} />
              <Route path="/docs/:slug" element={<Docs />} />
              <Route path="/viewer" element={<Viewer />} />
              <Route path="/viewer/:caseName/:runId" element={<Viewer />} />
              <Route path="/admin" element={<Admin />} />
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  );
}

function AccountCard({ status, email, isAdmin }: { status: "pending" | "error" | "success"; email?: string; isAdmin: boolean }) {
  const value = status === "pending" ? "Signing in…" : status === "error" ? "Session unavailable" : email ?? "Local session";
  return (
    <div className="account-card">
      <UserCircle2 aria-hidden="true" />
      <span className="account-label">Signed in</span>
      <Truncate className="account-value" title={value}>
        {value}
      </Truncate>
      {isAdmin && (
        <span className="account-badge">
          <ShieldCheck aria-hidden="true" width={12} height={12} />
          Admin
        </span>
      )}
    </div>
  );
}

const themeOptions: Array<{ value: ThemeMode; icon: typeof Sun; label: string }> = [
  { value: "light", icon: Sun, label: "Light theme" },
  { value: "system", icon: Monitor, label: "System theme" },
  { value: "dark", icon: Moon, label: "Dark theme" }
];

function ThemeToggle() {
  const { mode, setMode } = useTheme();
  return (
    <div className="theme-toggle" role="group" aria-label="Color theme">
      {themeOptions.map((option) => {
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            className={mode === option.value ? "active" : ""}
            aria-label={option.label}
            aria-pressed={mode === option.value}
            title={option.label}
            onClick={() => setMode(option.value)}
          >
            <Icon aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
