import type {
  AnyEvent,
  ChatMessage,
  Conversation,
  DocumentInfo,
  Experiment,
  GoldenCase,
  Meta,
  Rulebook,
  SectionDetail,
  TreeNode,
} from "./types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  meta: () => request<Meta>("/api/meta"),
  documents: () => request<DocumentInfo[]>("/api/documents"),
  document: (id: string) => request<DocumentInfo>(`/api/documents/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentInfo & { duplicate: boolean }>("/api/documents", { method: "POST", body: form });
  },
  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  reingest: (id: string, compileOnly: boolean) =>
    request(`/api/documents/${id}/reingest?compile_only=${compileOnly}`, { method: "POST" }),
  tree: (id: string) => request<TreeNode[]>(`/api/documents/${id}/tree`),
  section: (docId: string, nodeId: string) =>
    request<SectionDetail>(`/api/documents/${docId}/sections/${encodeURIComponent(nodeId)}`),
  rulebook: (id: string) => request<Rulebook>(`/api/documents/${id}/rulebook`),
  recompileRule: (docId: string, ruleId: string) =>
    request(`/api/documents/${docId}/rules/${encodeURIComponent(ruleId)}/recompile`, { method: "POST" }),
  fileUrl: (id: string) => `${BASE}/api/documents/${id}/file`,

  conversations: (docId: string) => request<Conversation[]>(`/api/documents/${docId}/conversations`),
  messages: (convId: string) => request<ChatMessage[]>(`/api/conversations/${convId}/messages`),
  deleteConversation: (convId: string) => request<void>(`/api/conversations/${convId}`, { method: "DELETE" }),

  cases: () => request<GoldenCase[]>("/api/eval/cases"),
  experiments: () => request<Experiment[]>("/api/experiments"),
  experiment: (id: string) => request<Experiment>(`/api/experiments/${id}`),
  createExperiment: (body: Record<string, unknown>) => request<Experiment>("/api/experiments", json(body)),
  deleteExperiment: (id: string) => request<void>(`/api/experiments/${id}`, { method: "DELETE" }),
};

/**
 * Minimal SSE reader over fetch, so we can POST a body (EventSource only does GET).
 * Resolves when the stream ends; abort via the signal.
 */
export async function streamEvents(
  path: string,
  onEvent: (event: AnyEvent) => void,
  options: { body?: unknown; signal?: AbortSignal } = {},
): Promise<void> {
  const init: RequestInit = options.body !== undefined ? json(options.body) : { method: "GET" };
  const res = await fetch(BASE + path, { ...init, signal: options.signal, headers: { ...init.headers, Accept: "text/event-stream" } });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split(/\r?\n/)
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}
