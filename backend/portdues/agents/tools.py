"""Tools over the tariff tree, shared by the rule compiler and the query-time agents."""

from typing import Literal

from langchain_core.tools import BaseTool, tool

from portdues.rules.engine import Formula, MissingInput, RuleError
from portdues.storage.db import get_db
from portdues.storage.vectors import get_index

_SNIPPET = 1200

_KINDS = {
    "facts": ["fact"],
    "source": ["source"],
    "summaries": ["section"],
    "any": ["fact", "source", "section"],
}


def _cite(hit: dict) -> str:
    pages = hit.get("pages") or []
    where = f"§{hit['section']}" if hit.get("section") else "document"
    if pages:
        where += f", p.{pages[0]}" if len(pages) == 1 else f", pp.{pages[0]}-{pages[-1]}"
    return where


def build_tools(doc_id: str, *, with_calculator: bool = True) -> list[BaseTool]:
    @tool
    async def search_tariff(
        query: str,
        level: Literal["facts", "source", "summaries", "any"] = "any",
        section: str | None = None,
    ) -> str:
        """Semantic search over the tariff document.

        level: 'facts' for atomic extracted facts, 'source' for verbatim clause text (use this for
        exact figures and tables), 'summaries' for consolidated clause digests, 'any' for all.
        section: optionally restrict to a clause and its sub-clauses, e.g. '3.6'.
        """
        hits = await get_index().search(doc_id, query, kinds=_KINDS[level], section_prefix=section, limit=6)
        if not hits:
            return "No matches."
        out = []
        for h in hits:
            # keep results short: the agent can read_section() for the full clause
            text = h["text"] if len(h["text"]) < _SNIPPET else h["text"][:_SNIPPET] + " ... [truncated, use read_section]"
            out.append(f"[{h['kind']} | {_cite(h)} | {h['title']}]\n{text}")
        return "\n\n---\n\n".join(out)

    @tool
    async def read_section(section: str) -> str:
        """Read one clause in full: its verbatim text (incl. tables), its digest and its sub-clauses.

        section: clause number as printed, e.g. '4.1.1'. Use 'root' for the whole-document digest.
        """
        key = "" if section.strip().lower() in ("root", "") else section.strip().lstrip("§")
        db = get_db()
        node = await db.section_node(doc_id, key)
        if node is None:
            return f"No clause {section}. Use outline() to see the available clause numbers."
        sources = await db.children(node["id"], kind="source")
        kids = await db.children(node["id"], kind="section")
        parts = [f"§{node['section']} {node['title']} (pages {node['pages']})".strip()]
        if sources:
            parts.append("VERBATIM TEXT:\n" + "\n\n".join(s["text"] for s in sources))
        if not sources or kids:
            # a leaf's digest only restates its verbatim text; skip it to save tokens
            parts.append("DIGEST:\n" + node["text"])
        if kids:
            parts.append("SUB-CLAUSES: " + ", ".join(f"§{k['section']} {k['title']}" for k in kids))
        return "\n\n".join(parts)

    @tool
    async def outline() -> str:
        """List every clause number and title in the document."""
        nodes = await get_db().nodes(doc_id, kind="section")
        return "\n".join(
            "  " * max(n["meta"].get("depth", 1) - 1, 0) + f"§{n['section']} {n['title']}"
            for n in nodes if n["section"]
        )

    tools: list[BaseTool] = [search_tariff, read_section, outline]

    if with_calculator:
        @tool
        def calculator(expression: str) -> str:
            """Evaluate an arithmetic expression exactly. Supports + - * / ( ), min, max, round,
            ceil, floor, units(x, size) = ceil(x/size) for 'per <size> or part thereof', and
            above(x, threshold) = max(0, x - threshold). Example: units(51300, 100) * 117.08
            """
            try:
                value = Formula(expression).evaluate({}, {})
            except (RuleError, MissingInput, ZeroDivisionError, TypeError) as exc:
                return f"error: {exc}"
            return f"{value:,.4f}".rstrip("0").rstrip(".")

        tools.append(calculator)
    return tools

