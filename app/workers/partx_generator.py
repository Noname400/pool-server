"""
app/workers/partx_generator.py — PartX number distribution via KeyDB leases.

X space: 0 .. 2^32-1 (4,294,967,295), inclusive.

pool:ready is refilled by a background loop (ready_queue_filler in background/tasks.py).
Request path only does lease_allocate from pool:ready — never generates ranges inline.
"""
import logging

from app.cache import keydb

logger = logging.getLogger("pool_server.partx")

MAX_X = 2**32 - 1


async def refill_ready_queue(batch: int = 5000) -> int:
    """Generate up to `batch` new X values and add them to pool:ready.

    Called by the background refill loop. Returns how many were actually added.
    """
    await keydb.ensure_step_above_start()
    start = await keydb.claim_range(batch)

    if start > MAX_X:
        await keydb.set_step(MAX_X + 1)
        return 0

    end = min(start + batch, MAX_X + 1)
    if end <= start:
        return 0

    xs = list(range(start, end))
    await keydb.supply_ready(xs)
    return len(xs)


async def is_space_exhausted() -> bool:
    step = await keydb.get_step()
    return step > MAX_X
