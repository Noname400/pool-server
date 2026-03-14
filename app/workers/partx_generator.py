"""
app/workers/partx_generator.py — PartX number distribution via KeyDB leases.

Allocates X values with lease-based tracking:
  1. Top up pool:ready from partx:step if needed
  2. Atomically pop from ready, create leases, move to inflight
  3. Return [{x, lease_id}, ...] to the trainer

X space: 0 .. 2^32-1 (4,294,967,295), inclusive.
"""
import logging
import time

from app.cache import keydb
from app.config import get_settings

logger = logging.getLogger("pool_server.partx")

MAX_X = 2**32 - 1


async def next_batch(count: int, machine_id: str) -> list[dict]:
    """Allocate up to `count` X values with leases.

    Returns list of {"x": int, "lease_id": str}.
    Empty list when the full space is exhausted and nothing is queued.
    """
    settings = get_settings()

    available = await keydb.get_ready_count()
    if available < count:
        need = count - available
        await keydb.ensure_step_above_start()
        start = await keydb.claim_range(need)

        if start <= MAX_X:
            end = min(start + need, MAX_X + 1)
            if end > start:
                xs = list(range(start, end))
                await keydb.supply_ready(xs)
        else:
            await keydb.set_step(MAX_X + 1)

    now = time.time()
    ttl = settings.LEASE_TTL
    expire_ts = now + ttl
    key_ttl = ttl * 3

    return await keydb.lease_allocate(count, machine_id, expire_ts, key_ttl)
