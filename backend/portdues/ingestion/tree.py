"""Fact extraction and hierarchical consolidation (a structure-aware take on RAPTOR).

RAPTOR clusters chunks by embedding similarity and summarises the clusters recursively.
A tariff book already tells us how its content is grouped: clauses nest inside sections.
So instead of clustering we walk the clause tree:

  pass 1  every clause's own text -> atomic, self-contained facts (level 0)
  pass 2+ every clause, deepest first -> a digest of its facts plus its children's
          digests (level = height in the tree), up to a root digest that works as a
          catalogue of every charge in the document.

All three node kinds (verbatim source chunks, facts, digests) are embedded, so retrieval
can land on whichever granularity matches the question.
"""

import asyncio
import hashlib
import logging
from typing import Awaitable, Callable, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from portdues.config import get_settings
from portdues.ingestion.sections import Section, chunk_text
from portdues.llm import chat_model, run_config

log = logging.getLogger(__name__)

FACT_PROMPT = """You extract facts from one clause of a port tariff document.

Return atomic facts that a person calculating a vessel's port costs would need. Each fact must
stand on its own without the surrounding text: name the charge, the port(s) it applies to,
the unit/basis, the exact amount and any condition, e.g.
"Pilotage at Durban: basic fee of 18 608.61 per service (normal entering or leaving the port)."

Cover rates and fees, the basis of measurement and rounding ("per 100 tons or part thereof"),
minimums/maximums, banded tables (one fact per band and port), surcharges, reductions,
exemptions, who pays, and definitions that change how a charge is computed. Copy every number
exactly. Skip contact details and pure procedure that has no effect on amounts."""

DIGEST_PROMPT = """You consolidate one clause of a port tariff document into a dense reference digest.

You get the clause's own facts and the digests of its sub-clauses. Write a markdown digest that
groups content by charge (what is charged, who pays, on what basis) and keeps every rate, band,
minimum, surcharge, reduction and exemption with its exact figure and clause, as [§x.y].
Never invent anything that is not in the input."""

OVERVIEW_PROMPT = """You write the overview node for a large part of a port tariff document.

You get its own facts and the digests of its sub-clauses, which already hold the exact figures. Write a compact
markdown overview (at most ~{words} words) that a reader uses to navigate: for each charge, one or two
lines on what is charged, who pays, the basis of calculation (e.g. per 100 GT per service, per metre
LOA, per port call), notable surcharges/reductions/exemptions, and the clause to read for the rates,
as [§x.y]. Do not copy rate tables. Never invent anything that is not in the input.
{root}"""

ROOT_EXTRA = """This is the root of the document: start with a "Charges in this document" list naming every
charge levied on vessels or cargo and its clause, then any document-wide rules (currency, tax, tonnage
definition)."""

# Digests for nodes this high (or higher) summarise rather than copy their children.
OVERVIEW_HEIGHT = 2


class Fact(BaseModel):
    statement: str = Field(description="Self-contained fact with exact figures")
    category: Literal[
        "rate", "basis", "minimum", "surcharge", "reduction", "exemption", "condition", "definition", "procedure"
    ]
    charge: str = Field(description="The charge or due this fact belongs to, or 'general'")


class FactSheet(BaseModel):
    facts: list[Fact]


ProgressFn = Callable[[str, float], Awaitable[None]]


def _node_id(doc_id: str, kind: str, key: str, i: int | None = None) -> str:
    base = f"{doc_id}:{kind}:{key or 'root'}"
    return base if i is None else f"{base}:{i}"


def _label(section: Section) -> str:
    return f"§{section.key} {section.title}".strip() if section.key else section.title


async def _extract_facts(section: Section, doc_id: str) -> list[Fact]:
    text = section.text
    if len(text) < 40:
        return []
    llm = chat_model().with_structured_output(FactSheet)
    facts: list[Fact] = []
    for part in chunk_text(text, size=9000):
        sheet = await llm.ainvoke(
            [SystemMessage(FACT_PROMPT), HumanMessage(f"Clause {_label(section)}\n\n{part}")],
            config=run_config("extract_facts", session_id=doc_id, tags=["ingest"],
                              metadata={"section": section.key}),
        )
        facts.extend(sheet.facts)
    return facts


