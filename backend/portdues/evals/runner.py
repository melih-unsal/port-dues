"""Experiment runner with live progress.

An experiment runs the selected golden cases through the answering pipeline, scores each answer and keeps
a rolling summary. Runs happen in a background task; `ExperimentHub` fans the events out to any number of
SSE subscribers and replays history to late joiners.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, AsyncIterator

from portdues.agents.strategies import run_turn
from portdues.evals.scorers import laya_available, score_all
from portdues.storage.db import get_db

log = logging.getLogger(__name__)

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
DIFFICULTIES = ("easy", "medium", "hard")


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]


def select_cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    cases = load_cases()
    if config.get("case_ids"):
        cases = [c for c in cases if c["id"] in config["case_ids"]]
    if config.get("difficulty"):
        cases = [c for c in cases if c["difficulty"] in config["difficulty"]]
    if config.get("limit"):
        per = defaultdict(list)
        for c in cases:
            per[c["difficulty"]].append(c)
        # keep the mix of difficulties when sampling
        picked, i = [], 0
        while len(picked) < config["limit"] and any(per.values()):
            d = DIFFICULTIES[i % 3]
            if per[d]:
                picked.append(per[d].pop(0))
            i += 1
        cases = picked
    return cases


def _avg(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(mean(vals), 4) if vals else None


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Scores, latency, tokens and cost for one run, overall and by difficulty and charge."""
    score = lambda name: [(i["scores"].get(name) or {}).get("score") for i in results]
    charge_hits: dict[str, list[float]] = defaultdict(list)
    for i in results:
        for key, c in ((i["scores"].get("numeric") or {}).get("per_charge") or {}).items():
            charge_hits[key].append(1.0 if c["hit"] else 0.0)
    tokens = lambda i: i.get("tokens") or {}
    answer_cost = sum(tokens(i).get("cost_usd") or 0 for i in results)
    judge_cost = sum(((i["scores"].get("judge") or {}).get("tokens") or {}).get("cost_usd") or 0 for i in results)
    return {
        "completed": len(results),
        "errors": sum(1 for i in results if i.get("error")),
        "numeric": _avg(score("numeric")),
        "judge": _avg(score("judge")),
        "laya": _avg(score("laya")),
        "exact": sum(1 for i in results if (i["scores"].get("numeric") or {}).get("exact")),
        "exact_rate": _avg([1.0 if (i["scores"].get("numeric") or {}).get("exact") else 0.0 for i in results]),
        "judge_pass_rate": _avg([1.0 if (i["scores"].get("judge") or {}).get("verdict") == "pass" else 0.0
                                 for i in results if i["scores"].get("judge")]),
        "latency_s": _avg([i.get("latency_s") for i in results]),
        # cached is a subset of input and reasoning a subset of output, so only add those two
        "tokens": _avg([tokens(i).get("input", 0) + tokens(i).get("output", 0) for i in results]),
        "cached_share": _avg([tokens(i).get("cached", 0) / max(tokens(i).get("input", 0), 1) for i in results]),
        "by_difficulty": {
            d: _avg([(i["scores"].get("numeric") or {}).get("score") for i in results if i["difficulty"] == d])
            for d in DIFFICULTIES
        },
        "by_charge": {k: _avg(v) for k, v in charge_hits.items()},
        "cost_usd": {"answers": round(answer_cost, 4), "judge": round(judge_cost, 4),
                     "total": round(answer_cost + judge_cost, 4)},
    }


