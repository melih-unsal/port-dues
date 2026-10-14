import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, streamEvents } from "../api";
import { applyEvent, Breakdown, emptyTrace, TraceView } from "../components/Trace";
import { Markdown } from "../components/ui";
import { ago, compact, seconds, tokenTitle, usd } from "../format";
import type { ChatMessage, Conversation, DocumentInfo, TokenUsage, Trace } from "../types";

const EXAMPLES = [
  "Calculate all port charges for a 51,300 GT bulk carrier at Durban, 3.39 days alongside, 2 operations, exporting iron ore.",
  "What pilotage and towage would a 22,000 GT container ship pay entering and leaving Cape Town at night?",
  "Is a South African Navy vessel exempt from light dues and VTS charges?",
  "A tanker calls Saldanha only for bunkers and stays 20 hours. What are its port dues?",
];

interface Pending {
  question: string;
  content: string;
  trace: Trace;
  error?: string;
}

function replay(meta: ChatMessage["meta"]): Trace {
  return (meta.trace ?? []).reduce(applyEvent, emptyTrace());
}

export default function ChatPage() {
  const { docId, convId } = useParams();
  const navigate = useNavigate();
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const strategy = "compiled";
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const abort = useRef<AbortController | null>(null);
  const streamingConv = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const ready = useMemo(() => docs.filter((d) => d.status === "ready"), [docs]);
  const doc = docs.find((d) => d.id === docId);

  useEffect(() => {
    api.documents().then((all) => {
      setDocs(all);
      if (!docId) {
        const first = all.find((d) => d.status === "ready");
        if (first) navigate(`/chat/${first.id}`, { replace: true });
      }
    });
  }, [docId, navigate]);

  const loadConversations = useCallback(() => {
    if (docId) api.conversations(docId).then(setConversations).catch(() => setConversations([]));
  }, [docId]);
  useEffect(loadConversations, [loadConversations]);

  useEffect(() => {
    if (convId && convId === streamingConv.current) return; // we just created it while streaming
    if (!convId) {
      setMessages([]);
      return;
    }
    api.messages(convId).then(setMessages).catch(() => setMessages([]));
  }, [convId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending?.content, pending?.trace]);

  useEffect(() => () => abort.current?.abort(), []);

  async function send(text: string) {
    const question = text.trim();
    if (!question || !docId || pending) return;
    setInput("");
    const controller = new AbortController();
    abort.current = controller;
    let conversation = convId ?? null;
    let state: Pending = { question, content: "", trace: emptyTrace() };
    setPending(state);
    const update = (fn: (p: Pending) => Pending) => {
      state = fn(state);
      setPending(state);
    };
    try {
      await streamEvents(
        "/api/chat",
        (ev) => {
          if (ev.type === "conversation") {
            if (!conversation) {
              conversation = ev.id;
              streamingConv.current = ev.id;
              navigate(`/chat/${docId}/${ev.id}`, { replace: true });
            }
          } else if (ev.type === "token") {
            update((p) => ({ ...p, content: p.content + ev.text }));
          } else if (ev.type === "error") {
            update((p) => ({ ...p, error: ev.message }));
          } else if (ev.type !== "done") {
            update((p) => ({ ...p, trace: applyEvent(p.trace, ev) }));
          }
        },
        { body: { document_id: docId, message: question, strategy, conversation_id: conversation }, signal: controller.signal },
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") update((p) => ({ ...p, error: (e as Error).message }));
    }
    // the server persists both sides of the turn (errors included), so reload and drop the local copy
    const fresh = conversation ? await api.messages(conversation).catch(() => null) : null;
    if (fresh) setMessages(fresh);
    streamingConv.current = null;
    setPending(fresh ? null : state);
    loadConversations();
  }

  function newChat() {
    abort.current?.abort();
    setPending(null);
    navigate(`/chat/${docId}`);
  }

  if (docs.length && !ready.length) {
    return (
      <div className="page">
        <div className="card empty">No document is ready yet. Upload a tariff on the Documents page and wait for ingestion to finish.</div>
      </div>
    );
  }

  const empty = messages.length === 0 && !pending;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "250px 1fr", height: "100%" }}>
      <aside style={{ borderRight: "1px solid var(--border)", background: "var(--surface)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: 12, display: "grid", gap: 8 }}>
          <select className="select" value={docId ?? ""} onChange={(e) => navigate(`/chat/${e.target.value}`)} aria-label="Document">
            {ready.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          <button className="btn" onClick={newChat}>+ New conversation</button>
        </div>
        <div style={{ overflow: "auto", padding: "0 8px 12px" }}>
          {conversations.map((c) => (
            <div key={c.id} className="row" style={{ gap: 2 }}>
              <button
                className="btn btn-ghost btn-sm grow"
                style={{ justifyContent: "flex-start", background: c.id === convId ? "var(--accent-soft)" : undefined, color: c.id === convId ? "var(--accent)" : undefined, display: "grid", textAlign: "left" }}
                onClick={() => navigate(`/chat/${docId}/${c.id}`)}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</span>
                <span className="muted" style={{ fontSize: 11 }}>{ago(c.created_at)}</span>
              </button>
              <button className="btn btn-ghost btn-sm" aria-label="Delete conversation" title="Delete"
                onClick={async () => { await api.deleteConversation(c.id); if (c.id === convId) newChat(); loadConversations(); }}>×</button>
            </div>
          ))}
        </div>
      </aside>

      <section style={{ display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0 }}>
        <header className="row" style={{ padding: "12px 20px", borderBottom: "1px solid var(--border)", background: "var(--surface)", gap: 12 }}>
          <div className="grow" style={{ minWidth: 0 }}>
            <h2 style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{doc?.name ?? "Chat"}</h2>
            {doc?.stats.rules !== undefined && <div className="muted small">{doc.stats.rules} compiled rules · {doc.stats.facts} facts · {doc.stats.sections} clauses</div>}
          </div>
        </header>

        <div style={{ flex: 1, overflow: "auto" }}>
          <div style={{ maxWidth: 900, margin: "0 auto", padding: "24px 20px" }} className="stack">
            {empty && (
              <div style={{ padding: "40px 0" }}>
                <h1 style={{ marginBottom: 6 }}>Ask about a vessel call</h1>
                <p className="ink-2" style={{ marginTop: 0 }}>
                  Describe the vessel and the call in plain language or paste its particulars as JSON. Every amount comes from the compiled tariff rules; the explanation streams in as it is written.
                </p>
                <div className="stack" style={{ gap: 8, marginTop: 18 }}>
                  {EXAMPLES.map((e) => (
                    <button key={e} className="btn" style={{ justifyContent: "flex-start", whiteSpace: "normal", textAlign: "left" }} onClick={() => send(e)}>{e}</button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m) =>
              m.role === "user" ? (
                <UserBubble key={m.id} text={m.content} />
              ) : (
                <AssistantTurn key={m.id} content={m.content} trace={replay(m.meta)} live={false}
                  latency={m.meta.latency_s} tokens={m.meta.tokens} traceUrl={m.meta.trace_url}
                  error={m.meta.error ? m.content : undefined} />
              ),
            )}

            {pending && (
              <>
                <UserBubble text={pending.question} />
                <AssistantTurn content={pending.content} trace={pending.trace} live={!pending.error} error={pending.error} />
              </>
            )}
            <div ref={bottom} />
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          style={{ borderTop: "1px solid var(--border)", background: "var(--surface)", padding: 14 }}
        >
          <div className="row" style={{ maxWidth: 900, margin: "0 auto", alignItems: "flex-end" }}>
            <textarea
              className="textarea"
              rows={2}
              value={input}
              placeholder="e.g. Port dues for a 40,000 GT container ship staying 2 days in Cape Town?"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              style={{ resize: "vertical", minHeight: 44, maxHeight: 240 }}
              disabled={!docId}
            />
            {pending && !pending.error ? (
              <button type="button" className="btn" onClick={() => abort.current?.abort()}>Stop</button>
            ) : (
              <button type="submit" className="btn btn-primary" disabled={!input.trim() || !docId}>Send</button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end" }}>
      <div style={{ background: "var(--accent-soft)", padding: "10px 14px", borderRadius: "14px 14px 4px 14px", maxWidth: "80%", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        {text}
      </div>
    </div>
  );
}

function AssistantTurn(props: {
  content: string;
  trace: Trace;
  live: boolean;
  latency?: number;
  tokens?: TokenUsage;
  traceUrl?: string | null;
  error?: string;
}) {
  const { content, trace, live, latency, tokens, traceUrl, error } = props;
  const [showTrace, setShowTrace] = useState(live);
  useEffect(() => {
    if (live) setShowTrace(true);
  }, [live]);
  const hasTrace = !!trace.profile;

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="row small" style={{ marginBottom: 10, gap: 10 }}>

        {latency !== undefined && <span className="muted">{seconds(latency)}</span>}
        {tokens && (
          <span className="muted" title={tokenTitle(tokens)}>
            {compact(tokens.input + tokens.output)} tokens{tokens.cached ? ` · ${Math.round((tokens.cached / Math.max(tokens.input, 1)) * 100)}% cached` : ""}
          </span>
        )}
        {tokens?.cost_usd !== undefined && <span className="muted">{usd(tokens.cost_usd)}</span>}
        {traceUrl && <a className="small" href={traceUrl} target="_blank" rel="noreferrer">trace ↗</a>}
        <span className="grow" />
        {hasTrace && (
          <button className="btn btn-ghost btn-sm" onClick={() => setShowTrace((s) => !s)}>
            {showTrace ? "Hide" : "Show"} working
          </button>
        )}
      </div>
      {(showTrace || (live && !content)) && (hasTrace || live) && (
        <div style={{ borderLeft: "2px solid var(--grid)", paddingLeft: 12, marginBottom: 14 }}>
          <TraceView trace={trace} live={live} />
        </div>
      )}
      {content && (
        <div>
          <Markdown>{content + (live ? " ▍" : "")}</Markdown>
        </div>
      )}
      {error && <div className="error-box" style={{ marginTop: 10 }}>{error}</div>}
      {trace.lines && trace.lines.length > 0 && !live && (
        <details style={{ marginTop: 12 }}>
          <summary className="small" style={{ color: "var(--accent)", fontWeight: 560 }}>Line-item breakdown ({trace.lines.filter((l) => l.status !== "not_applicable").length})</summary>
          <div style={{ marginTop: 8 }}>
            <Breakdown lines={trace.lines} currency={trace.currency} />
          </div>
        </details>
      )}
    </div>
  );
}
