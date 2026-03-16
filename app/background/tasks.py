"""
app/background/tasks.py — Background loops (pool v3).

With multiple Uvicorn workers, only the leader (elected via KeyDB SETNX)
runs persist + telegram + requeue + refill tasks.
"""
import asyncio
import logging
import traceback
from datetime import datetime, timezone

from app.cache import keydb
from app.config import get_settings
from app.db.sqlite import (
    cleanup_stats_history, count_found_keys, get_db, get_setting,
    insert_stats_snapshot, list_machines, set_setting,
)
from app.notifications.telegram import send_notification
from app.workers.partx_generator import MAX_X, is_space_exhausted, refill_ready_queue

logger = logging.getLogger("pool_server.background")
settings = get_settings()

PERSIST_INTERVAL = 300


async def persist_keydb_state() -> None:
    """Save KeyDB step + completed count to SQLite periodically."""
    while True:
        await asyncio.sleep(PERSIST_INTERVAL)
        try:
            is_leader = await keydb.try_become_leader()
            if not is_leader:
                continue
            await keydb.renew_leadership()

            step = await keydb.get_step()
            completed = await keydb.get_completed_count()
            async with get_db() as db:
                await set_setting(db, "partx_step_saved", str(step))
                await set_setting(db, "completed_count_saved", str(completed))
        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("persist_keydb_state error:\n%s", traceback.format_exc())


async def requeue_expired_leases() -> None:
    """Move expired inflight X values back to the ready queue."""
    interval = settings.REQUEUE_INTERVAL
    batch = settings.REQUEUE_BATCH
    while True:
        await asyncio.sleep(interval)
        try:
            is_leader = await keydb.try_become_leader()
            if not is_leader:
                continue
            await keydb.renew_leadership()

            total = 0
            while True:
                n = await keydb.lease_requeue(limit=batch)
                total += n
                if n < batch:
                    break

            if total > 0:
                logger.info("Requeued %d expired leases", total)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("requeue_expired_leases error:\n%s", traceback.format_exc())


async def ready_queue_filler() -> None:
    """Keep pool:ready above READY_LOW_WATERMARK by generating new X ranges.

    Only the leader runs this. Checks every REFILL_INTERVAL seconds.
    """
    interval = settings.REFILL_INTERVAL
    low_wm = settings.READY_LOW_WATERMARK
    target = settings.READY_TARGET
    batch = settings.REFILL_BATCH

    while True:
        await asyncio.sleep(interval)
        try:
            is_leader = await keydb.try_become_leader()
            if not is_leader:
                continue
            await keydb.renew_leadership()

            if await is_space_exhausted():
                continue

            ready = await keydb.get_ready_count()
            if ready >= low_wm:
                continue

            need = target - ready
            filled = 0
            while need > 0 and not await is_space_exhausted():
                chunk = min(need, batch)
                added = await refill_ready_queue(batch=chunk)
                filled += added
                need -= added
                if added < chunk:
                    break

            if filled > 0:
                logger.info("Refilled pool:ready with %d items (now ~%d)", filled, ready + filled)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("ready_queue_filler error:\n%s", traceback.format_exc())


STATS_COLLECT_INTERVAL = 60
STATS_CLEANUP_EVERY = 300
_stats_cleanup_counter = 0


async def stats_collector() -> None:
    """Record pool snapshots to SQLite every 60s when stats_debug is enabled."""
    global _stats_cleanup_counter
    while True:
        await asyncio.sleep(STATS_COLLECT_INTERVAL)
        try:
            is_leader = await keydb.try_become_leader()
            if not is_leader:
                continue
            await keydb.renew_leadership()

            async with get_db() as db:
                enabled = await get_setting(db, "stats_debug", "0")
            if enabled != "1":
                continue

            kdb = await keydb.get_pool_stats()
            async with get_db() as db:
                found = await count_found_keys(db)
                machines = await list_machines(db)
                alive = await keydb.get_alive_machines()

                await insert_stats_snapshot(
                    db,
                    completed=kdb["completed"],
                    inflight=kdb["inflight"],
                    ready_queue=kdb["ready_queue"],
                    requeued_total=kdb["requeued_total"],
                    found_keys=found,
                    machines_online=len(alive),
                    machines_total=len(machines),
                    step=kdb["step"],
                )

                _stats_cleanup_counter += 1
                if _stats_cleanup_counter >= STATS_CLEANUP_EVERY // STATS_COLLECT_INTERVAL:
                    deleted = await cleanup_stats_history(db, keep_hours=24)
                    if deleted > 0:
                        logger.info("Stats history cleanup: removed %d old records", deleted)
                    _stats_cleanup_counter = 0

        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("stats_collector error:\n%s", traceback.format_exc())


async def telegram_stats_loop() -> None:
    interval = settings.TELEGRAM_STATS_INTERVAL * 60
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            is_leader = await keydb.try_become_leader()
            if not is_leader:
                continue
            await keydb.renew_leadership()

            kdb = await keydb.get_pool_stats()
            async with get_db() as db:
                found = await count_found_keys(db)
                machines = await list_machines(db)

            alive = await keydb.get_alive_machines()
            step = kdb["step"]
            completed = kdb["completed"]
            progress = (step / MAX_X * 100) if MAX_X > 0 else 0
            remaining = max(0, MAX_X - step)

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            msg = (
                f"Cluster stats\n\n"
                f"Completed: {completed:,}\n"
                f"Found keys: {found}\n"
                f"Inflight: {kdb['inflight']}\n"
                f"Ready queue: {kdb['ready_queue']}\n"
                f"Requeued total: {kdb['requeued_total']}\n\n"
                f"Progress: {progress:.4f}%\n"
                f"Step: {step:,}\n"
                f"Remaining: {remaining:,}\n\n"
                f"Machines: {len(machines)} (online: {len(alive)})\n"
                f"{now_str} UTC"
            )
            await send_notification(msg)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("telegram_stats_loop error:\n%s", traceback.format_exc())