async def _run_case(doc_id: str, exp_id: str, case: dict, strategy: str, scorers: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {}
    error = None
    async for event in run_turn(doc_id, case["question"], strategy=strategy,
                                session_id=f"{exp_id}:{case['id']}"):
        if event["type"] == "result":
            result = event
        elif event["type"] == "error":
            error = event["message"]
    answer, lines = result.get("answer", ""), result.get("lines", [])
    scores = await score_all(case, answer, lines, scorers, session_id=f"{exp_id}:{case['id']}") \
        if not error else {"numeric": {"score": 0.0, "exact": False, "per_charge": {}}}
    return {
        "case_id": case["id"], "strategy": strategy, "difficulty": case["difficulty"],
        "question": case["question"], "expected": case["expected"], "answer": answer, "lines": lines,
        "scores": scores, "error": error, "latency_s": result.get("latency_s", round(time.monotonic() - started, 2)),
        "tokens": result.get("tokens"), "trace_url": result.get("trace_url"),
    }



async def run_experiment(exp_id: str) -> AsyncIterator[dict[str, Any]]:
    db = get_db()
    exp = await db.get_experiment(exp_id)
    config = exp["config"]
    cases = select_cases(config)
    strategies = config.get("strategies") or ["compiled"]
    scorers = [s for s in (config.get("scorers") or ["numeric", "judge", "laya"])
               if s != "laya" or laya_available()]
    jobs = [(c, s) for c in cases for s in strategies]
    yield {"type": "start", "total": len(jobs), "strategies": strategies, "scorers": scorers,
           "cases": [{"id": c["id"], "difficulty": c["difficulty"]} for c in cases]}

    gate = asyncio.Semaphore(int(config.get("concurrency", 4)))
    queue: asyncio.Queue = asyncio.Queue()
    results: list[dict[str, Any]] = []

    async def job(case: dict, strategy: str) -> None:
        async with gate:
            await queue.put({"type": "running", "case_id": case["id"], "strategy": strategy})
            try:
                row = await _run_case(exp["doc_id"], exp_id, case, strategy, scorers)
            except Exception as exc:
                log.exception("case %s/%s failed", case["id"], strategy)
                row = {"case_id": case["id"], "strategy": strategy, "difficulty": case["difficulty"],
                       "question": case["question"], "expected": case["expected"], "answer": "", "lines": [],
                       "scores": {"numeric": {"score": 0.0, "exact": False, "per_charge": {}}},
                       "error": str(exc)[:300], "latency_s": None, "tokens": None}
            await db.save_result(exp_id, case["id"], strategy, row)
            results.append(row)
            await queue.put({"type": "case", **{k: row[k] for k in (
                "case_id", "strategy", "difficulty", "scores", "error", "latency_s", "tokens")},
                "summary": summarize(results)})

    tasks = [asyncio.create_task(job(c, s)) for c, s in jobs]
    pending = set(tasks)
    while pending or not queue.empty():
        try:
            yield await asyncio.wait_for(queue.get(), timeout=0.25)
        except asyncio.TimeoutError:
            pending = {t for t in pending if not t.done()}

    summary = summarize(results)
    await db.finish_experiment(exp_id, "done", summary)
    yield {"type": "done", "summary": summary}


class ExperimentHub:
    def __init__(self) -> None:
        self.history: dict[str, list[dict]] = {}
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, exp_id: str) -> None:
        self.history[exp_id] = []
        self.tasks[exp_id] = asyncio.create_task(self._drive(exp_id))

    async def _drive(self, exp_id: str) -> None:
        try:
            async for event in run_experiment(exp_id):
                self._publish(exp_id, event)
        except Exception as exc:
            log.exception("experiment %s crashed", exp_id)
            await get_db().finish_experiment(exp_id, "failed", {"error": str(exc)[:300]})
            self._publish(exp_id, {"type": "error", "message": str(exc)[:300]})
        finally:
            self._publish(exp_id, None)
            self.tasks.pop(exp_id, None)

    def _publish(self, exp_id: str, event: dict | None) -> None:
        if event is not None:
            self.history[exp_id].append(event)
        for q in self.subscribers[exp_id]:
            q.put_nowait(event)

    def is_running(self, exp_id: str) -> bool:
        return exp_id in self.tasks

    async def subscribe(self, exp_id: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        for event in self.history.get(exp_id, []):
            q.put_nowait(event)
        if not self.is_running(exp_id):
            q.put_nowait(None)
        self.subscribers[exp_id].add(q)
        try:
            while (event := await q.get()) is not None:
                yield event
        finally:
            self.subscribers[exp_id].discard(q)


hub = ExperimentHub()
