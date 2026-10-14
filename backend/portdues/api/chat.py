import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from portdues.agents.strategies import run_turn
from portdues.storage.db import get_db

router = APIRouter(tags=["chat"])

# events worth replaying when an old conversation is reopened
_TRACE_EVENTS = {"profile", "lines"}


class ChatRequest(BaseModel):
    document_id: str
    message: str = Field(min_length=1, max_length=8000)
    strategy: Literal["compiled"] = "compiled"
    conversation_id: str | None = None


@router.post("/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    db = get_db()
    doc = await db.get_document(req.document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    if doc["status"] != "ready":
        raise HTTPException(409, "document is not ready yet")

    if req.conversation_id:
        conv = await db.get_conversation(req.conversation_id)
        if conv is None or conv["doc_id"] != req.document_id:
            raise HTTPException(404, "conversation not found")
    else:
        conv = await db.create_conversation(req.document_id, req.message)

    previous = await db.messages(conv["id"])
    history = [{"role": m["role"], "content": m["content"]} for m in previous]
    last_profile = next((m["meta"].get("profile") for m in reversed(previous)
                         if m["role"] == "assistant" and m["meta"].get("profile")), None)
    await db.add_message(conv["id"], "user", req.message)

    async def stream():
        yield {"data": json.dumps({"type": "conversation", "id": conv["id"], "title": conv["title"]})}
        trace: list[dict] = []
        async for event in run_turn(req.document_id, req.message, strategy=req.strategy, history=history,
                                    previous_profile=last_profile, session_id=conv["id"]):
            if event["type"] in _TRACE_EVENTS:
                trace.append(event)
            if event["type"] == "result":
                msg_id = await db.add_message(conv["id"], "assistant", event["answer"], {
                    "strategy": event["strategy"], "lines": event["lines"], "profile": event.get("profile"),
                    "latency_s": event["latency_s"], "tokens": event["tokens"], "trace": trace,
                    "trace_url": event.get("trace_url"),
                })
                yield {"data": json.dumps({"type": "done", "message_id": msg_id, "latency_s": event["latency_s"],
                                           "tokens": event["tokens"]}, default=str)}
                continue
            if event["type"] == "error":
                await db.add_message(conv["id"], "assistant", f"Sorry, that failed: {event['message']}",
                                     {"error": True, "strategy": req.strategy, "trace": trace})
            yield {"data": json.dumps(event, default=str)}

    return EventSourceResponse(stream(), ping=15)


@router.get("/documents/{doc_id}/conversations")
async def conversations(doc_id: str) -> list[dict]:
    return await get_db().list_conversations(doc_id)


@router.get("/conversations/{conv_id}/messages")
async def conversation_messages(conv_id: str) -> list[dict]:
    if await get_db().get_conversation(conv_id) is None:
        raise HTTPException(404, "conversation not found")
    return await get_db().messages(conv_id)


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str) -> None:
    await get_db().delete_conversation(conv_id)
