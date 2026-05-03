import { Suspense, lazy } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Atom, Box, BookOpen, Boxes, FlaskConical, Gauge, PlaySquare, ShieldCheck } from "lucide-react";
import { api } from "./api";

const Dashboard = lazy(() => import("./pages/Dashboard").then((module) => ({ default: module.Dashboard })));
const Cases = lazy(() => import("./pages/Cases").then((module) => ({ default: module.Cases })));
const Builder = lazy(() => import("./pages/Builder").then((module) => ({ default: module.Builder })));
const Runs = lazy(() => import("./pages/Runs").then((module) => ({ default: module.Runs })));
const Docs = lazy(() => import("./pages/Docs").then((module) => ({ default: module.Docs })));
const Viewer = lazy(() => import("./pages/Viewer").then((module) => ({ default: module.Viewer })));
const Admin = lazy(() => import("./pages/Admin").then((module) => ({ default: module.Admin })));

const navigation = [
  { to: "/", label: "Dashboard", icon: Gauge },
  { to: "/cases", label: "Cases", icon: Atom },
  { to: "/builder", label: "Builder", icon: PlaySquare },
  { to: "/runs", label: "Runs", icon: Activity },
  { to: "/docs", label: "Science", icon: BookOpen },
  { to: "/viewer", label: "3D", icon: Box }
];

export function App() {
  const session = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const items = session.data?.is_admin ? [...navigation, { to: "/admin", label: "Admin", icon: ShieldCheck }] : navigation;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Boxes aria-hidden="true" />
          <div>
            <strong>Thorium Lab</strong>
            <span>MSR simulation</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="Primary navigation">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
                <Icon aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-note">
          <FlaskConical aria-hidden="true" />
          <span>{session.data?.email ?? "Draft runs write isolated bundle snapshots."}</span>
        </div>
      </aside>
      <main className="main-panel">
        <Suspense fallback={<div className="route-loading">Loading...</div>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/cases" element={<Cases />} />
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
      </main>
    </div>
  );
}
