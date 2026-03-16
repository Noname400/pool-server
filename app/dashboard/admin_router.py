"""
app/dashboard/admin_router.py — Admin API (SQLite + KeyDB).
"""
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import require_admin
from app.cache import keydb
from app.config import get_settings
from app.db.sqlite import (
    clear_test_items,
    count_found_keys,
    delete_machine,
    get_all_settings,
    get_db,
    get_machine,
    get_setting,
    get_stats_history,
    get_test_status,
    insert_test_items,
    is_test_mode,
    list_found_keys,
    list_machines,
    list_users,
    set_setting,
    update_machine,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("pool_server.admin")
settings = get_settings()


# ---------------------------------------------------------------------------
# Stats (consistent snapshot with timestamp)
# ---------------------------------------------------------------------------
@router.get("/stats")
async def pool_stats(admin: dict = Depends(require_admin)):
    kdb = await keydb.get_pool_stats()
    async with get_db() as db:
        found = await count_found_keys(db)
        machines = await list_machines(db)

    alive = await keydb.get_alive_machines()

    return {
        "step": kdb["step"],
        "start": kdb["start"],
        "completed": kdb["completed"],
        "found_keys": found,
        "inflight": kdb["inflight"],
        "ready_queue": kdb["ready_queue"],
        "requeued_total": kdb["requeued_total"],
        "machines_total": len(machines),
        "machines_online": len(alive),
        "ts": kdb.get("ts", time.time()),
    }


# ---------------------------------------------------------------------------
# Stats History (debug analytics)
# ---------------------------------------------------------------------------
@router.get("/stats/history")
async def stats_history(hours: int = 24, admin: dict = Depends(require_admin)):
    hours = max(1, min(hours, 72))
    async with get_db() as db:
        enabled = await get_setting(db, "stats_debug", "0")
        rows = await get_stats_history(db, hours=hours) if enabled == "1" else []
    return {"enabled": enabled == "1", "hours": hours, "points": rows}


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------
@router.get("/machines")
async def admin_list_machines(admin: dict = Depends(require_admin)):
    async with get_db() as db:
        machines = await list_machines(db)
    alive = await keydb.get_alive_machines()
    for m in machines:
        m["online"] = m["machine_id"] in alive
    return machines


@router.get("/machines/{machine_id}")
async def admin_machine_detail(machine_id: str, admin: dict = Depends(require_admin)):
    async with get_db() as db:
        machine = await get_machine(db, machine_id)
        if not machine:
            raise HTTPException(status_code=404, detail="Machine not found")
    machine["online"] = await keydb.is_machine_alive(machine_id)
    return machine


class UpdateMachineRequest(BaseModel):
    name: str | None = None
    tags: list[str] | None = None


@router.patch("/machines/{machine_id}")
async def admin_update_machine(machine_id: str, body: UpdateMachineRequest,
                                admin: dict = Depends(require_admin)):
    fields = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.tags is not None:
        fields["tags"] = json.dumps(body.tags)

    if not fields:
        return {"ok": True}

    async with get_db() as db:
        await update_machine(db, machine_id, **fields)
    return {"ok": True}


@router.delete("/machines/{machine_id}")
async def admin_delete_machine(machine_id: str, admin: dict = Depends(require_admin)):
    async with get_db() as db:
        ok = await delete_machine(db, machine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Machine not found")
    from app.workers.trainer_router import evict_machine_cache
    evict_machine_cache(machine_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Machine commands (via pending_command, delivered on next get_number)
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    command: str


@router.post("/machines/{machine_id}/command")
async def admin_machine_command(machine_id: str, body: CommandRequest,
                                 admin: dict = Depends(require_admin)):
    if body.command not in ("stop", "pause", "restart"):
        raise HTTPException(status_code=400, detail="Unknown command. Allowed: stop, pause, restart")

    async with get_db() as db:
        machine = await get_machine(db, machine_id)
        if not machine:
            raise HTTPException(status_code=404, detail="Machine not found")
    await keydb.set_pending_command(machine_id, body.command)

    return {"ok": True, "message": f"Command '{body.command}' queued for next get_number"}


class CommandAllRequest(BaseModel):
    command: str
    only_online: bool = True
    tag: str | None = None


@router.post("/machines/command-all")
async def admin_command_all(body: CommandAllRequest, admin: dict = Depends(require_admin)):
    if body.command not in ("stop", "pause", "restart"):
        raise HTTPException(status_code=400, detail="Unknown command")

    async with get_db() as db:
        machines = await list_machines(db)

    alive = await keydb.get_alive_machines()
    target_ids: list[str] = []

    for m in machines:
        if body.only_online and m["machine_id"] not in alive:
            continue
        if body.tag:
            try:
                tags = json.loads(m.get("tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                tags = []
            if body.tag not in tags:
                continue
        target_ids.append(m["machine_id"])

    if target_ids:
        for mid in target_ids:
            await keydb.set_pending_command(mid, body.command)

    return {"ok": True, "affected": len(target_ids)}


# ---------------------------------------------------------------------------
# Found Keys
# ---------------------------------------------------------------------------
@router.get("/found-keys")
async def admin_found_keys(limit: int = 200, admin: dict = Depends(require_admin)):
    limit = max(1, min(limit, 1000))
    async with get_db() as db:
        return await list_found_keys(db, limit)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class UpdateSettingsRequest(BaseModel):
    settings: dict[str, str]


@router.get("/settings")
async def get_settings_endpoint(admin: dict = Depends(require_admin)):
    async with get_db() as db:
        db_settings = await get_all_settings(db)

    step = await keydb.get_step()
    start = await keydb.get_start()
    completed = await keydb.get_completed_count()

    db_settings["partx_step"] = str(step)
    db_settings["partx_start"] = str(start)
    db_settings["completed_count"] = str(completed)
    token = settings.TRAINER_AUTH_TOKEN
    db_settings["trainer_auth_token"] = f"{token[:6]}***" if len(token) > 6 else "***"
    db_settings["lease_ttl"] = str(settings.LEASE_TTL)

    return db_settings


@router.post("/settings")
async def update_settings_endpoint(body: UpdateSettingsRequest,
                                    admin: dict = Depends(require_admin)):
    ALLOWED_SETTINGS = {"test_seeds", "telegram_bot_token", "telegram_chat_id", "stats_debug"}
    db_settings = {}
    for key, value in body.settings.items():
        if key == "partx_start":
            v = int(value)
            if v < 0 or v > 2**32 - 1:
                raise HTTPException(status_code=400, detail="partx_start out of range")
            await keydb.set_start(v)
        elif key == "partx_step":
            v = int(value)
            if v < 0 or v > 2**32 - 1:
                raise HTTPException(status_code=400, detail="partx_step out of range")
            await keydb.set_step(v)
        elif key in ("trainer_auth_token", "completed_count"):
            pass
        elif key in ALLOWED_SETTINGS:
            db_settings[key] = value

    if db_settings:
        async with get_db() as db:
            for key, value in db_settings.items():
                await set_setting(db, key, value)

    await keydb.ensure_step_above_start()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Test Stand
# ---------------------------------------------------------------------------
class TestStartRequest(BaseModel):
    x_values: list[int]


@router.post("/test/start")
async def test_start(body: TestStartRequest, admin: dict = Depends(require_admin)):
    if not body.x_values:
        raise HTTPException(status_code=400, detail="No X values")
    if len(body.x_values) > 10000:
        raise HTTPException(status_code=400, detail="Too many X values (max 10000)")
    for x in body.x_values:
        if x < 0 or x > 2**32 - 1:
            raise HTTPException(status_code=400, detail=f"X value out of range: {x}")
    async with get_db() as db:
        await insert_test_items(db, body.x_values)
        await set_setting(db, "test_mode", "1")
    return {"ok": True, "count": len(body.x_values)}


@router.post("/test/stop")
async def test_stop(admin: dict = Depends(require_admin)):
    async with get_db() as db:
        await set_setting(db, "test_mode", "0")
        await clear_test_items(db)
    return {"ok": True}


@router.get("/test/status")
async def test_status_endpoint(admin: dict = Depends(require_admin)):
    async with get_db() as db:
        result = await get_test_status(db)
        result["test_mode"] = await is_test_mode(db)
        return result


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@router.get("/users")
async def admin_list_users(admin: dict = Depends(require_admin)):
    async with get_db() as db:
        return await list_users(db)


# ---------------------------------------------------------------------------
# Gate Monitor (in-memory, realtime)
# ---------------------------------------------------------------------------
@router.get("/gate-stats")
async def admin_gate_stats(admin: dict = Depends(require_admin)):
    from app.workers.trainer_router import get_gate_stats
    return get_gate_stats()
