"""Scorers for the golden set.

numeric  deterministic: each expected charge is matched to the answer's line items and counts
         as a hit within a relative tolerance (1% by default)
judge    LLM-as-judge with a rubric and the reference figures
laya     Laya (open-source System-1 decision model, ModernBERT) answering typed yes/no questions
         about the answer: does it give an amount for each requested charge, is it itemised,
         does it cite clauses and state assumptions. Laya is fast and calibrated but is an
         encoder classifier, so arithmetic agreement is left to the other two scorers.
"""

import asyncio
import logging
import os
import re
import threading
from functools import lru_cache
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from portdues.config import get_settings
from langchain_core.callbacks import UsageMetadataCallbackHandler

from portdues.llm import chat_model, run_config, usage_totals

log = logging.getLogger(__name__)

CHARGE_NAMES = {
    "light_dues": "light dues",
    "port_dues": "port dues",
    "vts_dues": "VTS dues",
    "pilotage_dues": "pilotage",
    "towage_dues": "towage / tug assistance",
    "berthing_services": "berthing services",
    "running_lines": "running of vessel lines",
    "cargo_dues": "cargo dues",
}


def canonical(name: str) -> str:
    """Map a free-text line item name onto the golden set's charge keys."""
    n = name.lower()
    if "light" in n:
        return "light_dues"
    if "vts" in n or "traffic" in n:
        return "vts_dues"
    if "pilot" in n and "exemption" not in n:
        return "pilotage_dues"
    if "running" in n or "line handling" in n:
        return "running_lines"
    if "tow" in n or "tug" in n or "craft" in n:
        return "towage_dues"
    if "berthing" in n or "mooring" in n:
        return "berthing_services"
    if "berth due" in n:
        return "berth_dues"
    if re.search(r"\b(port|harbour)\s+(dues|fees)\b", n):
        return "port_dues"
    if "cargo" in n:
        return "cargo_dues"
    return re.sub(r"\W+", "_", n).strip("_")


# --------------------------------------------------------------------------- numeric


def numeric_score(case: dict, lines: list[dict], tolerance: float = 0.01) -> dict[str, Any]:
    predicted: dict[str, float] = {}
    for line in lines:
        key = canonical(line["name"])
        if line["status"] == "charged" and line.get("amount") is not None:
            predicted[key] = predicted.get(key, 0.0) + float(line["amount"])
        elif line["status"] == "exempt":
            predicted.setdefault(key, 0.0)

    per_charge, hits = {}, 0
    for key, exp in case["expected"].items():
        got = predicted.get(key)
        if exp == 0:
            hit, err = (got in (None, 0.0)), (0.0 if not got else 1.0)
        elif got is None:
            hit, err = False, None
        else:
            err = abs(got - exp) / exp
            hit = err <= tolerance
        hits += hit
        per_charge[key] = {"expected": exp, "predicted": got, "error": None if err is None else round(err, 5),
                           "hit": hit}
    errors = [c["error"] for c in per_charge.values() if c["error"] is not None]
    return {
        "score": round(hits / max(len(case["expected"]), 1), 4),
        "exact": hits == len(case["expected"]),
        "mean_abs_error": round(sum(errors) / len(errors), 5) if errors else None,
        "per_charge": per_charge,
        "extra": sorted(set(predicted) - set(case["expected"])),
    }


# --------------------------------------------------------------------------- llm judge

JUDGE_PROMPT = """You grade answers from a port-tariff assistant against reference figures.

Score each criterion 1-5:
- correctness: do the stated amounts match the reference (within ~1%)? 5 = all match, 1 = none do.
  Charges the reference lists as 0 are exempt; saying so is correct.
- completeness: does the answer cover every charge in the reference? Extra charges are fine if they
  are plausibly payable and clearly itemised.
- reasoning: does it show the basis of each figure (rates, units, clauses) and state assumptions?
verdict is pass only if correctness >= 4 and completeness >= 4."""


class JudgeVerdict(BaseModel):
    correctness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    reasoning: int = Field(ge=1, le=5)
    verdict: Literal["pass", "fail"]
    rationale: str = Field(description="Two sentences at most")


async def judge_score(case: dict, answer: str, session_id: str) -> dict[str, Any]:
    reference = "\n".join(f"- {CHARGE_NAMES.get(k, k)}: {v:,.2f} {case['currency']}" for k, v in case["expected"].items())
    usage = UsageMetadataCallbackHandler()
    verdict: JudgeVerdict = await chat_model("judge").with_structured_output(JudgeVerdict).ainvoke(
        [SystemMessage(JUDGE_PROMPT),
         HumanMessage(f"Question:\n{case['question']}\n\nReference figures:\n{reference}\n\nAnswer:\n{answer}")],
        config=run_config("judge", session_id=session_id, tags=["eval", "judge"], callbacks=[usage]),
    )
    norm = lambda x: (x - 1) / 4
    score = 0.6 * norm(verdict.correctness) + 0.25 * norm(verdict.completeness) + 0.15 * norm(verdict.reasoning)
    return {"score": round(score, 4), **verdict.model_dump(), "tokens": usage_totals(usage.usage_metadata)}


# --------------------------------------------------------------------------- laya

_laya_lock = threading.Lock()


@lru_cache(maxsize=1)
def _laya_agent():
    os.environ.setdefault("USE_TF", "0")
    import laya

    s = get_settings()
    return laya.load(s.laya_model, device=s.laya_device)


def laya_available() -> bool:
    if not get_settings().laya_enabled:
        return False
    try:
        import laya  # noqa: F401
        return True
    except ImportError:
        return False


QUALITY_QUESTIONS = {
    "itemised": "The answer lists the charges one by one with an amount for each.",
    "cites_clauses": "The answer refers to tariff clause or section numbers.",
    "states_assumptions": "The answer states the assumptions or inputs it relied on.",
}


def _laya_predict(case: dict, answer: str) -> dict[str, Any]:
    questions = {
        f"covers_{key}": {"type": "noul", "instructions": f"The answer gives an amount for {CHARGE_NAMES.get(key, key)}."}
        for key in case["expected"]
    }
    questions.update({k: {"type": "noul", "instructions": q} for k, q in QUALITY_QUESTIONS.items()})
    with _laya_lock:
        result = _laya_agent().predict(answer[:6000], questions)
    probs = {k: round(float(v["noul"]), 4) for k, v in result["answers"].items()}
    coverage = [p for k, p in probs.items() if k.startswith("covers_")]
    quality = [probs[k] for k in QUALITY_QUESTIONS]
    score = 0.7 * (sum(coverage) / len(coverage)) + 0.3 * (sum(quality) / len(quality))
    return {"score": round(score, 4), "probabilities": probs}


async def laya_score(case: dict, answer: str) -> dict[str, Any]:
    return await asyncio.to_thread(_laya_predict, case, answer)


async def score_all(case: dict, answer: str, lines: list[dict], scorers: list[str], session_id: str
                    ) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "numeric" in scorers:
        out["numeric"] = numeric_score(case, lines)
    jobs = {}
    if "judge" in scorers and answer:
        jobs["judge"] = judge_score(case, answer, session_id)
    if "laya" in scorers and answer and laya_available():
        jobs["laya"] = laya_score(case, answer)
    for name, result in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
        if isinstance(result, Exception):
            log.warning("%s scorer failed: %s", name, result)
            out[name] = {"score": None, "error": str(result)[:200]}
        else:
            out[name] = result
    return out
