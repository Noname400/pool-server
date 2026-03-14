"""
app/workers/partx_generator.py — PartX number distribution via KeyDB.

Hot path: KeyDB INCR (atomic, O(1), zero contention).
X space: 0 .. 2^32-1 (4,294,967,295).
"""
import logging

from app.cache import keydb
from app.config import get_settings

logger = logging.getLogger("pool_server.partx")
settings = get_settings()

MAX_X = 2**32 - 1


async def next_batch(count: int, machine_id: str) -> list[int]:
    await keydb.ensure_step_above_start()

    start = await keydb.claim_range(count)

    if start >= MAX_X:
        await keydb.set_step(MAX_X)
        return []

    end = min(start + count, MAX_X)
    numbers = list(range(start, end))

    if numbers:
        r = keydb.get_keydb()
        pipe = r.pipeline()
        ttl = settings.ACTIVE_X_TTL
        for n in numbers:
            pipe.set(f"active:{n}", machine_id, ex=ttl)
        await pipe.execute()

    return numbers
