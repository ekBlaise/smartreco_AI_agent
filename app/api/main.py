"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.deps import RedirectToLogin, login_redirect
from app.api.middleware import AnonSessionMiddleware
from app.api.routes import admin_routes, auth_routes, events, pages, recos
from app.config import BASE_DIR, settings
from app.database import init_db
from app.llm.mesh import configure_tracing
from app.vector import store

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    configure_tracing()
    if not settings.mesh_configured:
        logger.warning(
            "MESH_API_KEY is not set — the catalog cannot be embedded and the agent "
            "cannot run. Browsing and event tracking still work."
        )
    try:
        store.ensure_collection()
    except Exception as exc:
        logger.warning("Qdrant not reachable at startup (%s); will retry on first use", exc)
    yield


app = FastAPI(
    title=f"{settings.app_name} — Behavioral AI Recommendation Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(AnonSessionMiddleware)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

app.include_router(pages.router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(events.router)
app.include_router(recos.router)


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin):
    return login_redirect(exc.next_url)


@app.get("/healthz", tags=["ops"])
def healthz():
    from app.cache import get_redis
    from app.ingest.buffer import buffer_depth

    return JSONResponse(
        {
            "ok": True,
            "mesh_configured": settings.mesh_configured,
            "chat_model": settings.mesh_chat_model,
            "embed_model": settings.mesh_embed_model,
            "redis": get_redis() is not None,
            "event_buffer_depth": buffer_depth(),
            "vector_store": store.health(),
            "tracing": settings.langsmith_tracing and bool(settings.langsmith_api_key),
        }
    )
