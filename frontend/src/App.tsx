import { createContext, useContext, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import ChatPage from "./pages/Chat";
import DocumentsPage from "./pages/Documents";
import ExperimentsPage from "./pages/Experiments";
import type { Meta } from "./types";

const MetaContext = createContext<Meta | null>(null);
export const useMeta = () => useContext(MetaContext);

type Theme = "system" | "light" | "dark";

function readTheme(): Theme {
  try {
    return (localStorage.getItem("theme") as Theme) || "system";
  } catch {
    return "system";
  }
}

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* private mode */
    }
  }, [theme]);
  const next = () => setTheme((t) => (t === "system" ? "light" : t === "light" ? "dark" : "system"));
  return [theme, next];
}

const Icon = ({ d }: { d: string }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
);

const NAV = [
  { to: "/documents", label: "Documents", icon: "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5" },
  { to: "/chat", label: "Chat", icon: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" },
  { to: "/experiments", label: "Experiments", icon: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3M7.5 14h9" },
];

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [theme, cycleTheme] = useTheme();

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
  }, []);

  return (
    <MetaContext.Provider value={meta}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinejoin="round">
                <path d="M3 15h18l-3 5H6zM12 3v10M12 5l6 6h-6" />
              </svg>
            </span>
            Port Dues
          </div>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <Icon d={n.icon} />
              {n.label}
            </NavLink>
          ))}
          <div className="sidebar-foot">
            {meta && (
              <>
                <span title={`judge: ${meta.models.judge}`}>Compiler · <span className="mono">{meta.models.compiler}</span></span>
                <span>Queries · <span className="mono">{meta.models.fast}</span></span>
                <span>Embeddings · <span className="mono">{meta.embedding_model}</span></span>
                <span>
                  Tracing ·{" "}
                  {meta.langfuse_url ? <a href={meta.langfuse_url} target="_blank" rel="noreferrer">Langfuse ↗</a> : "off"}
                </span>
              </>
            )}
            <button className="btn btn-sm btn-ghost" style={{ justifyContent: "flex-start", padding: "4px 0" }} onClick={cycleTheme}>
              Theme: {theme}
            </button>
          </div>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Navigate to="/documents" replace />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:docId" element={<ChatPage />} />
            <Route path="/chat/:docId/:convId" element={<ChatPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
            <Route path="/experiments/:expId" element={<ExperimentsPage />} />
          </Routes>
        </main>
      </div>
    </MetaContext.Provider>
  );
}
