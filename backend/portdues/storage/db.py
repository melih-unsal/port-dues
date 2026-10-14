"""PostgreSQL persistence for documents, the section tree, rulebooks, chats and experiments.

Vectors live in Qdrant; everything relational lives here. The tree is stored in both:
Postgres is the source of truth for structure (parent/child navigation), Qdrant holds the
embeddings for similarity search.
"""

import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    stats JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id TEXT,
    kind TEXT NOT NULL,
    level INTEGER NOT NULL,
    section TEXT,
    title TEXT,
    text TEXT NOT NULL,
    pages JSONB NOT NULL DEFAULT '[]',
    meta JSONB NOT NULL DEFAULT '{}',
    ord INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_nodes_doc_kind ON nodes (doc_id, kind);
CREATE INDEX IF NOT EXISTS ix_nodes_parent ON nodes (parent_id);
CREATE TABLE IF NOT EXISTS rulebooks (
    doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    body JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages (conversation_id, created_at);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    name TEXT,
    config JSONB NOT NULL,
    status TEXT NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS experiment_results (
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    body JSONB NOT NULL,
    PRIMARY KEY (experiment_id, case_id, strategy)
);
"""

_JSON_COLUMNS = {"stats", "pages", "meta", "body", "config", "summary"}


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _param(key: str, value: Any) -> Any:
    return Jsonb(value) if key in _JSON_COLUMNS else value


class Database:
    def __init__(self, dsn: str):
        self.pool = AsyncConnectionPool(dsn, min_size=1, max_size=10, open=False,
                                        kwargs={"row_factory": dict_row, "autocommit": True})

    async def open(self) -> None:
        await self.pool.open(wait=True)
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def close(self) -> None:
        await self.pool.close()

    async def _all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, args)
            return await cur.fetchall()

    async def _one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, args)
            return await cur.fetchone()

    async def _exec(self, sql: str, *args: Any) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(sql, args)

    # documents -----------------------------------------------------------------

    async def create_document(self, name: str, filename: str, sha256: str) -> dict[str, Any]:
        return await self._one(
            "INSERT INTO documents (id, name, filename, sha256, status, stage)"
            " VALUES (%s, %s, %s, %s, 'queued', 'queued') RETURNING *",
            new_id("doc_"), name, filename, sha256,
        )

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM documents WHERE id = %s", doc_id)

    async def find_document_by_hash(self, sha256: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM documents WHERE sha256 = %s", sha256)

    async def list_documents(self) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM documents ORDER BY created_at DESC")

    async def update_document(self, doc_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = %s" for k in fields)
        await self._exec(
            f"UPDATE documents SET {cols}, updated_at = now() WHERE id = %s",
            *(_param(k, v) for k, v in fields.items()), doc_id,
        )

    async def delete_document(self, doc_id: str) -> None:
        await self._exec("DELETE FROM documents WHERE id = %s", doc_id)

    async def mark_interrupted(self) -> None:
        await self._exec(
            "UPDATE documents SET status = 'failed', error = 'interrupted by restart'"
            " WHERE status IN ('queued', 'running')"
        )
        await self._exec("UPDATE experiments SET status = 'failed' WHERE status = 'running'")

    # tree ----------------------------------------------------------------------

    async def replace_nodes(self, doc_id: str, nodes: list[dict[str, Any]], kinds: list[str]) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("DELETE FROM nodes WHERE doc_id = %s AND kind = ANY(%s)", (doc_id, kinds))
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO nodes (id, doc_id, parent_id, kind, level, section, title, text, pages, meta, ord)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            n["id"], doc_id, n.get("parent_id"), n["kind"], n["level"],
                            n.get("section"), n.get("title"), n["text"],
                            Jsonb(n.get("pages", [])), Jsonb(n.get("meta", {})), n.get("ord", 0),
                        )
                        for n in nodes
                    ],
                )

    async def nodes(self, doc_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return await self._all(
                "SELECT * FROM nodes WHERE doc_id = %s AND kind = %s ORDER BY ord", doc_id, kind
            )
        return await self._all("SELECT * FROM nodes WHERE doc_id = %s ORDER BY ord", doc_id)

    async def node(self, node_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM nodes WHERE id = %s", node_id)

    async def children(self, parent_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return await self._all(
                "SELECT * FROM nodes WHERE parent_id = %s AND kind = %s ORDER BY ord", parent_id, kind
            )
        return await self._all("SELECT * FROM nodes WHERE parent_id = %s ORDER BY ord", parent_id)

    async def section_node(self, doc_id: str, section: str) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM nodes WHERE doc_id = %s AND kind = 'section' AND lower(section) = lower(%s)",
            doc_id, section,
        )

    # rulebook ------------------------------------------------------------------

    async def save_rulebook(self, doc_id: str, body: dict[str, Any]) -> None:
        await self._exec(
            "INSERT INTO rulebooks (doc_id, body) VALUES (%s, %s)"
            " ON CONFLICT (doc_id) DO UPDATE SET body = excluded.body, created_at = now()",
            doc_id, Jsonb(body),
        )

    async def get_rulebook(self, doc_id: str) -> dict[str, Any] | None:
        row = await self._one("SELECT body FROM rulebooks WHERE doc_id = %s", doc_id)
        return row["body"] if row else None

    # chat ----------------------------------------------------------------------

    async def create_conversation(self, doc_id: str, title: str) -> dict[str, Any]:
        return await self._one(
            "INSERT INTO conversations (id, doc_id, title) VALUES (%s, %s, %s) RETURNING *",
            new_id("conv_"), doc_id, title[:80],
        )

    async def get_conversation(self, conv_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM conversations WHERE id = %s", conv_id)

    async def list_conversations(self, doc_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM conversations WHERE doc_id = %s ORDER BY created_at DESC", doc_id
        )

    async def delete_conversation(self, conv_id: str) -> None:
        await self._exec("DELETE FROM conversations WHERE id = %s", conv_id)

    async def add_message(self, conv_id: str, role: str, content: str, meta: dict | None = None) -> str:
        msg_id = new_id("msg_")
        await self._exec(
            "INSERT INTO messages (id, conversation_id, role, content, meta) VALUES (%s, %s, %s, %s, %s)",
            msg_id, conv_id, role, content, Jsonb(meta or {}),
        )
        return msg_id

    async def messages(self, conv_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM messages WHERE conversation_id = %s ORDER BY created_at", conv_id
        )

    # experiments ---------------------------------------------------------------

    async def create_experiment(self, doc_id: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
        return await self._one(
            "INSERT INTO experiments (id, doc_id, name, config, status) VALUES (%s, %s, %s, %s, 'running')"
            " RETURNING *",
            new_id("exp_"), doc_id, name, Jsonb(config),
        )

    async def get_experiment(self, exp_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM experiments WHERE id = %s", exp_id)

    async def list_experiments(self) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM experiments ORDER BY created_at DESC")

    async def finish_experiment(self, exp_id: str, status: str, summary: dict[str, Any]) -> None:
        await self._exec(
            "UPDATE experiments SET status = %s, summary = %s, finished_at = now() WHERE id = %s",
            status, Jsonb(summary), exp_id,
        )

    async def save_result(self, exp_id: str, case_id: str, strategy: str, body: dict[str, Any]) -> None:
        await self._exec(
            "INSERT INTO experiment_results (experiment_id, case_id, strategy, body) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (experiment_id, case_id, strategy) DO UPDATE SET body = excluded.body",
            exp_id, case_id, strategy, Jsonb(body),
        )

    async def results(self, exp_id: str) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM experiment_results WHERE experiment_id = %s", exp_id)

    async def delete_experiment(self, exp_id: str) -> None:
        await self._exec("DELETE FROM experiments WHERE id = %s", exp_id)


_db: Database | None = None


async def init_db() -> Database:
    global _db
    if _db is None:
        from portdues.config import get_settings

        _db = Database(get_settings().database_url)
        await _db.open()
    return _db


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("database not initialised; call init_db() first")
    return _db
