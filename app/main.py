"""
app/main.py — FastAPI application (Pool v3: SQLite + KeyDB, lease-based).
"""
import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth.api_keys import generate_api_key
from app.auth.router import router as auth_router
from app.background.tasks import (
    persist_keydb_state,
    ready_queue_filler,
    requeue_expired_leases,
    stats_collector,
    telegram_stats_loop,
)
from app.cache import keydb
from app.cache.keydb import close_keydb, init_keydb, is_keydb_healthy
from app.config import get_settings
from app.dashboard.admin_router import router as admin_router
from app.db.sqlite import close_db_pool, create_api_key, create_user, get_db, get_setting, init_db, list_users
from app.export_router import router as export_router
from app.security.middleware import setup_middleware
from app.workers.trainer_router import router as trainer_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("pool_server")
settings = get_settings()

_background_tasks: list[asyncio.Task] = []


async def _restore_keydb_state():
    """Restore KeyDB step and completed count from SQLite if KeyDB was reset."""
    step = await keydb.get_step()
    completed = await keydb.get_completed_count()

    async with get_db() as db:
        saved_step = int(await get_setting(db, "partx_step_saved", "0"))
        saved_completed = int(await get_setting(db, "completed_count_saved", "0"))

    if step == 0 and saved_step > 0:
        await keydb.set_step(saved_step)
        logger.info("Restored KeyDB step from SQLite: %d", saved_step)

    if completed == 0 and saved_completed > 0:
        await keydb.set_completed_count(saved_completed)
        logger.info("Restored KeyDB completed count from SQLite: %d", saved_completed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _background_tasks

    await init_db()
    await init_keydb()
    await _restore_keydb_state()

    async with get_db() as db:
        users = await list_users(db)
        admins = [u for u in users if u["role"] == "admin"]
        if not admins:
            admin_id = str(uuid.uuid4())
            await create_user(db, admin_id, "admin", "admin")
            plaintext, key_hash = generate_api_key()
            await create_api_key(db, admin_id, key_hash, plaintext[:4], "initial-admin", "admin")
            logger.info("Created admin user. API key: %s (save now!)", plaintext)
        else:
            logger.info("Admin user exists: %s", admins[0]["username"])

    _background_tasks = [
        asyncio.create_task(persist_keydb_state()),
        asyncio.create_task(requeue_expired_leases()),
        asyncio.create_task(ready_queue_filler()),
        asyncio.create_task(stats_collector()),
        asyncio.create_task(telegram_stats_loop()),
    ]

    logger.info(
        "Pool v3 started (SQLite + KeyDB, lease-based). Dashboard: http://%s:%d",
        settings.HOST,
        settings.PORT,
    )
    yield

    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    await close_db_pool()
    await close_keydb()
    logger.info("Pool server shut down.")


app = FastAPI(
    title="GPU Pool API",
    version="3.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

setup_middleware(app)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled: %s %s — %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth_router)
app.include_router(trainer_router)
app.include_router(admin_router)
app.include_router(export_router)


# ---------------------------------------------------------------------------
# Deep health check (KeyDB + ready queue depth)
# ---------------------------------------------------------------------------
@app.get("/health")
async def deep_health():
    kdb_ok = await is_keydb_healthy()
    ready = await keydb.get_ready_count() if kdb_ok else 0
    inflight = await keydb.get_inflight_count() if kdb_ok else 0
    step = await keydb.get_step() if kdb_ok else 0
    healthy = kdb_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "keydb": "ok" if kdb_ok else "down",
            "ready_queue": ready,
            "inflight": inflight,
            "step": step,
        },
    )


# ---------------------------------------------------------------------------
# SPA serving (with path traversal protection)
# ---------------------------------------------------------------------------
_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
_DIST_RESOLVED = str(Path(_DIST).resolve()) + os.sep if os.path.isdir(_DIST) else ""

_DIST_ASSETS = os.path.join(_DIST_RESOLVED, "assets") if _DIST_RESOLVED else ""
if _DIST_ASSETS and os.path.isdir(_DIST_ASSETS):
    app.mount("/assets", StaticFiles(directory=_DIST_ASSETS), name="assets")


@app.get("/{full_path:path}")
async def spa_catch_all(full_path: str):
    if not _DIST_RESOLVED:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    if full_path:
        candidate = str(Path(os.path.join(_DIST_RESOLVED, full_path)).resolve())
        if candidate.startswith(_DIST_RESOLVED) and os.path.isfile(candidate):
            return FileResponse(candidate)

    index = os.path.join(_DIST_RESOLVED, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse({"detail": "Not found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
