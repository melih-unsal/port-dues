"""Answering a question: query -> vessel profile -> deterministic rule engine -> streamed explanation.

Two LLM calls per question (profile extraction and the explanation); every amount comes from the compiled
rules. Query-time agents (a task-list agent with and without the compiled rules) were evaluated and removed:
they matched this pipeline's accuracy at several times the latency and tokens (see the README).

Event types: status, profile, lines, token, result, error.
"""

import json
import logging
import time
from typing import Any, AsyncIterator

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from portdues.agents.profile import extract_profile, to_values
from portdues.llm import chat_model, flush_traces, new_trace_id, run_config, trace_url, usage_totals
from portdues.rules.engine import quote
from portdues.rules.schema import ChargeLine, Rulebook
from portdues.storage.db import get_db

log = logging.getLogger(__name__)

STRATEGIES: dict[str, str] = {"compiled": "Compiled rules"}

SYNTH_PROMPT = """You are a port disbursement analyst explaining a cost estimate.

The figures below come from a deterministic tariff engine. Use them
exactly as given: never recompute, round differently or add charges of your own.

Write:
1. One sentence with the total of vessel charges (and the cargo total separately, if any).
2. A markdown table: Charge | Clause | How it was calculated | Amount ({currency}).
3. Short notes: assumptions that were applied, exemptions, charges that need more input, how tax applies.
If a fact needed by several charges is missing (most often the port of call), say so first and ask for it
instead of presenting a partial total as the answer.
If the user asked about particular charges, answer just those. Be concise; no preamble."""


class Turn:
    """Shared state for one question."""

    def __init__(self, doc_id: str, question: str, strategy: str, history: list[dict],
                 previous_profile: dict | None, session_id: str | None):
        self.doc_id = doc_id
        self.question = question
        self.strategy = strategy
        self.history = history[-6:]
        self.previous_profile = previous_profile
        self.session_id = session_id or doc_id
        self.usage = UsageMetadataCallbackHandler()
        self.rulebook: Rulebook | None = None
        self.trace_id = new_trace_id()  # every LLM call of this turn lands in one Langfuse trace

    def config(self, name: str) -> dict:
        return run_config(name, session_id=self.session_id, tags=["query", self.strategy],
                          metadata={"doc_id": self.doc_id}, callbacks=[self.usage], trace_id=self.trace_id)

    def history_messages(self) -> list[BaseMessage]:
        return [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]) for m in self.history]

    def tokens(self) -> dict[str, int]:
        return usage_totals(self.usage.usage_metadata)


def _event(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, **data}


def _payload(lines: list[ChargeLine]) -> list[dict]:
    return [l.model_dump() for l in lines]


async def _synthesize(turn: Turn, profile: dict, lines: list[ChargeLine]) -> AsyncIterator[dict]:
    rb = turn.rulebook
    facts = {
        "profile": {k: v for k, v in profile.items() if v not in (None, [], "")},
        "charges": [l.model_dump(exclude={"inputs"}) for l in lines if l.status != "not_applicable"],
        "not_applicable": [f"{l.name}: {l.reason}" for l in lines if l.status == "not_applicable"],
        "vessel_total": round(sum(l.amount or 0 for l in lines if l.status == "charged" and l.payer == "vessel"), 2),
        "tax_note": rb.tax_note if rb else "",
    }
    cargo_total = round(sum(l.amount or 0 for l in lines if l.status == "charged" and l.payer == "cargo"), 2)
    if cargo_total:
        facts["cargo_total"] = cargo_total
    messages = [
        SystemMessage(SYNTH_PROMPT.format(currency=rb.currency if rb else "local currency")),
        *turn.history_messages(),
        HumanMessage(f"Question: {turn.question}\n\nComputed figures:\n{json.dumps(facts, default=str)}"),
    ]
    async for chunk in chat_model("fast", streaming=True).astream(messages, config=turn.config("synthesize")):
        if chunk.text:
            yield _event("token", text=chunk.text)


async def _profile(turn: Turn) -> dict:
    return await extract_profile(turn.question, turn.rulebook, turn.previous_profile, turn.config("extract_profile"))


async def _run_compiled(turn: Turn) -> AsyncIterator[dict]:
    if not turn.rulebook or not turn.rulebook.charges:
        yield _event("error", message="This document has no compiled rules yet; recompile it from the Documents page.")
        return
    yield _event("status", message="Reading the vessel call")
    profile = await _profile(turn)
    yield _event("profile", profile=profile)
    result = quote(turn.rulebook, to_values(profile), profile.get("requested_charges") or None)
    yield _event("lines", lines=_payload(result.lines), currency=result.currency)
    answer = []
    async for ev in _synthesize(turn, profile, result.lines):
        answer.append(ev["text"])
        yield ev
    yield _event("result", answer="".join(answer), lines=_payload(result.lines), profile=profile)


_RUNNERS = {"compiled": _run_compiled}


async def run_turn(
    doc_id: str,
    question: str,
    strategy: str = "compiled",
    history: list[dict] | None = None,
    previous_profile: dict | None = None,
    session_id: str | None = None,
) -> AsyncIterator[dict]:
    if strategy not in _RUNNERS:
        yield _event("error", message=f"unknown strategy {strategy}")
        return
    turn = Turn(doc_id, question, strategy, history or [], previous_profile, session_id)
    body = await get_db().get_rulebook(doc_id)
    turn.rulebook = Rulebook.model_validate(body) if body else None
    started = time.monotonic()
    try:
        async for event in _RUNNERS[strategy](turn):
            if event["type"] == "result":
                event.update(strategy=strategy, latency_s=round(time.monotonic() - started, 2), tokens=turn.tokens(),
                             trace_url=trace_url(turn.trace_id))
            yield event
    except Exception as exc:
        log.exception("turn failed")
        yield _event("error", message=f"{type(exc).__name__}: {exc}")
    finally:
        flush_traces()
