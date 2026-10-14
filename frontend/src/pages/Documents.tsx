import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import DocumentInspector from "../components/DocumentInspector";
import { DocStatusPill, Progress } from "../components/ui";
import { ago, compact, seconds, tokenTitle, usd } from "../format";
import type { DocumentInfo, TokenUsage } from "../types";

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [inspect, setInspect] = useState<DocumentInfo | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const refresh = useCallback(() => api.documents().then(setDocs).catch((e) => setError(e.message)), []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // poll while anything is ingesting
  const busy = docs?.some((d) => d.status === "running" || d.status === "queued");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [busy, refresh]);

  async function upload(file: File) {
    setError(null);
    setNotice(null);
    if (file.type && file.type !== "application/pdf") {
      setError("Only PDF tariff documents are supported.");
      return;
    }
    setUploading(true);
    try {
      const doc = await api.upload(file);
      setNotice(doc.duplicate ? `“${doc.name}” was already uploaded, so its existing ingestion is reused.` : `Ingesting “${doc.name}”…`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Tariff documents</h1>
          <p>Upload a port tariff PDF once; it is parsed, structured, indexed and compiled into executable rules.</p>
        </div>
      </div>

      <div
        className="card card-pad"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) upload(file);
        }}
        style={{
          borderStyle: "dashed",
          borderWidth: 1.5,
          borderColor: dragging ? "var(--accent)" : "var(--border)",
          background: dragging ? "var(--accent-soft)" : undefined,
          display: "flex",
          alignItems: "center",
          gap: 16,
          marginBottom: 20,
        }}
      >
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
        </svg>
        <div className="grow">
          <h3>Drop a tariff PDF here</h3>
          <div className="muted small">Text pages come from the PDF text layer; pages with tables or figures are read by the vision model.</div>
        </div>
        <input ref={fileInput} type="file" accept="application/pdf" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <button className="btn btn-primary" disabled={uploading} onClick={() => fileInput.current?.click()}>
          {uploading ? <span className="spinner" /> : null} Choose file
        </button>
      </div>

      {notice && <div className="card card-pad small" style={{ marginBottom: 12 }}>{notice}</div>}
      {error && <div className="error-box" style={{ marginBottom: 12 }}>{error}</div>}

      {docs === null ? (
        <div className="empty"><span className="spinner" /></div>
      ) : docs.length === 0 ? (
        <div className="card empty">No documents yet. Upload a tariff book to get started.</div>
      ) : (
        <div className="stack">
          {docs.map((d) => (
            <div key={d.id} className="card card-pad">
              <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
                <div className="grow">
                  <div className="row" style={{ gap: 12 }}>
                    <h2 style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</h2>
                    <DocStatusPill status={d.status} />
                  </div>
                  <div className="muted small" style={{ marginTop: 2 }}>
                    {d.filename} · uploaded {ago(d.created_at)}
                  </div>
                </div>
                <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
                  <button className="btn btn-sm" onClick={() => setInspect(d)} disabled={!d.stats.sections}>Inspect</button>
                  <button className="btn btn-sm btn-primary" disabled={d.status !== "ready"} onClick={() => navigate(`/chat/${d.id}`)}>
                    Chat
                  </button>
                  <details style={{ position: "relative" }}>
                    <summary className="btn btn-sm btn-ghost" aria-label="More actions">•••</summary>
                    <div className="card" style={{ position: "absolute", right: 0, top: 34, zIndex: 5, padding: 6, display: "grid", minWidth: 190 }}>
                      <button className="btn btn-sm btn-ghost" style={{ justifyContent: "flex-start" }} disabled={d.status === "running"}
                        onClick={() => act(() => api.reingest(d.id, true))}>Recompile rules</button>
                      <button className="btn btn-sm btn-ghost" style={{ justifyContent: "flex-start" }} disabled={d.status === "running"}
                        onClick={() => act(() => api.reingest(d.id, false))}>Re-ingest from scratch</button>
                      <a className="btn btn-sm btn-ghost" style={{ justifyContent: "flex-start" }} href={api.fileUrl(d.id)} target="_blank" rel="noreferrer">Open PDF</a>
                      <button className="btn btn-sm btn-ghost btn-danger" style={{ justifyContent: "flex-start" }} disabled={d.status === "running"}
                        onClick={() => confirm(`Delete “${d.name}” and everything derived from it?`) && act(() => api.deleteDocument(d.id))}>
                        Delete
                      </button>
                    </div>
                  </details>
                </div>
              </div>

              {(d.status === "running" || d.status === "queued") && (
                <div style={{ marginTop: 14 }}>
                  <div className="row small" style={{ justifyContent: "space-between", marginBottom: 6 }}>
                    <span className="ink-2">{d.stage}</span>
                    <span className="num muted">{Math.round(d.progress * 100)}%</span>
                  </div>
                  <Progress value={d.progress} />
                  <PipelineSteps progress={d.progress} />
                </div>
              )}
              {d.status === "failed" && d.error && <div className="error-box" style={{ marginTop: 12 }}>{d.error}</div>}

              {d.stats.pages !== undefined && (
                <div className="row-wrap" style={{ marginTop: 14 }}>
                  <span className="chip"><b>{d.stats.pages}</b> pages</span>
                  <span className="chip"><b>{d.stats.vision_pages}</b> read by vision</span>
                  {d.stats.sections !== undefined && <span className="chip"><b>{d.stats.sections}</b> clauses</span>}
                  {d.stats.facts !== undefined && <span className="chip"><b>{d.stats.facts}</b> facts</span>}
                  {d.stats.tree_height !== undefined && <span className="chip"><b>{d.stats.tree_height}</b> tree levels</span>}
                  {d.stats.rules !== undefined && <span className="chip"><b>{d.stats.rules}</b> compiled rules</span>}
                  {!!d.stats.uncovered && <span className="chip"><b>{d.stats.uncovered}</b> left to research</span>}
                  {d.stats.seconds !== undefined && <span className="chip">built in <b>{seconds(d.stats.seconds)}</b></span>}
                  {d.stats.tokens && <TokenChips tokens={d.stats.tokens} />}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {inspect && <DocumentInspector doc={inspect} onClose={() => setInspect(null)} />}
    </div>
  );
}

const STEPS: [string, number][] = [
  ["Read pages", 0.35],
  ["Facts & digests", 0.7],
  ["Embed", 0.75],
  ["Compile rules", 1],
];

function PipelineSteps({ progress }: { progress: number }) {
  let start = 0;
  return (
    <div className="row-wrap small" style={{ marginTop: 10, gap: 16 }}>
      {STEPS.map(([label, end]) => {
        const state = progress >= end ? "done" : progress >= start ? "active" : "todo";
        start = end;
        return (
          <span key={label} className="row" style={{ gap: 6, color: state === "todo" ? "var(--muted)" : "var(--ink-2)" }}>
            {state === "done" ? <span style={{ color: "var(--good-ink)" }}>✓</span> : state === "active" ? <span className="spinner" /> : <span>○</span>}
            {label}
          </span>
        );
      })}
    </div>
  );
}

const STAGE_LABELS: Record<string, string> = { vision: "vision", tree: "facts + digests", compile: "compiler", recompile: "last recompile" };

function TokenChips({ tokens }: { tokens: Record<string, TokenUsage> }) {
  const stages = Object.entries(tokens);
  const total = stages.reduce((sum, [, t]) => sum + t.input + t.output, 0);
  const cost = stages.reduce((sum, [, t]) => sum + (t.cost_usd ?? 0), 0);
  return (
    <>
      <span className="chip"><b>{compact(total)}</b> tokens · <b>{usd(cost)}</b></span>
      {stages.map(([stage, t]) => (
        <span key={stage} className="chip" title={tokenTitle(t)}>
          {STAGE_LABELS[stage] ?? stage} <b>{compact(t.input + t.output)}</b>{t.cost_usd !== undefined && <> · {usd(t.cost_usd)}</>}
        </span>
      ))}
    </>
  );
}