async def _digest(section: Section, height: int, facts: list[Fact], child_digests: list[tuple[Section, str]],
                  doc_id: str, notes: list[str]) -> str:
    parts = [f"# {_label(section)}"]
    if notes and not section.key:
        parts.append("Document-wide notes:\n" + "\n".join(f"- {n}" for n in notes))
    if facts:
        parts.append("## Facts from this clause\n" + "\n".join(f"- {f.statement}" for f in facts))
    for child, digest in child_digests:
        parts.append(f"## Sub-clause {_label(child)}\n{digest}")
    if len(parts) == 1:
        return section.title
    if height >= OVERVIEW_HEIGHT or not section.key:
        system = OVERVIEW_PROMPT.format(words=900 if not section.key else 500,
                                        root=ROOT_EXTRA if not section.key else "")
    else:
        system = DIGEST_PROMPT
    result = await chat_model().ainvoke(
        [SystemMessage(system), HumanMessage("\n\n".join(parts))],
        config=run_config("digest_section", session_id=doc_id, tags=["ingest"],
                          metadata={"section": section.key or "root"}),
    )
    return result.text.strip()


async def build_tree(
    sections: list[Section], notes: list[str], doc_id: str, progress: ProgressFn,
    fact_cache: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Run both passes. `fact_cache` (keyed by clause text hash) lets a restarted ingestion skip pass 1
    for clauses it already processed; it is updated in place."""
    fact_cache = {} if fact_cache is None else fact_cache
    gate = asyncio.Semaphore(get_settings().llm_concurrency)
    children: dict[str, list[Section]] = {s.key: [] for s in sections}
    for s in sections:
        if s.parent is not None:
            children[s.parent].append(s)
    for s in sections:
        if not s.title and children[s.key]:
            s.title = children[s.key][0].title

    # pass 1: facts
    facts: dict[str, list[Fact]] = {}
    done = 0

    async def facts_for(s: Section) -> None:
        nonlocal done
        key = hashlib.sha1(f"{s.key}\n{s.text}".encode()).hexdigest()
        if key in fact_cache:
            facts[s.key] = [Fact.model_validate(f) for f in fact_cache[key]]
        else:
            async with gate:
                try:
                    facts[s.key] = await _extract_facts(s, doc_id)
                    fact_cache[key] = [f.model_dump() for f in facts[s.key]]
                except Exception:
                    log.exception("fact extraction failed for %s", s.key)
                    facts[s.key] = []
        done += 1
        await progress(f"Extracting facts ({done}/{len(sections)})", done / len(sections))

    await asyncio.gather(*(facts_for(s) for s in sections))

    # pass 2..n: digests, deepest clauses first so parents can build on their children
    heights: dict[str, int] = {}

    def height(key: str) -> int:
        if key not in heights:
            heights[key] = 1 + max((height(c.key) for c in children[key]), default=0)
        return heights[key]

    digests: dict[str, str] = {}
    max_height = height("")
    for level in range(1, max_height + 1):
        batch = [s for s in sections if height(s.key) == level]

        async def digest_for(s: Section) -> None:
            async with gate:
                kids = [(c, digests[c.key]) for c in children[s.key]]
                try:
                    digests[s.key] = await _digest(s, height(s.key), facts.get(s.key, []), kids, doc_id, notes)
                except Exception:
                    log.exception("digest failed for %s", s.key)
                    digests[s.key] = "\n".join(f.statement for f in facts.get(s.key, [])) or s.title

        await asyncio.gather(*(digest_for(s) for s in batch))
        await progress(f"Consolidating level {level}/{max_height}", level / max_height)

    # flatten into storable nodes
    nodes: list[dict] = []
    for order, s in enumerate(sections):
        sid = _node_id(doc_id, "section", s.key)
        pages = sorted(s.pages)
        nodes.append({
            "id": sid,
            "parent_id": None if s.parent is None else _node_id(doc_id, "section", s.parent),
            "kind": "section", "level": height(s.key), "section": s.key, "title": s.title,
            "text": digests.get(s.key, s.title), "pages": pages, "ord": order * 1000,
            "meta": {"depth": s.depth, "own_chars": len(s.text), "facts": len(facts.get(s.key, []))},
        })
        for i, chunk in enumerate(chunk_text(s.text)):
            nodes.append({
                "id": _node_id(doc_id, "source", s.key, i), "parent_id": sid, "kind": "source", "level": 0,
                "section": s.key, "title": s.title, "text": chunk, "pages": pages, "ord": order * 1000 + i,
            })
        for i, f in enumerate(facts.get(s.key, [])):
            nodes.append({
                "id": _node_id(doc_id, "fact", s.key, i), "parent_id": sid, "kind": "fact", "level": 0,
                "section": s.key, "title": s.title, "text": f.statement, "pages": pages,
                "ord": order * 1000 + 500 + i, "meta": {"category": f.category, "charge": f.charge},
            })
    return nodes
