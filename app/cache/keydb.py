"""
app/cache/keydb.py — KeyDB (Redis-compatible) async client.

Lease-based X distribution (pool v3):
  pool:ready      (ZSET score=x)    — X values available for allocation
  pool:inflight   (ZSET score=ts)   — X values under active leases (score = expire timestamp)
  pool:lease:{x}  (STRING)          — "machine_id:lease_id" with TTL safety-net
  pool:lease_seq  (counter)         — auto-increment lease ID

Machine liveness, pending commands, leader election unchanged from v2.
"""
import logging
import time
from typing import Any, Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger("pool_server.cache")

_pool: Optional[redis.Redis] = None
_scripts: dict[str, Any] = {}


async def init_keydb() -> None:
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


async def ensure_step_above_start() -> None:
    """Atomically: if step < start, set step = start."""
    script = await _get_script("ensure_step", _ENSURE_STEP_LUA)
    await script(keys=["partx:start", "partx:step"])


# ---------------------------------------------------------------------------
# Machine liveness (TTL-based auto-expire)
# ---------------------------------------------------------------------------
async def touch_machine(machine_id: str, ttl: int = 60) -> None:
    r = get_keydb()
    await r.set(f"alive:{machine_id}", "1", ex=ttl)


async def is_machine_alive(machine_id: str) -> bool:
    r = get_keydb()
    return await r.exists(f"alive:{machine_id}") > 0


_alive_cache: set[str] = set()
_alive_cache_ts: float = 0
_ALIVE_CACHE_TTL = 3


async def get_alive_machines() -> set[str]:
    global _alive_cache, _alive_cache_ts
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
# Lease-based X allocation (replaces old active:* TTL tracking)
# ---------------------------------------------------------------------------

_LUA_ALLOCATE = """
local count = tonumber(ARGV[1])
local machine_id = ARGV[2]
local expire_ts = tonumber(ARGV[3])
local key_ttl = tonumber(ARGV[4])

local items = redis.call('ZPOPMIN', KEYS[1], count)
local result = {}

for i = 1, #items, 2 do
    local x = items[i]
    local lid = redis.call('INCR', KEYS[3])
    redis.call('ZADD', KEYS[2], expire_ts, x)
    redis.call('SET', 'pool:lease:' .. x, machine_id .. ':' .. lid, 'EX', key_ttl)
    result[#result + 1] = x
    result[#result + 1] = tostring(lid)
end

return result
"""

_LUA_ACK = """
local acked = 0
local rejected = 0
local already_done = 0

local i = 1
while i <= #ARGV do
    local x = ARGV[i]
    local lid = ARGV[i + 1]
    local mid = ARGV[i + 2]
    i = i + 3

    local lk = 'pool:lease:' .. x
    local stored = redis.call('GET', lk)

    if stored then
        if stored == mid .. ':' .. lid then
            redis.call('ZREM', KEYS[1], x)
            redis.call('DEL', lk)
            acked = acked + 1
        else
            rejected = rejected + 1
        end
    else
        if redis.call('ZSCORE', KEYS[1], x) then
            rejected = rejected + 1
        else
            already_done = already_done + 1
        end
    end
end

if acked > 0 then
    redis.call('INCRBY', KEYS[2], acked)
end

return {acked, rejected, already_done}
"""

_LUA_REQUEUE = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])

local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', tostring(now), 'LIMIT', 0, limit)
local requeued = 0

for _, x in ipairs(expired) do
    redis.call('ZREM', KEYS[1], x)
    redis.call('DEL', 'pool:lease:' .. x)
    redis.call('ZADD', KEYS[2], tonumber(x), x)
    requeued = requeued + 1
end

if requeued > 0 then
    redis.call('INCRBY', 'pool:stats:requeued', requeued)
end

return requeued
"""


async def _get_script(name: str, source: str):
    if name not in _scripts:
        r = get_keydb()
        _scripts[name] = r.register_script(source)
    return _scripts[name]


async def lease_allocate(count: int, machine_id: str, expire_ts: float, key_ttl: int) -> list[dict]:
    """Atomically pop from ready, create leases, move to inflight.

    Returns list of {"x": int, "lease_id": str}.
    """
    script = await _get_script("allocate", _LUA_ALLOCATE)
    result = await script(
        keys=["pool:ready", "pool:inflight", "pool:lease_seq"],
        args=[count, machine_id, expire_ts, key_ttl],
    )
    items = []
    for i in range(0, len(result), 2):
        items.append({"x": int(result[i]), "lease_id": str(result[i + 1])})
    return items


async def lease_ack(entries: list[tuple[int, str, str]]) -> tuple[int, int, int]:
    """Validate leases and acknowledge completed X values.

    entries: [(x, lease_id, machine_id), ...]
    Returns: (acked, rejected, already_done)
    """
    if not entries:
        return 0, 0, 0
    script = await _get_script("ack", _LUA_ACK)
    args = []
    for x, lid, mid in entries:
        args.extend([str(x), str(lid), mid])
    result = await script(
        keys=["pool:inflight", "completed:count"],
        args=args,
    )
    return int(result[0]), int(result[1]), int(result[2])


async def lease_ack_legacy(nums: list[int]) -> int:
    """Legacy ack without lease validation (backward compat with old trainers)."""
    if not nums:
        return 0
    r = get_keydb()
    pipe = r.pipeline()
    for n in nums:
        pipe.zrem("pool:inflight", str(n))
        pipe.delete(f"pool:lease:{n}")
    await pipe.execute()
    await r.incrby("completed:count", len(nums))
    return len(nums)


async def lease_requeue(limit: int = 500) -> int:
    """Move expired inflight items back to ready queue. Returns count requeued."""
    script = await _get_script("requeue", _LUA_REQUEUE)
    now = time.time()
    result = await script(
        keys=["pool:inflight", "pool:ready"],
        args=[now, limit],
    )
    return int(result)


async def supply_ready(xs: list[int]) -> None:
    """Add X values to the ready queue."""
    if not xs:
        return
    r = get_keydb()
    mapping = {str(x): float(x) for x in xs}
    await r.zadd("pool:ready", mapping)


async def get_ready_count() -> int:
    r = get_keydb()
    return await r.zcard("pool:ready")


async def get_inflight_count() -> int:
    r = get_keydb()
    return await r.zcard("pool:inflight")


# ---------------------------------------------------------------------------
# Completed counter (atomic) — also incremented by lease_ack Lua
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
# Pending commands
# ---------------------------------------------------------------------------
async def set_pending_command(machine_id: str, command: str) -> None:
    r = get_keydb()
    await r.set(f"cmd:{machine_id}", command, ex=3600)


async def get_pending_command(machine_id: str) -> str | None:
    r = get_keydb()
    return await r.getdel(f"cmd:{machine_id}")


# ---------------------------------------------------------------------------
# Leader election
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
    inflight = await get_inflight_count()
    ready = await get_ready_count()
    requeued_total = int(await r.get("pool:stats:requeued") or 0)
    return {
        "step": step,
        "start": start,
        "completed": completed,
        "machines_online": len(alive),
        "inflight": inflight,
        "ready_queue": ready,
        "requeued_total": requeued_total,
    }
