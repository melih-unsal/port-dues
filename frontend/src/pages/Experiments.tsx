import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMeta } from "../App";
import { api, streamEvents } from "../api";
import { GroupedBars, HBars, rampColor, ScoreBar, useTooltip } from "../components/Charts";
import { DifficultyBadge, Markdown, Progress } from "../components/ui";
import { ago, CHARGE_LABELS, compact, money, pct, seconds, tokenTitle, usd } from "../format";
import type { CaseResult, Difficulty, DocumentInfo, Experiment, GoldenCase, RunSummary } from "../types";

const DIFFS: Difficulty[] = ["easy", "medium", "hard"];

/** Runs from before the single-technique change stored one summary row per strategy. */
function summaryOf(exp: Experiment): RunSummary | null {
  const s = exp.summary as Partial<RunSummary> & { rows?: (RunSummary & { strategy: string })[] };
  if (s?.rows) return s.rows.find((r) => r.strategy === "compiled") ?? s.rows[0] ?? null;
  return s && "numeric" in s ? (s as RunSummary) : null;
}

export default function ExperimentsPage() {
  const { expId } = useParams();
  const navigate = useNavigate();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const refresh = useCallback(() => api.experiments().then(setExperiments), []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="page" style={{ maxWidth: 1440 }}>
      <div className="page-head">
        <div>
          <h1>Experiments</h1>
          <p>Run the golden set and score every answer with a numeric check, an LLM judge and Laya.</p>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 320px) minmax(0, 1fr)", gap: 20, alignItems: "start" }}>
        <div className="stack" style={{ position: "sticky", top: 0 }}>
          <NewExperiment onCreated={(e) => { refresh(); navigate(`/experiments/${e.id}`); }} />
          <div className="card">
            <div className="card-head"><h3>Runs</h3><span className="muted small">{experiments.length}</span></div>
            <div style={{ maxHeight: 420, overflow: "auto", padding: 6 }}>
              {experiments.length === 0 && <div className="empty small">No runs yet</div>}
              {experiments.map((e) => {
                const summary = summaryOf(e);
                return (
                  <button key={e.id} className="btn btn-ghost" onClick={() => navigate(`/experiments/${e.id}`)}
                    style={{ width: "100%", display: "grid", textAlign: "left", justifyContent: "stretch", background: e.id === expId ? "var(--accent-soft)" : undefined }}>
                    <span className="row" style={{ gap: 6 }}>
                      {e.status === "running" ? <span className="spinner" /> : e.status === "failed" ? <span style={{ color: "var(--critical)" }}>✕</span> : <span style={{ color: "var(--good-ink)" }}>✓</span>}
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 560 }}>{e.name}</span>
                      <span className="grow" />
                      {summary && <span className="num" style={{ fontWeight: 650 }}>{pct(summary.numeric)}</span>}
                    </span>
                    <span className="muted small">
                      {ago(e.created_at)}
                      {e.config.rulebook?.model ? ` · rules by ${e.config.rulebook.model}` : ""}
                      {summary?.cost_usd ? ` · ${usd(summary.cost_usd.total)}` : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
        <div style={{ minWidth: 0 }}>{expId ? <ExperimentView key={expId} expId={expId} onChange={refresh} /> : <div className="card empty">Start a run or pick one from the list.</div>}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ form */

function NewExperiment({ onCreated }: { onCreated: (e: Experiment) => void }) {
  const meta = useMeta();
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [cases, setCases] = useState<GoldenCase[]>([]);
  const [docId, setDocId] = useState("");
  const [scorers, setScorers] = useState<string[]>(["numeric", "judge", "laya"]);
  const [difficulty, setDifficulty] = useState<Difficulty[]>([]);
  const [limit, setLimit] = useState<string>("");
  const [concurrency, setConcurrency] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.documents().then((d) => {
      const ready = d.filter((x) => x.status === "ready");
      setDocs(ready);
      if (ready[0]) setDocId((cur) => cur || ready[0].id);
    });
    api.cases().then(setCases);
  }, []);

  const filtered = useMemo(() => (difficulty.length ? cases.filter((x) => difficulty.includes(x.difficulty)) : cases), [cases, difficulty]);
  const selected = limit ? filtered.slice(0, Number(limit)) : filtered;
  const counts = DIFFS.map((d) => filtered.filter((x) => x.difficulty === d).length);
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.createExperiment({
        document_id: docId, strategies: ["compiled"], scorers, difficulty, concurrency, limit: limit ? Number(limit) : null,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-head"><h3>New run</h3></div>
      <div className="card-pad stack">
        <label className="field">Document
          <select className="select" value={docId} onChange={(e) => setDocId(e.target.value)}>
            {docs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <div className="field">Scorers
          <div className="row-wrap">
            {(meta?.scorers ?? []).map((s) => (
              <button key={s.id} disabled={!s.available} title={s.available ? "" : "laya is not installed on the server"}
                className={`chip toggle-chip${scorers.includes(s.id) && s.available ? " on" : ""}`} onClick={() => setScorers(toggle(scorers, s.id))}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
        <div className="field">Difficulty <span className="muted small" style={{ fontWeight: 400 }}>(none selected = all)</span>
          <div className="row-wrap">
            {DIFFS.map((d) => (
              <button key={d} className={`chip toggle-chip${difficulty.includes(d) ? " on" : ""}`} onClick={() => setDifficulty(toggle(difficulty, d))}>{d}</button>
            ))}
          </div>
        </div>
        <div className="row">
          <label className="field grow">Max cases
            <input className="input" type="number" min={1} placeholder="all" value={limit} onChange={(e) => setLimit(e.target.value)} />
          </label>
          <label className="field grow">Parallel
            <input className="input" type="number" min={1} max={16} value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} />
          </label>
        </div>
        <div>
          <div className="row small" style={{ justifyContent: "space-between", marginBottom: 4 }}>
            <span className="muted">Dataset mix</span>
            <span className="num"><b>{selected.length}</b> cases</span>
          </div>
          <div style={{ display: "flex", height: 8, borderRadius: 999, overflow: "hidden", gap: 2 }}>
            {DIFFS.map((d, i) => counts[i] > 0 && (
              <div key={d} title={`${counts[i]} ${d}`} style={{ flex: counts[i], background: `var(--${d === "easy" ? "good" : d === "medium" ? "warning" : "critical"})` }} />
            ))}
          </div>
          <div className="row small muted" style={{ gap: 12, marginTop: 4 }}>
            {DIFFS.map((d, i) => <span key={d}>{d} {counts[i]}</span>)}
          </div>
        </div>
        {error && <div className="error-box">{error}</div>}
        <button className="btn btn-primary" disabled={busy || !docId || !scorers.length || !selected.length} onClick={start}>
          {busy && <span className="spinner" />} Run experiment
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ run view */

interface Cell {
  state: "pending" | "running" | "done";
  numeric?: number | null;
  judge?: number | null;
  laya?: number | null;
  error?: string | null;
  latency?: number | null;
  cost?: number | null;
}

function ExperimentView({ expId, onChange }: { expId: string; onChange: () => void }) {
  const [exp, setExp] = useState<Experiment | null>(null);
  const [cases, setCases] = useState<GoldenCase[]>([]);
  const [caseIds, setCaseIds] = useState<{ id: string; difficulty: Difficulty }[]>([]);
  const [cells, setCells] = useState<Record<string, Cell>>({});
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [total, setTotal] = useState(0);
  const [live, setLive] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    const e = await api.experiment(expId);
    setExp(e);
    const strategy = e.config.strategies.includes("compiled") ? "compiled" : e.config.strategies[0];
    const results = (e.results ?? []).filter((r) => r.strategy === strategy);
    setCells((prev) => {
      const next = { ...prev };
      for (const r of results) {
        next[r.case_id] = { state: "done", numeric: r.scores.numeric?.score, judge: r.scores.judge?.score, laya: r.scores.laya?.score,
          error: r.error, latency: r.latency_s, cost: r.tokens?.cost_usd };
      }
      return next;
    });
    const s = summaryOf(e);
    if (s) setSummary(s);
    return { e, results };
  }, [expId]);

  useEffect(() => {
    api.cases().then(setCases);
    const controller = new AbortController();
    load().then(({ e, results }) => {
      if (!e.running) {
        setCaseIds(results.map((r) => ({ id: r.case_id, difficulty: r.difficulty }))
          .sort((a, b) => DIFFS.indexOf(a.difficulty) - DIFFS.indexOf(b.difficulty) || a.id.localeCompare(b.id)));
        setTotal(results.length);
        return;
      }
      setLive(true);
      streamEvents(`/api/experiments/${expId}/stream`, (ev) => {
        if (ev.type === "start") {
          setCaseIds(ev.cases);
          setTotal(ev.total);
        } else if (ev.type === "running") {
          setCells((c) => ({ ...c, [ev.case_id]: { ...c[ev.case_id], state: "running" } }));
        } else if (ev.type === "case") {
          setCells((c) => ({ ...c, [ev.case_id]: { state: "done", numeric: ev.scores.numeric?.score, judge: ev.scores.judge?.score,
            laya: ev.scores.laya?.score, error: ev.error, latency: ev.latency_s, cost: ev.tokens?.cost_usd } }));
          setSummary(ev.summary);
        } else if (ev.type === "done") {
          setSummary(ev.summary);
        }
      }, { signal: controller.signal })
        .catch(() => undefined)
        .finally(() => {
          setLive(false);
          load();
          onChange();
        });
    });
    return () => controller.abort();
  }, [expId, load, onChange]);

  if (!exp) return <div className="card empty"><span className="spinner" /></div>;

  const done = Object.values(cells).filter((c) => c.state === "done").length;
  const running = Object.values(cells).filter((c) => c.state === "running").length;
  const elapsed = (Date.now() - new Date(exp.created_at).getTime()) / 1000;
  const eta = live && done > 0 ? (elapsed / done) * (total - done) : null;
  const charges = Object.keys(summary?.by_charge ?? {}).sort(
    (a, b) => Object.keys(CHARGE_LABELS).indexOf(a) - Object.keys(CHARGE_LABELS).indexOf(b),
  );
  const legacy = exp.config.strategies.length > 1;

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card card-pad">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div className="grow">
            <h2>{exp.name}</h2>
            <div className="row-wrap small" style={{ marginTop: 6 }}>
              {exp.config.rulebook?.model && (
                <span className="chip" title="the rulebook these answers were priced with">
                  rules compiled by <b className="mono">{exp.config.rulebook.model}</b>
                  {exp.config.rulebook.tokens?.cost_usd !== undefined && <> for {usd(exp.config.rulebook.tokens.cost_usd)}</>}
                </span>
              )}
              {exp.config.scorers.map((s) => <span key={s} className="chip">{s}</span>)}
              {exp.config.difficulty?.length ? exp.config.difficulty.map((d) => <DifficultyBadge key={d} value={d} />) : <span className="chip">all difficulties</span>}
              {legacy && <span className="chip" title="recorded before the query-time agents were removed">earlier multi-strategy run, showing compiled rules</span>}
              <span className="muted">started {ago(exp.created_at)}</span>
            </div>
          </div>
          {!live && (
            <button className="btn btn-sm btn-ghost btn-danger" onClick={async () => { if (confirm("Delete this run?")) { await api.deleteExperiment(expId); onChange(); history.back(); } }}>
              Delete
            </button>
          )}
        </div>
        <div style={{ marginTop: 14 }}>
          <div className="row small" style={{ justifyContent: "space-between", marginBottom: 6 }}>
            <span className="row" style={{ gap: 8 }}>
              {live ? <><span className="spinner" /> Running · {running} in flight</> : exp.status === "failed" ? <span style={{ color: "var(--critical)" }}>✕ Failed</span> : <span style={{ color: "var(--good-ink)" }}>✓ Finished</span>}
            </span>
            <span className="num muted">{done}/{total || "?"} {eta !== null && `· ~${seconds(eta)} left`}</span>
          </div>
          <Progress value={total ? done / total : 0} />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
        <Kpi label="Charges within ±1%" value={pct(summary?.numeric, 1)} hint="numeric accuracy" />
        <Kpi label="Exact cases" value={summary ? `${summary.exact}/${summary.completed}` : "—"} hint="every charge right" />
        <Kpi label="Judge pass rate" value={pct(summary?.judge_pass_rate)} hint={`mean score ${pct(summary?.judge)}`} />
        <Kpi label="Laya" value={pct(summary?.laya)} hint="coverage and presentation" />
        <Kpi label="Latency" value={seconds(summary?.latency_s)} hint={`${compact(summary?.tokens)} tokens per answer`} />
        <Kpi label="Run cost" value={usd(summary?.cost_usd?.total)}
          hint={summary?.cost_usd ? `answers ${usd(summary.cost_usd.answers)} · judge ${usd(summary.cost_usd.judge)}` : undefined} />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-head"><h3>Accuracy by difficulty</h3><span className="muted small">charges within ±1%</span></div>
          <div className="card-pad">
            {summary ? (
              <GroupedBars categories={DIFFS} strategies={["compiled"]} legend={false}
                value={(d) => summary.by_difficulty[d as Difficulty] ?? null} />
            ) : <div className="empty small">No data yet</div>}
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Hit rate per charge</h3><span className="muted small">cases where the charge was right</span></div>
          <div className="card-pad">
            {summary && charges.length ? (
              <HBars items={charges.map((c) => ({ label: CHARGE_LABELS[c] ?? c, value: summary.by_charge[c] }))} />
            ) : <div className="empty small">No data yet</div>}
          </div>
        </div>
      </div>

      <CaseTable caseIds={caseIds} cells={cells} cases={cases} onOpen={setOpen} />

      {open && <CaseDrawer exp={exp} reload={load} caseId={open} golden={cases.find((c) => c.id === open)} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card card-pad" style={{ padding: 14 }}>
      <div className="muted small">{label}</div>
      <div style={{ fontSize: 22, fontWeight: 680, letterSpacing: "-0.01em", marginTop: 4 }}>{value}</div>
      {hint && <div className="muted" style={{ fontSize: 11.5 }}>{hint}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ case table */

function ScoreCell({ value, state, error }: { value?: number | null; state?: Cell["state"]; error?: string | null }) {
  if (state === "running") return <span className="spinner" />;
  if (state !== "done") return <span className="muted">·</span>;
  if (error) return <span style={{ color: "var(--critical)", fontWeight: 600 }}>✕ error</span>;
  const color = value === null || value === undefined ? null : rampColor(value);
  return (
    <span style={{ display: "inline-block", minWidth: 56, textAlign: "center", padding: "3px 8px", borderRadius: 5, fontWeight: 600,
      background: color?.fill ?? "var(--surface-2)", color: color?.ink ?? "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
      {pct(value)}
    </span>
  );
}

function CaseTable({ caseIds, cells, cases, onOpen }: {
  caseIds: { id: string; difficulty: Difficulty }[];
  cells: Record<string, Cell>;
  cases: GoldenCase[];
  onOpen: (caseId: string) => void;
}) {
  const tip = useTooltip();
  const byId = new Map(cases.map((c) => [c.id, c]));
  return (
    <div className="card">
      <div className="card-head">
        <h3>Cases</h3>
        <span className="muted small">click a row for the answer, expected vs predicted, and the trace</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="table">
          <thead>
            <tr><th>Case</th><th>Question</th><th>Numeric</th><th className="r">Judge</th><th className="r">Laya</th><th className="r">Latency</th><th className="r">Cost</th></tr>
          </thead>
          <tbody>
            {caseIds.map(({ id, difficulty }) => {
              const cell = cells[id];
              return (
                <tr key={id} className={cell?.state === "done" ? "clickable" : ""} onClick={() => cell?.state === "done" && onOpen(id)}>
                  <td style={{ whiteSpace: "nowrap" }}><span className="mono muted" style={{ marginRight: 8 }}>{id}</span><DifficultyBadge value={difficulty} /></td>
                  <td style={{ maxWidth: 520 }}>
                    <div className="ink-2" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      onPointerMove={(e) => tip.show(e, <div style={{ whiteSpace: "pre-wrap" }}>{byId.get(id)?.question}</div>)} onPointerLeave={tip.hide}>
                      {byId.get(id)?.question.replace(/\s+/g, " ")}
                    </div>
                  </td>
                  <td><ScoreCell value={cell?.numeric} state={cell?.state} error={cell?.error} /></td>
                  <td className="r num">{cell?.state === "done" ? pct(cell.judge) : ""}</td>
                  <td className="r num">{cell?.state === "done" ? pct(cell.laya) : ""}</td>
                  <td className="r num">{cell?.state === "done" ? seconds(cell.latency) : ""}</td>
                  <td className="r num">{cell?.state === "done" ? usd(cell.cost) : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {tip.node}
    </div>
  );
}

/* ------------------------------------------------------------------ case drawer */

function CaseDrawer({ exp, reload, caseId, golden, onClose }: {
  exp: Experiment;
  reload: () => Promise<{ e: Experiment; results: CaseResult[] }>;
  caseId: string;
  golden?: GoldenCase;
  onClose: () => void;
}) {
  const pick = (results?: CaseResult[]) =>
    results?.find((r) => r.case_id === caseId && r.strategy === "compiled") ?? results?.find((r) => r.case_id === caseId);
  const [result, setResult] = useState<CaseResult | undefined>(pick(exp.results));

  useEffect(() => {
    if (!result?.answer) reload().then(({ e }) => setResult(pick(e.results)));
  }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps

  const numeric = result?.scores.numeric;
  const judge = result?.scores.judge;
  const laya = result?.scores.laya;

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" style={{ width: "min(860px, 100vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="card-head">
          <div className="row" style={{ gap: 10 }}>
            <span className="mono">{caseId}</span>
            {golden && <DifficultyBadge value={golden.difficulty} />}
          </div>
          <button className="btn btn-sm" onClick={onClose}>Close</button>
        </div>
        <div className="drawer-body card-pad stack">
          {!result ? <span className="spinner" /> : (
            <>
              <div>
                <div className="muted small" style={{ marginBottom: 4 }}>Question</div>
                <div style={{ whiteSpace: "pre-wrap" }} className="ink-2">{result.question ?? golden?.question}</div>
                {golden?.scenario && <div className="muted small" style={{ marginTop: 4 }}>Scenario: {golden.scenario}</div>}
              </div>

              <div className="row-wrap">
                <span className="chip">numeric <b>{pct(numeric?.score)}</b></span>
                <span className="chip">judge <b>{pct(judge?.score)}</b> {judge?.verdict && `(${judge.verdict})`}</span>
                <span className="chip">laya <b>{pct(laya?.score)}</b></span>
                <span className="chip">latency <b>{seconds(result.latency_s)}</b></span>
                {result.tokens && <span className="chip" title={tokenTitle(result.tokens)}>tokens <b>{compact(result.tokens.input + result.tokens.output)}</b></span>}
                {result.tokens?.cost_usd !== undefined && <span className="chip">cost <b>{usd(result.tokens.cost_usd)}</b></span>}
                {result.trace_url && <a className="chip" href={result.trace_url} target="_blank" rel="noreferrer">Langfuse trace ↗</a>}
              </div>
              {result.error && <div className="error-box">{result.error}</div>}

              {numeric && (
                <div className="card" style={{ overflow: "hidden" }}>
                  <table className="table">
                    <thead><tr><th>Charge</th><th className="r">Expected</th><th className="r">Predicted</th><th className="r">Error</th><th>Result</th></tr></thead>
                    <tbody>
                      {Object.entries(numeric.per_charge).map(([k, c]) => (
                        <tr key={k}>
                          <td>{CHARGE_LABELS[k] ?? k}</td>
                          <td className="r num">{money(c.expected)}</td>
                          <td className="r num">{money(c.predicted)}</td>
                          <td className="r num">{c.error === null ? "—" : pct(c.error, 2)}</td>
                          <td>{c.hit ? <span className="status status-good"><span className="dot" />✓ within 1%</span> : <span className="status status-critical"><span className="dot" />✕ {c.predicted === null ? "missing" : "off"}</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {numeric.extra && numeric.extra.length > 0 && <div className="small muted" style={{ padding: "8px 10px" }}>Also reported: {numeric.extra.join(", ")}</div>}
                </div>
              )}

              {judge?.rationale && (
                <div className="card card-pad small">
                  <div className="row-wrap" style={{ marginBottom: 6 }}>
                    <b>LLM judge</b>
                    <span className="chip">correctness {judge.correctness}/5</span>
                    <span className="chip">completeness {judge.completeness}/5</span>
                    <span className="chip">reasoning {judge.reasoning}/5</span>
                  </div>
                  <div className="ink-2">{judge.rationale}</div>
                </div>
              )}

              {laya?.probabilities && (
                <div className="card card-pad small">
                  <b>Laya decisions</b> <span className="muted">P(true) for each typed check</span>
                  <div className="stack" style={{ gap: 4, marginTop: 8 }}>
                    {Object.entries(laya.probabilities).map(([k, p]) => (
                      <div key={k} className="row" style={{ gap: 10 }}>
                        <span className="mono" style={{ width: 210 }}>{k}</span>
                        <div className="grow"><ScoreBar value={p} color="var(--accent)" /></div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.answer && (
                <div className="card card-pad">
                  <div className="muted small" style={{ marginBottom: 8 }}>Answer</div>
                  <Markdown>{result.answer}</Markdown>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
