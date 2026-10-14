export const money = (value: number | null | undefined, currency = "") =>
  value === null || value === undefined
    ? "—"
    : `${currency ? currency + " " : ""}${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const pct = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

export const seconds = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : value < 60 ? `${value.toFixed(1)}s` : `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;

export const compact = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : Intl.NumberFormat("en", { notation: "compact" }).format(value);

export const ago = (iso: string) => {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
};

export const CHARGE_LABELS: Record<string, string> = {
  light_dues: "Light dues",
  port_dues: "Port dues",
  vts_dues: "VTS",
  pilotage_dues: "Pilotage",
  towage_dues: "Towage",
  berthing_services: "Berthing",
  running_lines: "Running lines",
  cargo_dues: "Cargo dues",
};

// planner/agentic only appear in experiment runs recorded before the query-time agents were removed
export const STRATEGY_LABELS: Record<string, string> = {
  compiled: "Compiled rules",
  planner: "Agent + rules (removed)",
  agentic: "Agent, retrieval only (removed)",
};

export const usd = (value: number | null | undefined) =>
  value === null || value === undefined
    ? "—"
    : value === 0
      ? "$0"
      : value < 0.01
        ? `$${value.toFixed(4)}`
        : `$${value.toFixed(value < 1 ? 3 : 2)}`;

export const tokenTitle = (t?: { input: number; output: number; cached?: number; reasoning?: number } | null) =>
  t ? `input ${t.input.toLocaleString()} (cached ${(t.cached ?? 0).toLocaleString()}) · output ${t.output.toLocaleString()} (reasoning ${(t.reasoning ?? 0).toLocaleString()})` : "";
