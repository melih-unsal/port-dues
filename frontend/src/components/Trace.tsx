import { Fragment, useState } from "react";
import { money } from "../format";
import type { AnyEvent, ChargeLine, Trace } from "../types";
import { LineStatusPill } from "./ui";

export const emptyTrace = (): Trace => ({});

/** Fold one stream event into the trace. Used live and when replaying saved turns. */
export function applyEvent(trace: Trace, ev: AnyEvent): Trace {
  switch (ev.type) {
    case "status":
      return { ...trace, status: ev.message };
    case "profile":
      return { ...trace, profile: ev.profile };
    case "lines":
      return { ...trace, lines: ev.lines, currency: ev.currency };
    default:
      return trace;
  }
}

export function TraceView({ trace, live }: { trace: Trace; live: boolean }) {
  const profile = trace.profile
    ? Object.entries(trace.profile).filter(([k, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0) && k !== "vessel_name")
    : [];
  return (
    <div className="stack" style={{ gap: 12 }}>
      {live && trace.status && <div className="row small ink-2"><span className="spinner" /> {trace.status}…</div>}
      {profile.length > 0 && (
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>Vessel call understood as</div>
          <div className="row-wrap" style={{ gap: 6 }}>
            {profile.map(([k, v]) => (
              <span key={k} className="chip"><span className="mono">{k}</span> <b>{Array.isArray(v) ? v.join(", ") : String(v)}</b></span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function Breakdown({ lines, currency }: { lines: ChargeLine[]; currency?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const shown = lines.filter((l) => l.status !== "not_applicable");
  if (!shown.length) return null;
  const total = (payer: string) => shown.filter((l) => l.payer === payer && l.status === "charged").reduce((s, l) => s + (l.amount ?? 0), 0);
  const cargo = total("cargo");

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <table className="table">
        <thead>
          <tr><th>Charge</th><th>Clause</th><th>Source</th><th>Status</th><th className="r">Amount{currency ? ` (${currency})` : ""}</th></tr>
        </thead>
        <tbody>
          {shown.map((l, i) => {
            const key = `${l.charge_id}-${i}`;
            return (
              <Fragment key={key}>
                <tr className="clickable" onClick={() => setOpen(open === key ? null : key)}>
                  <td><span className="muted" style={{ marginRight: 6 }}>{open === key ? "▾" : "▸"}</span>{l.name}{l.payer === "cargo" && <span className="chip" style={{ marginLeft: 6 }}>cargo</span>}</td>
                  <td className="mono muted">{l.section ? `§${l.section}` : ""}</td>
                  <td><span className={`badge badge-${l.method}`}>{l.method === "rule" ? "compiled rule" : "research"}</span></td>
                  <td><LineStatusPill status={l.status} /></td>
                  <td className="r num" style={{ fontWeight: 600 }}>{money(l.amount)}</td>
                </tr>
                {open === key && (
                  <tr>
                    <td colSpan={5} style={{ background: "var(--surface-2)" }}>
                      <div className="stack small" style={{ gap: 6 }}>
                        {l.variant && <div><span className="muted">Variant</span> {l.variant}</div>}
                        {l.formula && <div><span className="muted">Formula</span> <code>{l.formula}</code></div>}
                        {Object.keys(l.inputs ?? {}).length > 0 && (
                          <div className="row-wrap" style={{ gap: 6 }}>
                            {Object.entries(l.inputs).map(([k, v]) => <span key={k} className="chip"><span className="mono">{k}</span> <b>{String(v)}</b></span>)}
                          </div>
                        )}
                        {l.steps.map((s, j) => <div key={j} className="mono">{s}</div>)}
                        {l.reason && <div className="ink-2">{l.reason}</div>}
                        {l.assumptions.length > 0 && <div className="muted">Assumed: {l.assumptions.join("; ")}</div>}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          <tr>
            <td colSpan={4} style={{ fontWeight: 620 }}>Vessel total</td>
            <td className="r num" style={{ fontWeight: 680 }}>{money(total("vessel"))}</td>
          </tr>
          {cargo > 0 && (
            <tr>
              <td colSpan={4} className="ink-2">Cargo dues (billed to the cargo owner)</td>
              <td className="r num">{money(cargo)}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
