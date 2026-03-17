"""
app/cache/keydb.py — KeyDB (Redis-compatible) async client.

Lease-based X distribution (pool v3):
  pool:ready      (ZSET score=x)    — X values available for allocation
  pool:inflight   (ZSET score=ts)   — X values under active leases (score = expire timestamp)
  pool:lease:{x}  (STRING)          — "machine_id:lease_id" with TTL safety-net
  pool:lease_seq  (counter)         — auto-increment lease ID

Invariants:
  - An X can only be in ONE of {ready, inflight} at any time.
  - mark_done without a valid lease_id NEVER increments completed:count.
  - requeue only moves items whose inflight score (expire_ts) <= now.
  - completed + |inflight| + |ready| accounts for all issued X (modulo requeue cycles).

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
    global _pool, _scripts
    settings = get_settings()
    _pool = redis.from_url(
        settings.KEYDB_URL,
        decode_responses=True,
        max_connections=200,
        socket_timeout=10.0,
        socket_connect_timeout=5.0,
    )
    # Script objects are bound to a specific client connection pool.
    # Drop cached script wrappers on reconnect/reinit.
    _scripts = {}
    await _pool.ping()
    await _pool.delete("pool:deny:machines")
    logger.info("KeyDB connected: %s", settings.KEYDB_URL)


async def close_keydb() -> None:
    global _pool, _scripts
    if _pool:
        await _pool.aclose()
        _pool = None
    _scripts = {}


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
# Machine liveness (TTL-based auto-expire + hysteresis grace period)
# ---------------------------------------------------------------------------
_ONLINE_GRACE_SEC = 60

async def touch_machine(machine_id: str, ttl: int = 180) -> None:
    r = get_keydb()
    await r.set(f"alive:{machine_id}", "1", ex=ttl)


async def is_machine_alive(machine_id: str) -> bool:
    r = get_keydb()
    return await r.exists(f"alive:{machine_id}") > 0


_alive_cache: set[str] = set()
_alive_cache_ts: float = 0
_ALIVE_CACHE_TTL = 3
_last_seen_alive: dict[str, float] = {}


async def get_alive_machines() -> set[str]:
    global _alive_cache, _alive_cache_ts
    now = time.monotonic()
    if now - _alive_cache_ts < _ALIVE_CACHE_TTL and _alive_cache_ts > 0:
        return _alive_cache
    r = get_keydb()
    keys: list[str] = []
    async for key in r.scan_iter("alive:*", count=1000):
        keys.append(key.removeprefix("alive:"))

    current = set(keys)
    for mid in current:
        _last_seen_alive[mid] = now

    grace_cutoff = now - _ONLINE_GRACE_SEC
    graced = {mid for mid, ts in _last_seen_alive.items() if ts >= grace_cutoff}
    result = current | graced

    stale = [mid for mid, ts in _last_seen_alive.items() if ts < grace_cutoff]
    for mid in stale:
        del _last_seen_alive[mid]

    _alive_cache = result
    _alive_cache_ts = now
    return _alive_cache


# ---------------------------------------------------------------------------
# Combined hot-path: touch + command check + lease allocate (single Lua)
# ---------------------------------------------------------------------------
_LUA_HOT_PATH = """
local machine_id = ARGV[1]
local alive_ttl = tonumber(ARGV[2])
local count = tonumber(ARGV[3])
local expire_ts = tonumber(ARGV[4])
local key_ttl = tonumber(ARGV[5])

-- 1. Touch alive
redis.call('SET', 'alive:' .. machine_id, '1', 'EX', alive_ttl)

-- 2. Check pending command (atomic getdel)
local cmd = redis.call('GETDEL', 'cmd:' .. machine_id)
if cmd then
    return {'CMD', cmd}
end

-- 3. Allocate leases
if count <= 0 then
    return {'OK'}
end

local items = redis.call('ZPOPMIN', KEYS[1], count)
local result = {'BATCH'}

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


async def hot_path_allocate(
    machine_id: str, alive_ttl: int, count: int, expire_ts: float, key_ttl: int
) -> tuple[str, list[dict] | str]:
    """Single round-trip: touch alive, check command, allocate leases.

    Returns:
        ("CMD", command_str)  — pending command found, no allocation
        ("OK", [])            — count=0, just heartbeat
        ("BATCH", items)      — items = [{"x": int, "lease_id": str}, ...]
    """
    script = await _get_script("hot_path", _LUA_HOT_PATH)
    result = await script(
        keys=["pool:ready", "pool:inflight", "pool:lease_seq"],
        args=[machine_id, alive_ttl, count, expire_ts, key_ttl],
    )
    tag = result[0]
    if tag == "CMD":
        return "CMD", result[1]
    if tag == "OK":
        return "OK", []
    items = []
    for i in range(1, len(result), 2):
        items.append({"x": int(result[i]), "lease_id": str(result[i + 1])})
    return "BATCH", items


# ---------------------------------------------------------------------------
# Lease-based X allocation (standalone, used by slow path / test mode)
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
# Completed counter — incremented atomically by lease_ack Lua script
# ---------------------------------------------------------------------------
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
# Stats helpers (cached to reduce Redis round-trips on dashboard polls)
# ---------------------------------------------------------------------------
_stats_cache: dict | None = None
_stats_cache_ts: float = 0
_STATS_CACHE_TTL = 2


async def get_pool_stats() -> dict:
    global _stats_cache, _stats_cache_ts
    now = time.monotonic()
    if _stats_cache and now - _stats_cache_ts < _STATS_CACHE_TTL:
        return _stats_cache

    r = get_keydb()
    pipe = r.pipeline()
    pipe.get("partx:step")
    pipe.get("partx:start")
    pipe.get("completed:count")
    pipe.zcard("pool:inflight")
    pipe.zcard("pool:ready")
    pipe.get("pool:stats:requeued")
    results = await pipe.execute()

    step = int(results[0] or 0)
    start = int(results[1] or 0)
    completed = int(results[2] or 0)
    inflight = int(results[3] or 0)
    ready = int(results[4] or 0)
    requeued_total = int(results[5] or 0)
    alive = await get_alive_machines()

    _stats_cache = {
        "step": step,
        "start": start,
        "completed": completed,
        "machines_online": len(alive),
        "inflight": inflight,
        "ready_queue": ready,
        "requeued_total": requeued_total,
        "ts": time.time(),
    }
    _stats_cache_ts = now
    return _stats_cache


async def is_keydb_healthy() -> bool:
    """Quick health probe for readiness checks."""
    try:
        r = get_keydb()
        return await r.ping()
    except Exception:
        return False
