import hashlib
import re
from pathlib import Path
from typing import Any

from portdues.config import get_settings
from portdues.storage.db import get_db


def pdf_path(doc: dict[str, Any]) -> Path:
    return get_settings().uploads_dir / f"{doc['id']}.pdf"


async def register_pdf(data: bytes, filename: str, name: str | None = None) -> tuple[dict[str, Any], bool]:
    """Store an uploaded PDF. Returns (document, created); identical files are deduplicated."""
    if not data.startswith(b"%PDF"):
        raise ValueError("not a PDF file")
    db = get_db()
    digest = hashlib.sha256(data).hexdigest()
    existing = await db.find_document_by_hash(digest)
    if existing:
        return existing, False
    title = name or re.sub(r"[_\-]+", " ", Path(filename).stem).strip() or "Untitled tariff"
    doc = await db.create_document(title, filename, digest)
    pdf_path(doc).write_bytes(data)
    return doc, True
