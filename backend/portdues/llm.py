"""Model factories and Langfuse wiring.

Everything that talks to OpenAI goes through here so model names, retries and
tracing are configured in one place.
"""

import logging
import re
from functools import lru_cache
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from portdues.config import get_settings

log = logging.getLogger(__name__)


Role = Literal["fast", "compiler", "judge"]


@lru_cache(maxsize=16)
def chat_model(role: Role = "fast", streaming: bool = False) -> ChatOpenAI:
    """fast: transcription, extraction, digests, synthesis. compiler: rule compilation. judge: eval grading."""
    s = get_settings()
    model, effort = {
        "fast": (s.fast_model, s.effort_fast),
        "compiler": (s.compiler_model, s.effort_compiler),
        "judge": (s.judge_model, s.effort_fast),
    }[role]
    return ChatOpenAI(
        model=model,
        api_key=s.openai_api_key,
        reasoning_effort=effort,
        max_retries=4,
        timeout=600,
        streaming=streaming,
        stream_usage=True,
    )


@lru_cache(maxsize=1)
def embeddings() -> OpenAIEmbeddings:
    s = get_settings()
    return OpenAIEmbeddings(
        model=s.embedding_model, api_key=s.openai_api_key, dimensions=s.embedding_dim
    )


def price_for(model: str) -> dict[str, float] | None:
    """Prices for a model name as returned by the API, which may carry a date suffix."""
    prices = get_settings().model_prices
    for name in sorted(prices, key=len, reverse=True):
        if model == name or model.startswith(name + "-"):
            return prices[name]
    return None


@lru_cache(maxsize=1)
def _langfuse_ready() -> bool:
    s = get_settings()
    if not s.tracing_enabled:
        return False
    try:
        from langfuse import Langfuse

        client = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key, host=s.langfuse_host)
    except Exception:  # tracing must never take the app down
        log.exception("langfuse init failed, tracing disabled")
        return False
    _register_prices(client)
    return True


def _langfuse_prices(p: dict[str, float]) -> dict[str, float]:
    """Per-token prices for every usage key the LangChain handler emits for OpenAI calls.

    The handler splits input into input / input_cache_read / input_cache_creation (and output into
    output / output_reasoning); a key without a price is costed at zero in Langfuse, so all of them
    are priced. Cache writes and audio are billed as ordinary input/output.
    """
    inp, out = p["input"] / 1e6, p["output"] / 1e6
    return {
        "input": inp, "input_cache_read": p.get("cached_input", p["input"]) / 1e6, "input_cache_creation": inp,
        "input_audio": inp, "output": out, "output_reasoning": out, "output_audio": out,
    }


def _register_prices(client: Any) -> None:
    """Give Langfuse a price for every model we use, so its cost views match ours. A definition whose
    prices differ (e.g. an older one missing a usage key) is replaced; Langfuse costs observations when
    they are ingested, so the change applies to new traces."""
    try:
        from langfuse.api import PricingTierInput

        existing = {m.model_name: m for m in client.api.models.list(limit=100).data if not m.is_langfuse_managed}
        for name, p in get_settings().model_prices.items():
            wanted = _langfuse_prices(p)
            current = existing.get(name)
            if current is not None:
                tiers = getattr(current, "pricing_tiers", None) or []
                if tiers and dict(tiers[0].prices) == wanted:
                    continue
                client.api.models.delete(current.id)
            client.api.models.create(
                model_name=name, match_pattern=f"(?i)^{re.escape(name)}(-\\d{{4}}-\\d{{2}}-\\d{{2}})?$",
                unit="TOKENS",
                pricing_tiers=[PricingTierInput(name="Standard", is_default=True, priority=0, conditions=[],
                                                prices=wanted)],
            )
            log.info("registered %s prices with langfuse", name)
    except Exception:
        log.warning("could not register model prices with langfuse; costs there may be missing", exc_info=True)


def new_trace_id() -> str | None:
    if not _langfuse_ready():
        return None
    from langfuse import Langfuse

    return Langfuse.create_trace_id()


def trace_url(trace_id: str | None) -> str | None:
    if not trace_id:
        return None
    s = get_settings()
    return f"{s.langfuse_public_url.rstrip('/')}/project/{s.langfuse_project_id}/traces/{trace_id}"


def run_config(
    name: str,
    *,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    callbacks: list[Any] | None = None,
    trace_id: str | None = None,
) -> RunnableConfig:
    """RunnableConfig with a Langfuse handler attached when tracing is configured.

    Calls that share a `trace_id` land in one Langfuse trace (one chat turn = one trace); calls that
    only share a `session_id` are grouped as a session (one ingestion, one conversation).
    """
    meta = dict(metadata or {})
    if session_id:
        meta["langfuse_session_id"] = session_id
    if tags:
        meta["langfuse_tags"] = tags
    handlers = list(callbacks or [])
    if _langfuse_ready():
        from langfuse.langchain import CallbackHandler

        context = {"trace_id": trace_id} if trace_id else None
        handlers.append(CallbackHandler(public_key=get_settings().langfuse_public_key, trace_context=context))
    return {"run_name": name, "metadata": meta, "tags": tags or [], "callbacks": handlers}


def usage_totals(usage_metadata: dict[str, Any]) -> dict[str, Any]:
    """Collapse LangChain's per-model usage into token counts plus cost in USD.

    cost_usd covers models with a known price; any others are listed under `unpriced`.
    """
    out: dict[str, Any] = {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "cost_usd": 0.0}
    unpriced = []
    for model, usage in usage_metadata.items():
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cached = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
        out["input"] += tokens_in
        out["output"] += tokens_out
        out["cached"] += cached
        out["reasoning"] += (usage.get("output_token_details") or {}).get("reasoning", 0) or 0
        prices = price_for(model)
        if prices is None:
            unpriced.append(model)
            continue
        out["cost_usd"] += ((tokens_in - cached) * prices["input"] + cached * prices.get("cached_input", prices["input"])
                            + tokens_out * prices["output"]) / 1e6
    out["cost_usd"] = round(out["cost_usd"], 6)
    if unpriced:
        out["unpriced"] = unpriced
    return out


def flush_traces() -> None:
    if _langfuse_ready():
        from langfuse import get_client

        get_client().flush()
