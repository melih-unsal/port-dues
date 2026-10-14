export type DocStatus = "queued" | "running" | "ready" | "failed";

export interface TokenUsage {
  input: number;
  output: number;
  cached?: number;
  reasoning?: number;
  cost_usd?: number;
}

export interface CompiledWith {
  model?: string;
  rules?: number;
  attempted?: number;
  repairs?: number;
  failed?: number;
  seconds?: number;
  tokens?: TokenUsage;
}

export interface DocumentInfo {
  id: string;
  name: string;
  filename: string;
  status: DocStatus;
  stage: string | null;
  progress: number;
  error: string | null;
  stats: Partial<{
    pages: number;
    vision_pages: number;
    sections: number;
    facts: number;
    chunks: number;
    tree_height: number;
    rules: number;
    uncovered: number;
    seconds: number;
    tokens: Record<string, TokenUsage>;
  }>;
  created_at: string;
}

export interface TreeNode {
  id: string;
  parent_id: string | null;
  section: string;
  title: string;
  level: number;
  depth: number;
  pages: number[];
  facts: number;
  digest: string;
}

export interface SectionDetail {
  section: string;
  title: string;
  digest: string;
  pages: number[];
  facts: { text: string; category: string; charge: string }[];
  source: string;
}

export interface Condition {
  field: string;
  op: string;
  value: string | number | boolean | string[] | null;
}

export interface NamedValue {
  name: string;
  value: number;
  source?: string;
}

export interface ChargeRule {
  id: string;
  name: string;
  section: string;
  payer: "vessel" | "cargo";
  description: string;
  applies_when: Condition[];
  constants: NamedValue[];
  tables: { name: string; description: string; bands: { lower: number; upper: number | null; values: NamedValue[] }[] }[];
  variants: { label: string; when: Condition[]; formula: string }[];
  minimum: number | null;
  maximum: number | null;
  adjustments: { name: string; kind: "surcharge" | "reduction"; percent: number; when: Condition[] }[];
  exemptions: { description: string; when: Condition[] }[];
  notes: string;
}

export interface Rulebook {
  title: string;
  currency: string;
  tax_note: string;
  variables: { name: string; type: string; description: string }[];
  charges: ChargeRule[];
  uncovered: { name: string; section: string; reason: string }[];
  compiled_with?: CompiledWith;
}

export type LineStatus = "charged" | "exempt" | "not_applicable" | "needs_input" | "error";

export interface ChargeLine {
  charge_id: string;
  name: string;
  section: string;
  payer: "vessel" | "cargo";
  status: LineStatus;
  amount: number | null;
  method: "rule" | "research";
  variant: string | null;
  formula: string | null;
  inputs: Record<string, unknown>;
  steps: string[];
  assumptions: string[];
  reason: string;
}

/** Everything the UI reconstructs from a turn's event stream. */
export interface Trace {
  status?: string;
  profile?: Record<string, unknown> | null;
  lines?: ChargeLine[];
  currency?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta: {
    strategy?: string;
    lines?: ChargeLine[];
    profile?: Record<string, unknown> | null;
    latency_s?: number;
    tokens?: TokenUsage;
    trace?: AnyEvent[];
    trace_url?: string | null;
    error?: boolean;
  };
  created_at?: string;
}

export interface Conversation {
  id: string;
  doc_id: string;
  title: string;
  created_at: string;
}

export type AnyEvent = { type: string; [key: string]: any };

export interface Meta {
  models: { compiler: string; fast: string; judge: string };
  embedding_model: string;
  strategies: { id: string; label: string }[];
  scorers: { id: string; label: string; available: boolean }[];
  tracing: boolean;
  langfuse_url: string | null;
}

export type Difficulty = "easy" | "medium" | "hard";

export interface GoldenCase {
  id: string;
  difficulty: Difficulty;
  question: string;
  expected: Record<string, number>;
  currency: string;
  port: string;
  scenario: string;
}

export interface ScoreSet {
  numeric?: {
    score: number;
    exact: boolean;
    mean_abs_error?: number | null;
    per_charge: Record<string, { expected: number; predicted: number | null; error: number | null; hit: boolean }>;
    extra?: string[];
  };
  judge?: {
    score: number | null;
    correctness?: number;
    completeness?: number;
    reasoning?: number;
    verdict?: "pass" | "fail";
    rationale?: string;
    error?: string;
  };
  laya?: { score: number | null; probabilities?: Record<string, number>; error?: string };
}

export interface CaseResult {
  case_id: string;
  strategy: string;
  difficulty: Difficulty;
  question?: string;
  expected?: Record<string, number>;
  answer?: string;
  lines?: ChargeLine[];
  scores: ScoreSet;
  error: string | null;
  latency_s: number | null;
  tokens: TokenUsage | null;
  trace_url?: string | null;
}

export interface RunSummary {
  completed: number;
  errors: number;
  numeric: number | null;
  judge: number | null;
  laya: number | null;
  exact: number;
  exact_rate: number | null;
  judge_pass_rate: number | null;
  latency_s: number | null;
  tokens: number | null;
  cached_share: number | null;
  by_difficulty: Record<Difficulty, number | null>;
  by_charge: Record<string, number | null>;
  cost_usd?: { answers: number; judge: number; total: number };
}

export interface Experiment {
  id: string;
  doc_id: string;
  name: string;
  config: {
    strategies: string[];
    scorers: string[];
    difficulty: Difficulty[];
    limit: number | null;
    concurrency: number;
    rulebook?: CompiledWith;
  };
  status: "running" | "done" | "failed";
  // runs recorded before the single-technique change stored one row per strategy
  summary: RunSummary | { rows: (RunSummary & { strategy: string })[] } | { error?: string };
  created_at: string;
  finished_at: string | null;
  results?: CaseResult[];
  running?: boolean;
}
