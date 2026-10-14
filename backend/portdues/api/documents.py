from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from portdues.documents import pdf_path, register_pdf
from portdues.ingestion.pipeline import is_running, start_ingestion
from portdues.storage.db import get_db
from portdues.storage.vectors import get_index

router = APIRouter(tags=["documents"])

MAX_UPLOAD = 50 * 1024 * 1024


async def _doc_or_404(doc_id: str) -> dict:
    doc = await get_db().get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@router.get("/documents")
async def list_documents() -> list[dict]:
    return await get_db().list_documents()


@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file larger than 50 MB")
    try:
        doc, created = await register_pdf(data, file.filename or "upload.pdf")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if created or doc["status"] == "failed":
        start_ingestion(doc["id"], pdf_path(doc))
    return {**doc, "duplicate": not created}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    return await _doc_or_404(doc_id)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str) -> None:
    doc = await _doc_or_404(doc_id)
    if is_running(doc_id):
        raise HTTPException(409, "document is being ingested")
    await get_index().delete_document(doc_id)
    await get_db().delete_document(doc_id)
    pdf_path(doc).unlink(missing_ok=True)


@router.post("/documents/{doc_id}/reingest", status_code=202)
async def reingest(doc_id: str, compile_only: bool = False) -> dict:
    doc = await _doc_or_404(doc_id)
    if is_running(doc_id):
        raise HTTPException(409, "already running")
    await get_db().update_document(doc_id, status="queued", stage="queued", progress=0.0, error=None)
    start_ingestion(doc_id, pdf_path(doc), compile_only=compile_only, fresh=not compile_only)
    return {"ok": True}


@router.post("/documents/{doc_id}/rules/{rule_id}/recompile", status_code=202)
async def recompile_rule(doc_id: str, rule_id: str) -> dict:
    doc = await _doc_or_404(doc_id)
    if is_running(doc_id):
        raise HTTPException(409, "already running")
    await get_db().update_document(doc_id, status="running", stage="queued", progress=0.75, error=None)
    start_ingestion(doc_id, pdf_path(doc), compile_only=True, rule_ids=[rule_id])
    return {"ok": True}


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: str) -> FileResponse:
    doc = await _doc_or_404(doc_id)
    return FileResponse(pdf_path(doc), media_type="application/pdf", filename=doc["filename"])


@router.get("/documents/{doc_id}/tree")
async def document_tree(doc_id: str) -> list[dict]:
    await _doc_or_404(doc_id)
    nodes = await get_db().nodes(doc_id, kind="section")
    return [
        {k: n[k] for k in ("id", "parent_id", "section", "title", "level", "pages")}
        | {"depth": n["meta"].get("depth", 0), "facts": n["meta"].get("facts", 0), "digest": n["text"]}
        for n in nodes
    ]


@router.get("/documents/{doc_id}/sections/{node_id}")
async def section_detail(doc_id: str, node_id: str) -> dict:
    db = get_db()
    node = await db.node(node_id)
    if node is None or node["doc_id"] != doc_id:
        raise HTTPException(404, "section not found")
    facts = await db.children(node_id, kind="fact")
    sources = await db.children(node_id, kind="source")
    return {
        "section": node["section"], "title": node["title"], "digest": node["text"], "pages": node["pages"],
        "facts": [{"text": f["text"], **f["meta"]} for f in facts],
        "source": "\n\n".join(s["text"] for s in sources),
    }


@router.get("/documents/{doc_id}/rulebook")
async def rulebook(doc_id: str) -> dict:
    await _doc_or_404(doc_id)
    body = await get_db().get_rulebook(doc_id)
    if body is None:
        raise HTTPException(404, "no rulebook yet")
    return body
