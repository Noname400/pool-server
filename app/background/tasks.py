"""
app/background/tasks.py — Background loops (pool v3).

With multiple Uvicorn workers, only the leader (elected via KeyDB SETNX)
runs persist + telegram + requeue tasks.
"""
import asyncio
import logging
import traceback
from datetime import datetime, timezone

from app.cache import keydb
from app.config import get_settings
from app.db.sqlite import count_found_keys, get_db, list_machines, set_setting
from app.notifications.telegram import send_notification
from app.workers.partx_generator import MAX_X

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
