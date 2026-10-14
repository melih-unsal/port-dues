"""PDF -> logical pages of markdown.

Text-only pages are taken straight from the PDF text layer. Pages with tables or figures
are rendered and transcribed by the vision model, with the text layer passed along as a
hint so it can copy exact figures instead of reading them off pixels.

Tariff books are often printed as two-up spreads (two A4 portrait pages on a landscape
sheet), so landscape pages that look like spreads are split down the middle first.
"""

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from langchain_core.messages import HumanMessage, SystemMessage

from portdues.config import get_settings
from portdues.llm import chat_model, run_config

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"^\(?-?\d[\d,.]*%?\)?$")
_RENDER_ZOOM = 2.5

VISION_PROMPT = """You transcribe pages of tariff documents into GitHub-flavoured markdown.

Rules:
- Transcribe everything that is printed, in reading order. Do not summarise or omit.
- Keep clause numbers and headings exactly as printed and mark headings with #, e.g. "## 3.3 PILOTAGE SERVICES".
- Tables become markdown tables with one row per logical row. Never pack several rows into one cell with <br>.
  If a table has stacked rows such as a base fee followed by a "Plus per 100 tons" increment, make them separate
  rows with explicit labels, and keep the column headers (e.g. port names) on every table.
- Copy numbers exactly, including thousands separators as printed. Use the text layer below to verify digits.
- Describe pictures or charts in one line as: > Figure: ...
- Drop running headers/footers that only carry the book title or page number.
- Output only the markdown."""


@dataclass
class LogicalPage:
    index: int
    source_page: int
    clip: tuple[float, float, float, float]
    text: str
    needs_vision: bool
    markdown: str = ""
    via: str = "text"


def _is_spread(page: pymupdf.Page) -> bool:
    r = page.rect
    if r.width < r.height * 1.25:
        return False
    # A spread has little text crossing the vertical centre line.
    mid = r.width / 2
    crossing = sum(1 for b in page.get_text("blocks") if b[0] < mid - 20 and b[2] > mid + 20)
    return crossing <= 2


def _looks_tabular(page: pymupdf.Page, clip: pymupdf.Rect) -> bool:
    """True when several text rows carry three or more separate numeric cells."""
    rows: dict[int, list[tuple[float, float]]] = {}
    for x0, y0, x1, y1, word, *_ in page.get_text("words", clip=clip):
        if _NUMBER.match(word.replace(" ", "")):
            rows.setdefault(round((y0 + y1) / 4), []).append((x0, x1))
    evidence = 0.0
    for spans in rows.values():
        spans.sort()
        cells, last_x1 = 0, -1e9
        for x0, x1 in spans:
            if x0 - last_x1 > 12:  # thousands groups like "18 608.61" sit closer than this
                cells += 1
            last_x1 = x1
        # leader-dot lines ("Minimum fee ....... 235.52") only ever have one number
        evidence += 1.0 if cells >= 3 else 0.5 if cells == 2 else 0.0
    return evidence >= 2


def _has_figure(page: pymupdf.Page, clip: pymupdf.Rect) -> bool:
    area = clip.width * clip.height
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & clip
        if not bbox.is_empty and bbox.width * bbox.height > 0.05 * area:
            return True
    return False


def split_pages(pdf_path: Path, mode: str) -> list[LogicalPage]:
    doc = pymupdf.open(pdf_path)
    out: list[LogicalPage] = []
    for page in doc:
        r = page.rect
        clips = (
            [pymupdf.Rect(r.x0, r.y0, r.x0 + r.width / 2, r.y1), pymupdf.Rect(r.x0 + r.width / 2, r.y0, r.x1, r.y1)]
            if _is_spread(page)
            else [r]
        )
        for clip in clips:
            text = page.get_text("text", clip=clip, sort=True).strip()
            if mode == "all":
                vision = True
            elif mode == "none":
                vision = False
            else:
                vision = not text or _looks_tabular(page, clip) or _has_figure(page, clip)
            out.append(LogicalPage(len(out) + 1, page.number, tuple(clip), text, vision))
    doc.close()
    return out


def _render(pdf_path: Path, page: LogicalPage) -> bytes:
    with pymupdf.open(pdf_path) as doc:
        pix = doc[page.source_page].get_pixmap(
            matrix=pymupdf.Matrix(_RENDER_ZOOM, _RENDER_ZOOM), clip=pymupdf.Rect(page.clip)
        )
        return pix.tobytes("png")


async def _transcribe(pdf_path: Path, page: LogicalPage, doc_id: str) -> str:
    png = await asyncio.to_thread(_render, pdf_path, page)
    message = HumanMessage(content=[
        {"type": "text", "text": f"Text layer of this page (may be out of order):\n\n{page.text[:6000]}"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
    ])
    result = await chat_model().ainvoke(
        [SystemMessage(VISION_PROMPT), message],
        config=run_config("transcribe_page", session_id=doc_id, tags=["ingest"],
                          metadata={"page": page.index}),
    )
    content = result.content if isinstance(result.content, str) else result.text
    return re.sub(r"^```(?:markdown)?\s*|\s*```$", "", content.strip())


async def extract_pages(pdf_path: Path, doc_id: str, on_page=None) -> list[LogicalPage]:
    settings = get_settings()
    pages = await asyncio.to_thread(split_pages, pdf_path, settings.vision_pages)
    gate = asyncio.Semaphore(settings.llm_concurrency)
    done = 0

    async def work(page: LogicalPage) -> None:
        nonlocal done
        if page.needs_vision:
            async with gate:
                try:
                    page.markdown = await _transcribe(pdf_path, page, doc_id)
                    page.via = "vision"
                except Exception:
                    log.exception("vision transcription failed for page %s, using text layer", page.index)
                    page.markdown = page.text
        else:
            page.markdown = page.text
        done += 1
        if on_page:
            await on_page(done, len(pages))

    await asyncio.gather(*(work(p) for p in pages))
    return pages
