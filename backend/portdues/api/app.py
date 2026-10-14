import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from portdues.api import chat, documents, experiments
from portdues.config import get_settings
from portdues.evals.scorers import laya_available
from portdues.agents.strategies import STRATEGIES
from portdues.storage.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    db = await init_db()
    await db.mark_interrupted()
    yield
    await db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Port Dues API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(documents.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(experiments.router, prefix="/api")

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/meta")
    async def meta() -> dict:
        return {
            "models": {"compiler": settings.compiler_model, "fast": settings.fast_model,
                       "judge": settings.judge_model},
            "embedding_model": settings.embedding_model,
            "strategies": [{"id": k, "label": v} for k, v in STRATEGIES.items()],
            "scorers": [
                {"id": "numeric", "label": "Numeric (±1%)", "available": True},
                {"id": "judge", "label": "LLM judge", "available": True},
                {"id": "laya", "label": "Laya", "available": laya_available()},
            ],
            "tracing": settings.tracing_enabled,
            "langfuse_url": settings.langfuse_public_url if settings.tracing_enabled else None,
            "prices": settings.model_prices,
        }

    return app


app = create_app()
