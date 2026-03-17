"""
app/workers/trainer_router.py — Trainer-facing API (pool v3, lease-based).

Hot path (get_number / mark_done) touches ONLY KeyDB via combined Lua scripts.
SQLite is hit only for rare events: new machine registration, set_found,
machine verification milestones, and test_mode flow.
"""
import asyncio
import hmac
import logging
import random
import time

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
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
_test_mode_lock: asyncio.Lock | None = None


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


def _safe_int_header(request: Request, name: str, default: int = 0) -> int:
    raw = request.headers.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


async def _auto_register(request: Request, machine_id: str, gpu_count_hint: int = 0):
    """Write SQLite only every _DB_WRITE_INTERVAL seconds.
    KeyDB alive touch is done inside the hot-path Lua script, not here.
    gpu_count_hint: inferred from get_number count param when header is missing.
    """
    now = time.monotonic()
    last = _machine_last_db_write.get(machine_id, 0)
    if now - last < _DB_WRITE_INTERVAL:
        return

    hostname = request.headers.get("X-Hostname", "").strip() or machine_id
    ip = _real_ip(request)
    gpu_name = request.headers.get("X-GPU-Name", "").strip() or None
    gpu_count = _safe_int_header(request, "X-GPU-Count", 0) or gpu_count_hint
    gpu_mem = _safe_int_header(request, "X-GPU-Mem", 0) or None
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
    global _test_mode_cache, _test_mode_ts, _test_mode_lock
    now = time.monotonic()
    if _test_mode_cache is not None and now - _test_mode_ts < _TEST_MODE_CACHE_TTL:
        return _test_mode_cache
    if _test_mode_lock is None:
        _test_mode_lock = asyncio.Lock()
    async with _test_mode_lock:
        if _test_mode_cache is not None and now - _test_mode_ts < _TEST_MODE_CACHE_TTL:
            return _test_mode_cache
        async with get_db() as db:
            _test_mode_cache = await is_test_mode(db)
        _test_mode_ts = time.monotonic()
        return _test_mode_cache


def _is_verified_cached(machine_id: str) -> bool:
    return machine_id in _verified_cache


def _empty_response(command: str) -> dict:
    return {"numbers": [], "leases": {}, "lease_ttl": settings.LEASE_TTL, "command": command}


def _batch_response(items: list[dict], command: str = "work") -> dict:
    numbers = [it["x"] for it in items]
    leases = {str(it["x"]): it["lease_id"] for it in items}
    return {
        "numbers": numbers,
        "leases": leases,
        "lease_ttl": settings.LEASE_TTL,
        "command": command,
    }


# ---------------------------------------------------------------------------
@router.get("/status")
async def trainer_status():
    return {"status": "ok", "version": "3.0.0"}


@router.post("/api/worker/heartbeat")
async def legacy_heartbeat(request: Request):
    token = request.headers.get("Authorization", "").strip()
    machine_id = request.headers.get("X-Machine-Id", "").strip()
    if token and machine_id and settings.TRAINER_AUTH_TOKEN:
        if hmac.compare_digest(token, settings.TRAINER_AUTH_TOKEN):
            await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)
            await _auto_register(request, machine_id)
    return {"status": "ok", "commands": []}


# ---------------------------------------------------------------------------
@router.get("/get_number")
async def get_number(request: Request, count: int = 1):
    machine_id = _check_token(request)

    count = max(0, min(count, 1000))

    if count == 0:
        await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)
        await _auto_register(request, machine_id, gpu_count_hint=0)
        return _empty_response("ok")

    # Fast path: verified machine, no test mode — single Lua round-trip
    if _is_verified_cached(machine_id) and not await _is_test_mode_cached():
        now = time.time()
        ttl = settings.LEASE_TTL
        tag, payload = await keydb.hot_path_allocate(
            machine_id=machine_id,
            alive_ttl=settings.MACHINE_ALIVE_TTL,
            count=count,
            expire_ts=now + ttl,
            key_ttl=ttl * 3,
        )
        await _auto_register(request, machine_id, gpu_count_hint=count)

        if tag == "CMD":
            return _empty_response(payload)
        if not payload:
            inflight = await keydb.get_inflight_count()
            return _empty_response("wait" if inflight > 0 else "done")
        return _batch_response(payload)

    # Slow path: DB checks for test mode / verification
    await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)
    await _auto_register(request, machine_id, gpu_count_hint=count)

    cmd = await keydb.get_pending_command(machine_id)
    if cmd:
        return _empty_response(cmd)

    async with get_db() as db:
        if await is_test_mode(db):
            numbers = await get_test_next(db, count, machine_id)
            if not numbers:
                st = await get_test_status(db)
                return _empty_response("stop" if st["complete"] else "wait")
            return {"numbers": numbers, "leases": {}, "lease_ttl": 0, "command": "work"}

        if not await is_machine_verified(db, machine_id):
            seeds_csv = await get_setting(db, "test_seeds", "")
            seeds = [int(s.strip()) for s in seeds_csv.split(",") if s.strip().isdigit()] if seeds_csv.strip() else []
            if seeds:
                await init_machine_verify(db, machine_id, seeds)
                unfound = await get_machine_unfound_seeds(db, machine_id)
                if unfound:
                    return {"numbers": unfound, "leases": {}, "lease_ttl": 0, "command": "verify"}
            await set_machine_verified(db, machine_id)
            logger.info("Machine %s verified", machine_id)

        _verified_cache.add(machine_id)

    now = time.time()
    ttl = settings.LEASE_TTL
    items = await keydb.lease_allocate(count, machine_id, now + ttl, ttl * 3)
    if not items:
        inflight = await keydb.get_inflight_count()
        return _empty_response("wait" if inflight > 0 else "done")
    return _batch_response(items)


