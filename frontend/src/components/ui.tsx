import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Difficulty, DocStatus, LineStatus } from "../types";

export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}

const DOC_STATUS: Record<DocStatus, [string, string]> = {
  queued: ["status-neutral", "Queued"],
  running: ["status-warning", "Ingesting"],
  ready: ["status-good", "Ready"],
  failed: ["status-critical", "Failed"],
};

export function DocStatusPill({ status }: { status: DocStatus }) {
  const [cls, label] = DOC_STATUS[status];
  return (
    <span className={`status ${cls}`}>
      <span className={`dot${status === "running" ? " pulse" : ""}`} />
      {label}
    </span>
  );
}

const LINE_STATUS: Record<LineStatus, [string, string, string]> = {
  charged: ["status-good", "✓", "Charged"],
  exempt: ["status-neutral", "○", "Exempt"],
  not_applicable: ["status-neutral", "–", "Not applicable"],
  needs_input: ["status-warning", "!", "Needs input"],
  error: ["status-critical", "✕", "Error"],
};

export function LineStatusPill({ status }: { status: LineStatus }) {
  const [cls, icon, label] = LINE_STATUS[status];
  return (
    <span className={`status ${cls}`} title={label}>
      <span className="dot" />
      <span aria-hidden>{icon}</span> {label}
    </span>
  );
}

export function DifficultyBadge({ value }: { value: Difficulty }) {
  return <span className={`badge badge-${value}`}>{value}</span>;
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function StrategyKey({ strategy }: { strategy: string }) {
  return <i style={{ background: `var(--series-${strategy})` }} />;
}
