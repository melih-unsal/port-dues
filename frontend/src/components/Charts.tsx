import { ReactNode, useCallback, useState } from "react";
import { pct, STRATEGY_LABELS } from "../format";

/* ------------------------------------------------------------------ tooltip */

interface TipState {
  x: number;
  y: number;
  content: ReactNode;
}

export function useTooltip() {
  const [tip, setTip] = useState<TipState | null>(null);
  const show = useCallback((e: React.PointerEvent | React.FocusEvent, content: ReactNode) => {
    const point = "clientX" in e ? { x: e.clientX, y: e.clientY } : (() => {
      const r = (e.target as Element).getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top };
    })();
    setTip({ ...point, content });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  const node = tip ? (
    <div className="tooltip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 300), top: tip.y + 14 }}>
      {tip.content}
    </div>
  ) : null;
  return { node, show, hide };
}

export const seriesColor = (strategy: string) => `var(--series-${strategy})`;

/** Sequential blue ramp for 0..1 values (one hue, light -> dark). */
const RAMP = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600", "--seq-700"];
export function rampColor(value: number) {
  const i = Math.min(RAMP.length - 1, Math.max(0, Math.round(value * (RAMP.length - 1))));
  // ink follows the cell's own lightness, not the page theme
  return { fill: `var(${RAMP[i]})`, ink: i >= 3 ? "#ffffff" : "#0b0b0b" };
}

export function Legend({ strategies }: { strategies: string[] }) {
  return (
    <div className="legend">
      {strategies.map((s) => (
        <span key={s}><i style={{ background: seriesColor(s) }} />{STRATEGY_LABELS[s] ?? s}</span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ grouped bars */

export function GroupedBars({
  categories,
  strategies,
  value,
  height = 220,
  legend = true,
}: {
  categories: string[];
  strategies: string[];
  value: (category: string, strategy: string) => number | null;
  height?: number;
  legend?: boolean;
}) {
  const tip = useTooltip();
  const width = 520;
  const pad = { top: 22, right: 8, bottom: 28, left: 38 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const groupW = innerW / categories.length;
  const barGap = 2;
  const barW = Math.min(36, (groupW * 0.72 - barGap * (strategies.length - 1)) / strategies.length);
  const y = (v: number) => pad.top + innerH * (1 - v);

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Numeric accuracy by difficulty and strategy">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line className={t === 0 ? "baseline" : "gridline"} x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} />
            <text x={pad.left - 6} y={y(t) + 4} textAnchor="end">{t * 100}%</text>
          </g>
        ))}
        {categories.map((c, ci) => {
          const groupX = pad.left + ci * groupW + (groupW - (barW * strategies.length + barGap * (strategies.length - 1))) / 2;
          return (
            <g key={c}>
              {strategies.map((s, si) => {
                const v = value(c, s);
                const x = groupX + si * (barW + barGap);
                if (v === null) return null;
                const h = Math.max(innerH * v, 1);
                const r = Math.min(4, h / 2, barW / 2);
                const top = y(v);
                const base = y(0);
                // rounded data-end, square at the baseline
                const d = `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${base} Z`;
                return (
                  <g key={s}>
                    <path
                      d={d}
                      fill={seriesColor(s)}
                      tabIndex={0}
                      onPointerMove={(e) => tip.show(e, <TipBody title={c} rows={[[s, v]]} />)}
                      onPointerLeave={tip.hide}
                      onFocus={(e) => tip.show(e, <TipBody title={c} rows={[[s, v]]} />)}
                      onBlur={tip.hide}
                    />
                    <text x={x + barW / 2} y={top - 5} textAnchor="middle" className="label-strong" style={{ fontSize: 10 }}>
                      {Math.round(v * 100)}
                    </text>
                  </g>
                );
              })}
              <text x={pad.left + ci * groupW + groupW / 2} y={height - 8} textAnchor="middle" style={{ fontSize: 12 }}>{c}</text>
            </g>
          );
        })}
      </svg>
      {legend && <Legend strategies={strategies} />}
      {tip.node}
    </div>
  );
}

function TipBody({ title, rows }: { title: string; rows: [string, number | null][] }) {
  return (
    <>
      <div className="t-head">{title}</div>
      {rows.map(([s, v]) => (
        <div key={s} className="t-row">
          <span className="key" style={{ background: seriesColor(s) }} />
          <b>{pct(v, 1)}</b>
          <span className="muted">{STRATEGY_LABELS[s] ?? s}</span>
        </div>
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ horizontal bars */

/** One bar per category for a single series (0..1), labelled directly, with hover detail. */
export function HBars({ items, color = "var(--accent)" }: { items: { label: string; value: number | null; detail?: string }[]; color?: string }) {
  const tip = useTooltip();
  return (
    <div className="stack" style={{ gap: 8 }}>
      {items.map((it) => (
        <div key={it.label} className="row small" style={{ gap: 10 }}
          onPointerMove={(e) => tip.show(e, <><div className="t-head">{it.label}</div><b>{pct(it.value, 1)}</b>{it.detail && <div className="muted">{it.detail}</div>}</>)}
          onPointerLeave={tip.hide}>
          <span style={{ width: 120, flex: "none" }} className="ink-2">{it.label}</span>
          <div style={{ flex: 1, height: 14, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ width: `${(it.value ?? 0) * 100}%`, height: "100%", background: color, borderRadius: 4, transition: "width .5s ease" }} />
          </div>
          <span className="num" style={{ width: 44, textAlign: "right", fontWeight: 600 }}>{pct(it.value)}</span>
        </div>
      ))}
      {tip.node}
    </div>
  );
}

/* ------------------------------------------------------------------ inline bar */

export function ScoreBar({ value, color }: { value: number | null; color: string }) {
  return (
    <div className="row" style={{ gap: 8, minWidth: 110 }}>
      <div style={{ flex: 1, height: 6, background: "var(--surface-3)", borderRadius: 999, overflow: "hidden" }}>
        <div style={{ width: `${(value ?? 0) * 100}%`, height: "100%", background: color, borderRadius: 999, transition: "width .5s ease" }} />
      </div>
      <span className="num" style={{ width: 40, textAlign: "right", fontWeight: 600 }}>{pct(value)}</span>
    </div>
  );
}
