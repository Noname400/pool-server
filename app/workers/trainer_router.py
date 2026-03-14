"""
app/workers/trainer_router.py — Trainer-facing API (pool v2).

Direct HTTPS, implicit heartbeat, auto-registration.
SQLite writes throttled: machine info updated at most once per MACHINE_ALIVE_TTL.
"""
import hmac
import logging
import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.cache import keydb
from app.config import get_settings
from app.db.sqlite import (
    add_found_key,
    check_all_seeds_found,
    count_found_keys,
    get_db,
    get_machine_unfound_seeds,
    get_setting,
    get_test_next,
    get_test_status,
    init_machine_verify,
    is_machine_verified,
    is_test_mode,
    list_found_keys,
    list_machines,
    mark_machine_seed_found,
    mark_test_done,
    mark_test_found,
    set_machine_verified,
    upsert_machine,
)
from app.workers.partx_generator import next_batch

router = APIRouter(tags=["trainer"])
logger = logging.getLogger("pool_server.trainer")
settings = get_settings()

_machine_last_db_write: dict[str, float] = {}
_DB_WRITE_INTERVAL = 300
_MAX_CACHE_SIZE = 10000

_verified_cache: set[str] = set()
_test_mode_cache: bool | None = None
_test_mode_ts: float = 0
_TEST_MODE_CACHE_TTL = 5


def _check_token(request: Request) -> str:
    token = request.headers.get("Authorization", "").strip()
    if not token or not settings.TRAINER_AUTH_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    if not hmac.compare_digest(token, settings.TRAINER_AUTH_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    machine_id = request.headers.get("X-Machine-Id", "").strip()
    if not machine_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Machine-Id required")
    return machine_id


def _real_ip(request: Request) -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "unknown")
    ).strip()


async def _auto_register(request: Request, machine_id: str):
    """Touch KeyDB always; write SQLite only every _DB_WRITE_INTERVAL seconds."""
    await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)

    now = time.monotonic()
    last = _machine_last_db_write.get(machine_id, 0)
    if now - last < _DB_WRITE_INTERVAL:
        return

    hostname = request.headers.get("X-Hostname", "").strip() or machine_id
    ip = _real_ip(request)
    gpu_name = request.headers.get("X-GPU-Name", "").strip() or None
    gpu_count = int(request.headers.get("X-GPU-Count", "0") or 0)
    gpu_mem = int(request.headers.get("X-GPU-Mem", "0") or 0) or None
    version = request.headers.get("X-Version", "").strip() or None

    async with get_db() as db:
        await upsert_machine(db, machine_id, hostname, ip, gpu_name, gpu_count, gpu_mem, version)

    if len(_machine_last_db_write) > _MAX_CACHE_SIZE:
        cutoff = now - _DB_WRITE_INTERVAL * 2
        stale = [k for k, v in _machine_last_db_write.items() if v < cutoff]
        for k in stale:
            del _machine_last_db_write[k]

    _machine_last_db_write[machine_id] = now


def evict_machine_cache(machine_id: str) -> None:
    """Remove machine from in-memory caches (call on delete)."""
    _verified_cache.discard(machine_id)
    _machine_last_db_write.pop(machine_id, None)


async def _is_test_mode_cached() -> bool:
    global _test_mode_cache, _test_mode_ts
    now = time.monotonic()
    if _test_mode_cache is not None and now - _test_mode_ts < _TEST_MODE_CACHE_TTL:
        return _test_mode_cache
    async with get_db() as db:
        _test_mode_cache = await is_test_mode(db)
    _test_mode_ts = now
    return _test_mode_cache


def _is_verified_cached(machine_id: str) -> bool:
    return machine_id in _verified_cache


# ---------------------------------------------------------------------------
@router.get("/status")
async def trainer_status():
    return {"status": "ok", "version": "2.0.0"}


@router.post("/api/worker/heartbeat")
async def legacy_heartbeat(request: Request):
    """Trainer heartbeat — keep machine alive in KeyDB."""
    token = request.headers.get("Authorization", "").strip()
    machine_id = request.headers.get("X-Machine-Id", "").strip()
    if token and machine_id and settings.TRAINER_AUTH_TOKEN:
        if hmac.compare_digest(token, settings.TRAINER_AUTH_TOKEN):
            await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)
    return {"status": "ok", "commands": []}