# ---------------------------------------------------------------------------
class MarkDoneRequest(BaseModel):
    num: int | None = None
    nums: list[int] | None = None
    leases: dict[str, str] | None = None


@router.post("/mark_done")
async def mark_done(request: Request, body: MarkDoneRequest):
    machine_id = _check_token(request)
    await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)
    await _auto_register(request, machine_id)

    nums_raw = body.nums or ([body.num] if body.num is not None else [])
    nums = list(dict.fromkeys(nums_raw))
    if not nums:
        return {"ok": True, "count": 0}

    if not body.leases:
        if await _is_test_mode_cached():
            async with get_db() as db:
                for n in nums:
                    await mark_test_done(db, n)
            return {"ok": True, "success": True, "count": len(nums), "mode": "test"}
        raise HTTPException(status_code=400, detail="leases are required")

    missing = [n for n in nums if str(n) not in body.leases]
    if missing:
        raise HTTPException(status_code=400, detail="Missing lease ids for some numbers")

    entries = [(n, body.leases[str(n)], machine_id) for n in nums]
    acked, rejected, already_done = await keydb.lease_ack(entries)

    if rejected > 0:
        logger.warning("mark_done: %d rejected (invalid lease) from %s", rejected, machine_id)

    if acked > 0 and rejected == 0 and already_done == 0 and await _is_test_mode_cached():
        async with get_db() as db:
            for n in nums:
                await mark_test_done(db, n)

    return {"ok": True, "success": True, "count": acked, "rejected": rejected, "already_done": already_done}


# ---------------------------------------------------------------------------
# Маршруты для симулятора (trainer_gate). НЕ ТРОГАЮТ get_number/mark_done и пул.
# In-memory статистика — никуда не пишется, только для мониторинга.
# ---------------------------------------------------------------------------
_gate_stats = {
    "get_task": 0,
    "task_done": 0,
    "chunk": 0,
    "bytes_sent": 0,
    "first_seen": 0.0,
    "last_seen": 0.0,
    "machines": set(),
}
_gate_stats_lock = asyncio.Lock()


async def _gate_touch(endpoint: str, machine_id: str = "", extra_bytes: int = 0):
    async with _gate_stats_lock:
        now = time.time()
        _gate_stats[endpoint] += 1
        _gate_stats["bytes_sent"] += extra_bytes
        _gate_stats["last_seen"] = now
        if _gate_stats["first_seen"] == 0:
            _gate_stats["first_seen"] = now
        if machine_id:
            _gate_stats["machines"].add(machine_id)


def get_gate_stats() -> dict:
    s = _gate_stats.copy()
    s["machines"] = len(_gate_stats["machines"])
    now = time.time()
    uptime = now - s["first_seen"] if s["first_seen"] else 0
    s["uptime_sec"] = round(uptime, 1)
    s["rps"] = round((s["get_task"] + s["task_done"] + s["chunk"]) / uptime, 2) if uptime > 1 else 0
    return s


@router.get("/bbdata/get_task")
async def sim_get_task(request: Request, count: int = 1):
    """Симулятор: фейковые задачи. Не выделяет реальные X из пула."""
    machine_id = _check_token(request)
    count = max(1, min(count, 100))
    nums = [random.randint(1000000, 9999999) for _ in range(count)]
    leases = {str(n): f"sim-{n}" for n in nums}
    await _gate_touch("get_task", machine_id)
    return {"numbers": nums, "leases": leases, "lease_ttl": 60, "command": "work"}


@router.post("/bbdata/task_done")
async def sim_task_done(request: Request):
    """Симулятор: принимает body, но не трогает пул (нет KeyDB, нет SQLite)."""
    machine_id = _check_token(request)
    await request.body()
    await _gate_touch("task_done", machine_id)
    return {"ok": True, "count": 0}


@router.get("/bbdata/chunk")
async def sim_chunk(request: Request, min_bytes: int = 256, max_bytes: int = 8192):
    """Для симулятора: произвольные бинарные данные небольшого разного размера."""
    machine_id = _check_token(request)
    lo = max(64, min(min_bytes, max_bytes))
    hi = min(65536, max(min_bytes, max_bytes))
    size = random.randint(lo, hi) if lo <= hi else lo
    data = bytes(random.getrandbits(8) for _ in range(size))
    await _gate_touch("chunk", machine_id, size)
    return Response(content=data, media_type="application/octet-stream")


# ---------------------------------------------------------------------------
class SetFoundRequest(BaseModel):
    x: str | int
    y: str | int


@router.post("/set_found")
@router.post("/overfitted")
async def set_found(request: Request, body: SetFoundRequest):
    machine_id = _check_token(request)
    await keydb.touch_machine(machine_id, ttl=settings.MACHINE_ALIVE_TTL)
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
        raise HTTPException(status_code=400, detail="Invalid X value")

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
        "inflight": kdb["inflight"],
        "ready_queue": kdb["ready_queue"],
        "requeued_total": kdb["requeued_total"],
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
