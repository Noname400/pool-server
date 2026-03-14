"""
app/cache/keydb.py — KeyDB (Redis-compatible) async client.

Handles: X number distribution (atomic INCR), machine liveness (TTL),
active number tracking (TTL-based stuck detection).
"""
import logging
from typing import Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger("pool_server.cache")

_pool: Optional[redis.Redis] = None


async def init_keydb() -> None:
    """Create the connection pool."""
    global _pool
    settings = get_settings()
    _pool = redis.from_url(
        settings.KEYDB_URL,
        decode_responses=True,
        max_connections=200,
        socket_timeout=10.0,
        socket_connect_timeout=5.0,
    )
    await _pool.ping()
    logger.info("KeyDB connected: %s", settings.KEYDB_URL)


async def close_keydb() -> None:
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None


def get_keydb() -> redis.Redis:
    if not _pool:
        raise RuntimeError("KeyDB not initialized — call init_keydb() first")
    return _pool


# ---------------------------------------------------------------------------
# PartX step counter
# ---------------------------------------------------------------------------
async def get_step() -> int:
    r = get_keydb()
    val = await r.get("partx:step")
    return int(val) if val else 0


async def set_step(value: int) -> None:
    r = get_keydb()
    await r.set("partx:step", value)


async def claim_range(count: int) -> int:
    """Atomically claim `count` numbers. Returns the start of the range."""
    r = get_keydb()
    end = await r.incrby("partx:step", count)
    return end - count


async def get_start() -> int:
    r = get_keydb()
    val = await r.get("partx:start")
    return int(val) if val else 0


async def set_start(value: int) -> None:
    r = get_keydb()
    await r.set("partx:start", value)


_ENSURE_STEP_LUA = """
local start = tonumber(redis.call('GET', KEYS[1]) or '0')
local step  = tonumber(redis.call('GET', KEYS[2]) or '0')
if step < start then
    redis.call('SET', KEYS[2], start)
    return start
end
return step
"""
_ensure_step_script = None


async def ensure_step_above_start() -> None:
    """Atomically: if step < start, set step = start (Lua script)."""
    global _ensure_step_script
    r = get_keydb()
    if _ensure_step_script is None:
        _ensure_step_script = r.register_script(_ENSURE_STEP_LUA)
    await _ensure_step_script(keys=["partx:start", "partx:step"])


# ---------------------------------------------------------------------------
# Machine liveness (TTL-based auto-expire)
# ---------------------------------------------------------------------------
async def touch_machine(machine_id: str, ttl: int = 60) -> None:
    """Mark machine as alive. Key auto-expires after `ttl` seconds."""
    r = get_keydb()
    await r.set(f"alive:{machine_id}", "1", ex=ttl)


async def is_machine_alive(machine_id: str) -> bool:
    r = get_keydb()
    return await r.exists(f"alive:{machine_id}") > 0


_alive_cache: set[str] = set()
_alive_cache_ts: float = 0
_ALIVE_CACHE_TTL = 3


async def get_alive_machines() -> set[str]:
    """Return set of all alive machine IDs (cached for 3s)."""
    global _alive_cache, _alive_cache_ts
    import time
    now = time.monotonic()
    if now - _alive_cache_ts < _ALIVE_CACHE_TTL and _alive_cache_ts > 0:
        return _alive_cache
    r = get_keydb()
    keys = []
    async for key in r.scan_iter("alive:*", count=1000):
        keys.append(key.removeprefix("alive:"))
    _alive_cache = set(keys)
    _alive_cache_ts = now
    return _alive_cache


# ---------------------------------------------------------------------------
# Active X tracking (TTL-based stuck detection)
# ---------------------------------------------------------------------------
async def mark_x_done(x_number: int) -> None:
    r = get_keydb()
    await r.delete(f"active:{x_number}")


async def mark_x_done_batch(numbers: list[int]) -> None:
    if not numbers:
        return
    r = get_keydb()
    pipe = r.pipeline()
    for n in numbers:
        pipe.delete(f"active:{n}")
    await pipe.execute()


async def get_active_count() -> int:
    r = get_keydb()
    count = 0
    async for _ in r.scan_iter("active:*", count=1000):
        count += 1
    return count


# ---------------------------------------------------------------------------
# Completed counter (atomic)
# ---------------------------------------------------------------------------
async def incr_completed(count: int = 1) -> int:
    r = get_keydb()
    return await r.incrby("completed:count", count)


async def get_completed_count() -> int:
    r = get_keydb()
    val = await r.get("completed:count")
    return int(val) if val else 0


async def set_completed_count(value: int) -> None:
    r = get_keydb()
    await r.set("completed:count", value)


# ---------------------------------------------------------------------------
# Pending commands (fast check from KeyDB, avoids SQLite read on hot path)
# ---------------------------------------------------------------------------
async def set_pending_command(machine_id: str, command: str) -> None:
    r = get_keydb()
    await r.set(f"cmd:{machine_id}", command, ex=3600)


async def get_pending_command(machine_id: str) -> str | None:
    """Atomic read-and-delete via GETDEL (Redis 6.2+ / KeyDB)."""
    r = get_keydb()
    return await r.getdel(f"cmd:{machine_id}")


# ---------------------------------------------------------------------------
# Leader election (so background tasks run in one worker only)
# ---------------------------------------------------------------------------
_LEADER_KEY = "pool:leader"
_LEADER_TTL = 30
_worker_id: str = ""


def _get_worker_id() -> str:
    global _worker_id
    if not _worker_id:
        import os
        _worker_id = f"w-{os.getpid()}"
    return _worker_id


async def try_become_leader() -> bool:
    r = get_keydb()
    wid = _get_worker_id()
    acquired = await r.set(_LEADER_KEY, wid, ex=_LEADER_TTL, nx=True)
    if acquired:
        return True
    current = await r.get(_LEADER_KEY)
    return current == wid


async def renew_leadership() -> bool:
    r = get_keydb()
    wid = _get_worker_id()
    current = await r.get(_LEADER_KEY)
    if current == wid:
        await r.expire(_LEADER_KEY, _LEADER_TTL)
        return True
    return False


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
async def get_pool_stats() -> dict:
    r = get_keydb()
    step = int(await r.get("partx:step") or 0)
    start = int(await r.get("partx:start") or 0)
    completed = int(await r.get("completed:count") or 0)
    alive = await get_alive_machines()
    active_count = await get_active_count()
    return {
        "step": step,
        "start": start,
        "completed": completed,
        "machines_online": len(alive),
        "active_numbers": active_count,
    }
