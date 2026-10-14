"""Ingestion orchestration: PDF -> pages -> clause tree -> facts/digests -> index -> rulebook."""

import asyncio
import json
import logging
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from portdues.config import get_settings
from portdues.ingestion.pages import LogicalPage, extract_pages
from portdues.ingestion.sections import build_sections
from portdues.ingestion.tree import build_tree
from langchain_core.callbacks import get_usage_metadata_callback

from portdues.llm import flush_traces, usage_totals
from portdues.rules.compiler import compile_rulebook, recompile_charges
from portdues.storage.db import get_db
from portdues.storage.vectors import get_index

log = logging.getLogger(__name__)

# share of the progress bar each stage owns
STAGES = {"reading": (0.0, 0.35), "structuring": (0.35, 0.7), "indexing": (0.7, 0.75), "compiling": (0.75, 1.0)}

_running: dict[str, asyncio.Task] = {}


class Checkpoint:
    """JSON files under data/cache/<doc_id> so a crashed or restarted ingestion does not pay twice
    for vision transcription and fact extraction."""

    def __init__(self, doc_id: str):
        self.dir = get_settings().data_dir / "cache" / doc_id

    def load(self, name: str):
        path = self.dir / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def save(self, name: str, value) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{name}.json").write_text(json.dumps(value))

    def clear(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class Reporter:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self._last = 0.0

    def stage(self, name: str):
        lo, hi = STAGES[name]

        async def report(message: str, fraction: float) -> None:
            now = time.monotonic()
            if fraction < 1 and now - self._last < 0.5:
                return
            self._last = now
            await get_db().update_document(self.doc_id, status="running", stage=message,
                                           progress=round(lo + (hi - lo) * fraction, 4))

        return report


async def ingest(doc_id: str, pdf_path: Path, *, compile_only: bool = False, fresh: bool = False,
                 rule_ids: list[str] | None = None) -> None:
    """Full ingestion; `compile_only` rebuilds the rulebook from the stored tree, and `rule_ids`
    narrows that to specific rules."""
    db, index = get_db(), get_index()
    doc = await db.get_document(doc_id)
    reporter = Reporter(doc_id)
    started = time.monotonic()
    stats = dict(doc["stats"] or {})
    checkpoint = Checkpoint(doc_id)
    # stages that were skipped (resumed from checkpoint, compile-only) keep their previous counts
    tokens: dict[str, dict[str, int]] = dict(stats.get("tokens") or {})
    if fresh:
        checkpoint.clear()
    try:
        if not compile_only:
            report = reporter.stage("reading")

            async def on_page(done: int, total: int) -> None:
                await report(f"Reading pages ({done}/{total})", done / total)

            cached = checkpoint.load("pages")
            if cached:
                pages = [LogicalPage(**p) for p in cached]
            else:
                await report("Splitting pages", 0)
                with get_usage_metadata_callback() as usage:
                    pages = await extract_pages(pdf_path, doc_id, on_page=on_page)
                tokens["vision"] = usage_totals(usage.usage_metadata)
                checkpoint.save("pages", [asdict(p) for p in pages])
            stats.update(pages=len(pages), vision_pages=sum(p.via == "vision" for p in pages))

            sections, notes = build_sections(pages, doc["name"])
            fact_cache = checkpoint.load("facts") or {}
            try:
                with get_usage_metadata_callback() as usage:
                    nodes = await build_tree(sections, notes, doc_id, reporter.stage("structuring"), fact_cache)
                tokens["tree"] = usage_totals(usage.usage_metadata)
            finally:
                checkpoint.save("facts", fact_cache)
            stats.update(
                sections=len(sections) - 1,
                facts=sum(n["kind"] == "fact" for n in nodes),
                chunks=sum(n["kind"] == "source" for n in nodes),
                tree_height=max(n["level"] for n in nodes),
            )
            await db.replace_nodes(doc_id, nodes, kinds=["section", "source", "fact"])

            await reporter.stage("indexing")("Embedding nodes", 0)
            await index.delete_document(doc_id)
            await index.index_nodes(doc_id, nodes)

        compile_started = time.monotonic()
        with get_usage_metadata_callback() as usage:
            if rule_ids:
                rulebook = await recompile_charges(doc_id, rule_ids, reporter.stage("compiling"))
            else:
                rulebook = await compile_rulebook(doc_id, reporter.stage("compiling"))
        stage = "recompile" if rule_ids else "compile"
        tokens[stage] = usage_totals(usage.usage_metadata)
        if not rule_ids:
            rulebook.compiled_with.update(tokens=tokens[stage], seconds=round(time.monotonic() - compile_started))
        await db.save_rulebook(doc_id, rulebook.model_dump())
        stats.update(rules=len(rulebook.charges), uncovered=len(rulebook.uncovered),
                     seconds=round(time.monotonic() - started), tokens=tokens)
        await db.update_document(doc_id, status="ready", stage="Ready", progress=1.0, stats=stats, error=None)
    except asyncio.CancelledError:
        await db.update_document(doc_id, status="failed", error="cancelled")
        raise
    except Exception as exc:
        log.exception("ingestion failed for %s", doc_id)
        await db.update_document(doc_id, status="failed", error=str(exc)[:500], stats=stats)
    finally:
        flush_traces()


def start_ingestion(doc_id: str, pdf_path: Path, *, compile_only: bool = False, fresh: bool = False,
                    rule_ids: list[str] | None = None) -> None:
    if doc_id in _running and not _running[doc_id].done():
        return
    task = asyncio.create_task(ingest(doc_id, pdf_path, compile_only=compile_only, fresh=fresh, rule_ids=rule_ids))
    _running[doc_id] = task
    task.add_done_callback(lambda _: _running.pop(doc_id, None))


def is_running(doc_id: str) -> bool:
    return doc_id in _running and not _running[doc_id].done()
