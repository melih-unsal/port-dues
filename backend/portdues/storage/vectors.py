"""Qdrant index over every node of the tariff tree (sources, facts and digests)."""

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from portdues.config import get_settings
from portdues.llm import embeddings

_NAMESPACE = uuid.UUID("6f1c1e0e-9a59-4d0e-8c4c-0f1d7a1b2c3d")
_BATCH = 128


class VectorIndex:
    def __init__(self, url: str, collection: str, dim: int):
        self.client = AsyncQdrantClient(url=url, timeout=60)
        self.collection = collection
        self.dim = dim
        self._ready = False

    async def ensure_collection(self) -> None:
        if self._ready:
            return
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
            )
            for field in ("doc_id", "kind", "section"):
                await self.client.create_payload_index(
                    self.collection, field, models.PayloadSchemaType.KEYWORD
                )
            await self.client.create_payload_index(
                self.collection, "level", models.PayloadSchemaType.INTEGER
            )
        self._ready = True

    async def index_nodes(self, doc_id: str, nodes: list[dict[str, Any]]) -> None:
        await self.ensure_collection()
        emb = embeddings()
        for start in range(0, len(nodes), _BATCH):
            batch = nodes[start : start + _BATCH]
            vectors = await emb.aembed_documents([_embedding_text(n) for n in batch])
            await self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=str(uuid.uuid5(_NAMESPACE, n["id"])),
                        vector=vec,
                        payload={
                            "doc_id": doc_id,
                            "node_id": n["id"],
                            "kind": n["kind"],
                            "level": n["level"],
                            "section": n.get("section") or "",
                            "title": n.get("title") or "",
                            "pages": n.get("pages", []),
                            "text": n["text"],
                        },
                    )
                    for n, vec in zip(batch, vectors)
                ],
            )

    async def delete_document(self, doc_id: str, kinds: list[str] | None = None) -> None:
        await self.ensure_collection()
        must = [models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
        if kinds:
            must.append(models.FieldCondition(key="kind", match=models.MatchAny(any=kinds)))
        await self.client.delete(
            self.collection, points_selector=models.FilterSelector(filter=models.Filter(must=must))
        )

    async def search(
        self,
        doc_id: str,
        query: str,
        *,
        kinds: list[str] | None = None,
        section_prefix: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        await self.ensure_collection()
        vector = await embeddings().aembed_query(query)
        must = [models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
        if kinds:
            must.append(models.FieldCondition(key="kind", match=models.MatchAny(any=kinds)))
        res = await self.client.query_points(
            self.collection,
            query=vector,
            query_filter=models.Filter(must=must),
            limit=limit * 3 if section_prefix else limit,
            with_payload=True,
        )
        hits = [{**p.payload, "score": round(p.score, 4)} for p in res.points]
        if section_prefix:
            hits = [h for h in hits if h["section"] == section_prefix or h["section"].startswith(section_prefix + ".")]
        return hits[:limit]


def _embedding_text(node: dict[str, Any]) -> str:
    header = " ".join(x for x in (node.get("section"), node.get("title")) if x)
    return f"{header}\n{node['text']}" if header else node["text"]


_index: VectorIndex | None = None


def get_index() -> VectorIndex:
    global _index
    if _index is None:
        s = get_settings()
        _index = VectorIndex(s.qdrant_url, s.qdrant_collection, s.embedding_dim)
    return _index
