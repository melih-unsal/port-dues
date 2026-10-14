import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { money, usd } from "../format";
import type { ChargeRule, Condition, DocumentInfo, Rulebook, SectionDetail, TreeNode } from "../types";
import { Markdown } from "./ui";

export default function DocumentInspector({ doc, onClose }: { doc: DocumentInfo; onClose: () => void }) {
  const [tab, setTab] = useState<"tree" | "rules">("tree");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`Inspect ${doc.name}`}>
        <div className="card-head">
          <div>
            <h2>{doc.name}</h2>
            <div className="muted small">What the ingestion pipeline built from this document</div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>Close</button>
        </div>
        <div className="tabs">
          <button className={tab === "tree" ? "on" : ""} onClick={() => setTab("tree")}>Clause tree</button>
          <button className={tab === "rules" ? "on" : ""} onClick={() => setTab("rules")}>Compiled rulebook</button>
        </div>
        <div className="drawer-body">{tab === "tree" ? <TreeView docId={doc.id} /> : <RulebookView docId={doc.id} />}</div>
      </div>
    </div>
  );
}

function TreeView({ docId }: { docId: string }) {
  const [nodes, setNodes] = useState<TreeNode[] | null>(null);
  const [selected, setSelected] = useState<TreeNode | null>(null);
  const [detail, setDetail] = useState<SectionDetail | null>(null);
  const [view, setView] = useState<"digest" | "facts" | "source">("digest");

  useEffect(() => {
    api.tree(docId).then((n) => {
      setNodes(n);
      setSelected(n[0] ?? null);
    });
  }, [docId]);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    api.section(docId, selected.id).then(setDetail);
  }, [docId, selected]);

  if (!nodes) return <div className="empty"><span className="spinner" /></div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 320px) 1fr", height: "100%" }}>
      <div style={{ borderRight: "1px solid var(--border)", overflow: "auto", padding: "8px 6px" }}>
        {nodes.map((n) => (
          <button
            key={n.id}
            onClick={() => setSelected(n)}
            className="btn btn-ghost btn-sm"
            style={{
              width: "100%",
              justifyContent: "flex-start",
              paddingLeft: 8 + n.depth * 14,
              background: selected?.id === n.id ? "var(--accent-soft)" : undefined,
              color: selected?.id === n.id ? "var(--accent)" : undefined,
              fontWeight: n.depth <= 1 ? 600 : 480,
            }}
            title={`Tree level ${n.level} · ${n.facts} facts`}
          >
            <span className="mono muted" style={{ minWidth: 34, textAlign: "left" }}>{n.section || "root"}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.title}</span>
          </button>
        ))}
      </div>
      <div style={{ overflow: "auto", padding: 20 }}>
        {selected && (
          <>
            <div className="row" style={{ marginBottom: 6 }}>
              <h2 className="grow">{selected.section ? `§${selected.section} ` : ""}{selected.title}</h2>
            </div>
            <div className="row-wrap" style={{ marginBottom: 14 }}>
              <span className="chip">tree level <b>{selected.level}</b></span>
              <span className="chip"><b>{selected.facts}</b> facts</span>
              {selected.pages.length > 0 && <span className="chip">pages <b>{selected.pages.join(", ")}</b></span>}
              <span className="grow" />
              <div className="segmented">
                {(["digest", "facts", "source"] as const).map((v) => (
                  <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
                    {v === "digest" ? "Digest" : v === "facts" ? "Facts" : "Verbatim"}
                  </button>
                ))}
              </div>
            </div>
            {!detail ? (
              <span className="spinner" />
            ) : view === "digest" ? (
              <Markdown>{detail.digest}</Markdown>
            ) : view === "facts" ? (
              detail.facts.length ? (
                <table className="table">
                  <tbody>
                    {detail.facts.map((f, i) => (
                      <tr key={i}>
                        <td style={{ width: 96 }}><span className="chip">{f.category}</span></td>
                        <td>{f.text}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="muted">This clause has no text of its own; see its sub-clauses.</div>
              )
            ) : detail.source ? (
              <Markdown>{detail.source}</Markdown>
            ) : (
              <div className="muted">No verbatim text at this level.</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function cond(c: Condition) {
  const v = Array.isArray(c.value) ? c.value.join(" / ") : c.value === null ? "" : String(c.value);
  const op = { eq: "=", ne: "≠", in: "∈", not_in: "∉", contains: "contains", gt: ">", gte: "≥", lt: "<", lte: "≤", is_true: "is true", is_false: "is false" }[c.op] ?? c.op;
  return `${c.field} ${op} ${v}`.trim();
}

function RulebookView({ docId }: { docId: string }) {
  const [book, setBook] = useState<Rulebook | null | undefined>(undefined);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.rulebook(docId).then(setBook).catch(() => setBook(null));
  }, [docId]);

  const totals = useMemo(() => book && { rules: book.charges.length, uncovered: book.uncovered.length }, [book]);

  if (book === undefined) return <div className="empty"><span className="spinner" /></div>;
  if (book === null) return <div className="empty">No rulebook yet. It is produced in the last ingestion stage.</div>;

  return (
    <div style={{ padding: 20 }} className="stack">
      <div className="row-wrap">
        <span className="chip">currency <b>{book.currency}</b></span>
        <span className="chip"><b>{totals?.rules}</b> compiled</span>
        <span className="chip"><b>{totals?.uncovered}</b> not compiled</span>
        {book.compiled_with?.model && (
          <span className="chip" title={`${book.compiled_with.repairs ?? 0} repair rounds, ${book.compiled_with.failed ?? 0} failed`}>
            compiled by <span className="mono">{book.compiled_with.model}</span>
            {book.compiled_with.tokens?.cost_usd !== undefined && <> · <b>{usd(book.compiled_with.tokens.cost_usd)}</b></>}
          </span>
        )}
        {book.variables.map((v) => (
          <span key={v.name} className="chip" title={v.description}>+ var <b className="mono">{v.name}</b></span>
        ))}
      </div>
      {book.tax_note && <div className="muted small">{book.tax_note}</div>}

      {book.charges.map((r) => (
        <div key={r.id} className="card">
          <button className="btn btn-ghost" style={{ width: "100%", justifyContent: "space-between", padding: "12px 16px" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
            <span className="row">
              <b>{r.name}</b>
              <span className="muted mono small">§{r.section}</span>
              {r.payer === "cargo" && <span className="chip">cargo</span>}
            </span>
            <span className="muted small">{r.variants.length} variant{r.variants.length === 1 ? "" : "s"} · {r.adjustments.length} adj. · {r.exemptions.length} exempt.</span>
          </button>
          {open === r.id && <RuleDetail rule={r} docId={docId} />}
        </div>
      ))}

      {book.uncovered.length > 0 && (
        <div className="card card-pad">
          <h3 style={{ marginBottom: 8 }}>Routed to the research agent at query time</h3>
          <table className="table">
            <tbody>
              {book.uncovered.map((u) => (
                <tr key={u.name}>
                  <td><b>{u.name}</b></td>
                  <td className="mono muted">§{u.section}</td>
                  <td className="ink-2">{u.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RuleDetail({ rule, docId }: { rule: ChargeRule; docId: string }) {
  const [state, setState] = useState<"idle" | "sent" | "error">("idle");
  return (
    <div style={{ padding: "0 16px 16px" }} className="stack">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="ink-2 grow">{rule.description}</div>
        <button className="btn btn-sm" disabled={state === "sent"} title="Re-run the compiler agent for this charge only"
          onClick={() => api.recompileRule(docId, rule.id).then(() => setState("sent"), () => setState("error"))}>
          {state === "sent" ? "Recompiling…" : state === "error" ? "Failed, retry" : "Recompile this rule"}
        </button>
      </div>
      {rule.applies_when.length > 0 && <div className="small"><span className="muted">Applies when </span>{rule.applies_when.map(cond).join(" and ")}</div>}
      <table className="table">
        <thead><tr><th>Variant</th><th>When</th><th>Formula</th></tr></thead>
        <tbody>
          {rule.variants.map((v) => (
            <tr key={v.label}>
              <td><b>{v.label}</b></td>
              <td className="small ink-2">{v.when.length ? v.when.map(cond).join(" and ") : "otherwise"}</td>
              <td><code>{v.formula}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
      {rule.constants.length > 0 && (
        <div className="row-wrap">
          {rule.constants.map((c) => (
            <span key={c.name} className="chip" title={c.source}><span className="mono">{c.name}</span> = <b className="num">{c.value}</b></span>
          ))}
        </div>
      )}
      {rule.tables.map((t) => {
        const cols = Array.from(new Set(t.bands.flatMap((b) => b.values.map((v) => v.name))));
        // keyed tables (e.g. commodities) have a row name instead of a numeric range
        const keyed = t.bands.some((b) => b.key);
        return (
          <div key={t.name}>
            <div className="small" style={{ marginBottom: 4 }}><b className="mono">{t.name}</b> <span className="muted">{t.description}</span></div>
            <table className="table">
              <thead>
                <tr>
                  {keyed ? <th>Item</th> : <><th>From</th><th>To</th></>}
                  {cols.map((c) => <th key={c} className="r">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {t.bands.map((b, i) => (
                  <tr key={i}>
                    {keyed ? (
                      <td>{b.key ?? "—"}</td>
                    ) : (
                      <>
                        <td className="num">{b.lower === null ? "—" : b.lower.toLocaleString()}</td>
                        <td className="num">{b.upper === null ? "∞" : b.upper.toLocaleString()}</td>
                      </>
                    )}
                    {cols.map((c) => <td key={c} className="r num">{money(b.values.find((v) => v.name === c)?.value)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
      {(rule.minimum !== null || rule.maximum !== null) && (
        <div className="small">{rule.minimum !== null && <>Minimum <b>{money(rule.minimum)}</b> </>}{rule.maximum !== null && <>Maximum <b>{money(rule.maximum)}</b></>}</div>
      )}
      {rule.adjustments.length > 0 && (
        <div className="small stack" style={{ gap: 4 }}>
          {rule.adjustments.map((a) => (
            <div key={a.name}>
              <b style={{ color: a.kind === "surcharge" ? "var(--critical)" : "var(--good-ink)" }}>{a.kind === "surcharge" ? "+" : "−"}{a.percent}%</b> {a.name}
              <span className="muted"> when {a.when.map(cond).join(" and ")}</span>
            </div>
          ))}
        </div>
      )}
      {rule.exemptions.length > 0 && (
        <div className="small">
          <span className="muted">Exempt: </span>
          {rule.exemptions.map((e) => e.description).join("; ")}
        </div>
      )}
      {rule.notes && <div className="small muted">{rule.notes}</div>}
    </div>
  );
}
