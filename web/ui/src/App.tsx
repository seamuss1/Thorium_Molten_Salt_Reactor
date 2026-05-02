import { NavLink, Route, Routes } from "react-router-dom";
import { Activity, Atom, Box, BookOpen, Boxes, FlaskConical, Gauge, PlaySquare } from "lucide-react";
import { Dashboard } from "./pages/Dashboard";
import { Cases } from "./pages/Cases";
import { Builder } from "./pages/Builder";
import { Runs } from "./pages/Runs";
import { Docs } from "./pages/Docs";
import { Viewer } from "./pages/Viewer";

const navigation = [
  { to: "/", label: "Dashboard", icon: Gauge },
  { to: "/cases", label: "Cases", icon: Atom },
  { to: "/builder", label: "Builder", icon: PlaySquare },
  { to: "/runs", label: "Runs", icon: Activity },
  { to: "/docs", label: "Science", icon: BookOpen },
  { to: "/viewer", label: "3D", icon: Box }
];

export function App() {
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
          {navigation.map((item) => {
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
          <span>Draft runs write isolated bundle snapshots.</span>
        </div>
      </aside>
      <main className="main-panel">
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
        </Routes>
      </main>
    </div>
  );
}
