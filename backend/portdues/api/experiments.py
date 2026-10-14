import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from portdues.evals.runner import hub, load_cases, select_cases
from portdues.storage.db import get_db

router = APIRouter(tags=["experiments"])


class ExperimentRequest(BaseModel):
    document_id: str
    name: str = ""
    strategies: list[Literal["compiled"]] = Field(default=["compiled"], min_length=1)
    scorers: list[Literal["numeric", "judge", "laya"]] = ["numeric", "judge", "laya"]
    difficulty: list[Literal["easy", "medium", "hard"]] = []
    case_ids: list[str] = []
    limit: int | None = Field(default=None, ge=1)
    concurrency: int = Field(default=4, ge=1, le=16)


@router.get("/eval/cases")
async def cases() -> list[dict]:
    return load_cases()


@router.get("/experiments")
async def list_experiments() -> list[dict]:
    return await get_db().list_experiments()


@router.post("/experiments", status_code=201)
async def create_experiment(req: ExperimentRequest) -> dict:
    db = get_db()
    doc = await db.get_document(req.document_id)
    if doc is None or doc["status"] != "ready":
        raise HTTPException(409, "document is not ready")
    config = req.model_dump(exclude={"document_id", "name"})
    # remember which rulebook (compiler design, model, cost) these scores belong to
    config["rulebook"] = ((await db.get_rulebook(req.document_id)) or {}).get("compiled_with", {})
    if not select_cases(config):
        raise HTTPException(400, "no golden cases match the selection")
    name = req.name or f"{' vs '.join(req.strategies)} on {doc['name']}"
    exp = await db.create_experiment(req.document_id, name, config)
    hub.start(exp["id"])
    return exp


@router.get("/experiments/{exp_id}")
async def get_experiment(exp_id: str) -> dict:
    db = get_db()
    exp = await db.get_experiment(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    results = [r["body"] for r in await db.results(exp_id)]
    return {**exp, "running": hub.is_running(exp_id), "results": results}


@router.get("/experiments/{exp_id}/stream")
async def stream_experiment(exp_id: str) -> EventSourceResponse:
    if await get_db().get_experiment(exp_id) is None:
        raise HTTPException(404, "experiment not found")

    async def events():
        async for event in hub.subscribe(exp_id):
            yield {"data": json.dumps(event, default=str)}
        yield {"data": json.dumps({"type": "closed"})}

    return EventSourceResponse(events(), ping=15)


@router.delete("/experiments/{exp_id}", status_code=204)
async def delete_experiment(exp_id: str) -> None:
    if hub.is_running(exp_id):
        raise HTTPException(409, "experiment is still running")
    await get_db().delete_experiment(exp_id)