# ---------------------------------------------------------------------------
@router.get("/get_number")
async def get_number(request: Request, count: int = 1):
    machine_id = _check_token(request)
    await _auto_register(request, machine_id)

    # count=0 means registration-only (run-script ping): heartbeat without allocating X values
    if count == 0:
        return {"numbers": [], "command": "ok"}

    count = max(1, min(count, 1000))

    # Fast path: verified machine, no test mode → 100% KeyDB, zero SQLite
    if _is_verified_cached(machine_id) and not await _is_test_mode_cached():
        cmd = await keydb.get_pending_command(machine_id)
        if cmd:
            return {"numbers": [], "command": cmd}
        numbers = await next_batch(count, machine_id)
        return {"numbers": numbers, "command": "work" if numbers else "done"}

    # Slow path: needs full DB checks
    # Commands always via KeyDB (single source of truth)
    cmd = await keydb.get_pending_command(machine_id)
    if cmd:
        return {"numbers": [], "command": cmd}
    async with get_db() as db:

        if await is_test_mode(db):
            numbers = await get_test_next(db, count, machine_id)
            if not numbers:
                st = await get_test_status(db)
                return {"numbers": [], "command": "stop" if st["complete"] else "wait"}
            return {"numbers": numbers, "command": "work"}

        if not await is_machine_verified(db, machine_id):
            seeds_csv = await get_setting(db, "test_seeds", "")
            seeds = [int(s.strip()) for s in seeds_csv.split(",") if s.strip().isdigit()] if seeds_csv.strip() else []
            if seeds:
                await init_machine_verify(db, machine_id, seeds)
                unfound = await get_machine_unfound_seeds(db, machine_id)
                if unfound:
                    return {"numbers": unfound, "command": "verify"}
            await set_machine_verified(db, machine_id)
            logger.info("Machine %s verified", machine_id)

        _verified_cache.add(machine_id)

    numbers = await next_batch(count, machine_id)
    return {"numbers": numbers, "command": "work" if numbers else "done"}


# ---------------------------------------------------------------------------
class MarkDoneRequest(BaseModel):
    num: int | None = None
    nums: list[int] | None = None


@router.post("/mark_done")
async def mark_done(request: Request, body: MarkDoneRequest):
    machine_id = _check_token(request)
    await _auto_register(request, machine_id)

    nums = body.nums or ([body.num] if body.num is not None else [])
    if not nums:
        return {"ok": True, "count": 0}

    # KeyDB: increment completed counter + remove active keys (batched)
    await keydb.incr_completed(len(nums))
    await keydb.mark_x_done_batch(nums)

    # SQLite only for test mode (rare)
    if await _is_test_mode_cached():
        async with get_db() as db:
            for n in nums:
                await mark_test_done(db, n)

    return {"ok": True, "success": True, "count": len(nums)}


# ---------------------------------------------------------------------------
class SetFoundRequest(BaseModel):
    x: str | int
    y: str | int


@router.post("/set_found")
@router.post("/overfitted")
async def set_found(request: Request, body: SetFoundRequest):
    machine_id = _check_token(request)
    await _auto_register(request, machine_id)

    y_str = str(body.y)[:8192]

    try:
        x_raw = body.x
        if isinstance(x_raw, int):
            x_int = x_raw
        elif x_raw.startswith("0x") or any(c in x_raw for c in "abcdefABCDEF"):
            x_int = int(x_raw, 16)
        else:
            x_int = int(x_raw)
    except (ValueError, AttributeError):
        x_int = 0

    if x_int < 0 or x_int > 2**32 - 1:
        raise HTTPException(status_code=400, detail="X value out of range")

    async with get_db() as db:
        await add_found_key(db, x_int, y_str, machine_id)
        if await is_test_mode(db):
            await mark_test_found(db, x_int, y_str)
        if not await is_machine_verified(db, machine_id):
            await mark_machine_seed_found(db, machine_id, x_int)
            if await check_all_seeds_found(db, machine_id):
                await set_machine_verified(db, machine_id)
                _verified_cache.add(machine_id)
                logger.info("Machine %s verified (all seeds found)", machine_id)

    logger.info("FOUND KEY: x=%s y=%s machine=%s", body.x, body.y, machine_id)
    return {"ok": True, "success": True}


# ---------------------------------------------------------------------------
@router.get("/stats")
async def trainer_stats(request: Request):
    _check_token(request)
    kdb = await keydb.get_pool_stats()
    async with get_db() as db:
        found = await count_found_keys(db)
    return {
        "step": kdb["step"],
        "start": kdb["start"],
        "completed": kdb["completed"],
        "found_keys": found,
        "active_numbers": kdb["active_numbers"],
        "machines_online": kdb["machines_online"],
    }


# ---------------------------------------------------------------------------
@router.get("/machines")
async def list_machines_endpoint(request: Request):
    _check_token(request)
    async with get_db() as db:
        machines = await list_machines(db)
    alive = await keydb.get_alive_machines()
    for m in machines:
        m["online"] = m["machine_id"] in alive
    return machines


# ---------------------------------------------------------------------------
@router.get("/found_keys")
async def list_found_keys_endpoint(request: Request, limit: int = 100):
    _check_token(request)
    limit = max(1, min(limit, 1000))
    async with get_db() as db:
        return await list_found_keys(db, limit)
