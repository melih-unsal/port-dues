"""Markdown pages -> clause hierarchy.

Tariff books are numbered legal-style documents ("SECTION 4" > "4.1" > "4.1.1"), so the
numbering is a far more reliable signal of structure than font sizes or markdown heading
levels, which vary between documents and between vision/text pages. Content before the
first numbered clause (definitions, preamble) becomes its own top-level node.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from portdues.ingestion.pages import LogicalPage

_SECTION = re.compile(r"^(SECTION|PART|CHAPTER)\s+(\d{1,2}|[IVX]{1,5})\b[\s:.\-–—]*(.*)$", re.I)
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})(?:\.\s*|\s+)(\S.{1,139})$")
_TOC_LINE = re.compile(r"^(SECTION\s+\d+|\d{1,2}(\.\d{1,2})*\.?)\s+.{3,90}?\s(\d{1,3}(-\d{1,3})?)$", re.I)
_LEADER = re.compile(r"[.…]{4,}|_{4,}")


@dataclass
class Section:
    key: str
    title: str
    depth: int
    parent: str | None
    lines: list[str] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)

    @property
    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.lines)).strip()


def _clean(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^\*\*(.+)\*\*$", r"\1", line)
    return line.strip(" *")


def _mostly_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) >= 0.75


def _boilerplate(pages: list[LogicalPage]) -> set[str]:
    """Lines that repeat on a large share of pages are running headers/footers."""
    counts: Counter[str] = Counter()
    for p in pages:
        counts.update({_signature(l) for l in p.markdown.splitlines() if l.strip()})
    threshold = max(3, int(len(pages) * 0.3))
    return {sig for sig, n in counts.items() if n >= threshold and sig}


def _signature(line: str) -> str:
    return re.sub(r"[\d\s|*#—–-]+", " ", _clean(line)).strip().lower()


def _is_toc(page: LogicalPage) -> bool:
    lines = [_clean(l) for l in page.markdown.splitlines() if l.strip()]
    hits = sum(1 for l in lines if _TOC_LINE.match(l) or re.match(r"^contents$", l, re.I))
    return hits >= 5


def _heading(line: str, raw: str) -> tuple[str, str, bool] | None:
    """Return (key, title, is_section_header) when the line opens a clause."""
    if m := _SECTION.match(line):
        return m.group(2).upper(), m.group(3).strip(), True
    if (m := _NUMBERED.match(line)) and not _LEADER.search(line):
        title = m.group(2).strip()
        explicit = raw.lstrip().startswith("#")
        if (explicit or _mostly_upper(title)) and not re.search(r"\d[\d ,.]*$", title):
            return m.group(1), title, False
    return None


def build_sections(pages: list[LogicalPage], doc_title: str) -> tuple[list[Section], list[str]]:
    """Return sections in document order (the first one is the root) and boilerplate lines."""
    boiler = _boilerplate(pages)
    notes: list[str] = []
    root = Section(key="", title=doc_title, depth=0, parent=None)
    sections: dict[str, Section] = {"": root}
    order: list[Section] = [root]
    current: Section | None = None
    pending_title: Section | None = None

    for page in pages:
        if _is_toc(page):
            continue
        for raw in page.markdown.splitlines():
            line = _clean(raw)
            if not line:
                if current:
                    current.lines.append("")
                continue
            sig = _signature(line)
            if sig in boiler:
                if len(line) > 25 and line not in notes and not re.search(r"\d{2,}\s*$", line):
                    notes.append(line)
                continue

            if pending_title is not None:
                wraps = pending_title.depth == 1 or len(pending_title.title) >= 35
                if wraps and not _heading(line, raw) and _mostly_upper(line) and len(line) < 110:
                    pending_title.title = (pending_title.title + " " + line).strip()
                    if pending_title.depth > 1:
                        pending_title = None
                    continue
                pending_title = None

            head = _heading(line, raw)
            if head:
                key, title, is_section = head
                if key in sections:
                    current = sections[key]
                    current.pages.add(page.index)
                    continue
                depth = 1 if is_section else key.count(".") + 1
                parent = _parent_key(key, sections)
                node = Section(key=key, title=title, depth=depth, parent=parent, pages={page.index})
                sections[key] = node
                order.append(node)
                current = node
                pending_title = node
                continue

            if current is None:
                current = Section(key="0", title="Preamble and definitions", depth=1, parent="")
                sections["0"] = current
                order.append(current)
            current.lines.append(raw.rstrip())
            current.pages.add(page.index)

    return order, notes


def _parent_key(key: str, sections: dict[str, Section]) -> str:
    parts = key.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in sections:
            return candidate
    return ""


def chunk_text(text: str, size: int = 1800) -> list[str]:
    """Split on blank lines, keeping markdown tables intact where possible."""
    if len(text) <= size:
        return [text] if text.strip() else []
    blocks, chunks, buf = re.split(r"\n\s*\n", text), [], ""
    for block in blocks:
        if buf and len(buf) + len(block) > size:
            chunks.append(buf.strip())
            buf = ""
        buf += block + "\n\n"
    if buf.strip():
        chunks.append(buf.strip())
    return chunks
