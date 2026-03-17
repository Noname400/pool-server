"""
app/db/sqlite.py — Async SQLite via aiosqlite.

Single writer, WAL mode. All persistent CRUD goes through this module.
Hot-path data (step, completed count, liveness) lives in KeyDB.
Connection pool avoids per-request thread creation overhead.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from app.config import get_settings

logger = logging.getLogger("pool_server.db")

_db_path: str = ""
_pool: asyncio.Queue | None = None
_POOL_SIZE = 16
_shutting_down = False
_in_flight = 0
_in_flight_lock = asyncio.Lock()
_all_returned = asyncio.Event()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    email TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    key_hash TEXT NOT NULL,
    key_prefix TEXT,
    label TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT,
    last_used_ip TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS machines (
    machine_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    ip TEXT NOT NULL,
    name TEXT,
    tags TEXT DEFAULT '[]',
    gpu_name TEXT,
    gpu_count INTEGER DEFAULT 0,
    gpu_mem_mb INTEGER,
    version TEXT,
    pending_command TEXT,
    verified INTEGER DEFAULT 0,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_machines_hostname ON machines(hostname);
CREATE INDEX IF NOT EXISTS idx_machines_last_seen ON machines(last_seen);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);

CREATE TABLE IF NOT EXISTS machine_verify (
    machine_id TEXT NOT NULL REFERENCES machines(machine_id) ON DELETE CASCADE,
    x_value INTEGER NOT NULL,
    found INTEGER DEFAULT 0,
    found_at TEXT,
    PRIMARY KEY (machine_id, x_value)
);

CREATE TABLE IF NOT EXISTS found_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    x_value INTEGER NOT NULL,
    y_value TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    found_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_found_x ON found_keys(x_value);
CREATE INDEX IF NOT EXISTS idx_found_keys_found_at ON found_keys(found_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    x_value INTEGER NOT NULL UNIQUE,
    status TEXT DEFAULT 'pending',
    machine_id TEXT,
    assigned_at TEXT,
    completed_at TEXT,
    found INTEGER DEFAULT 0,
    found_y TEXT,
    found_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_test_status ON test_items(status);

CREATE TABLE IF NOT EXISTS stats_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    completed INTEGER NOT NULL DEFAULT 0,
    inflight INTEGER NOT NULL DEFAULT 0,
    ready_queue INTEGER NOT NULL DEFAULT 0,
    requeued_total INTEGER NOT NULL DEFAULT 0,
    found_keys INTEGER NOT NULL DEFAULT 0,
    machines_online INTEGER NOT NULL DEFAULT 0,
    machines_total INTEGER NOT NULL DEFAULT 0,
    step INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stats_history_ts ON stats_history(ts);
"""


async def init_db() -> None:
    settings = get_settings()
    global _db_path
    _db_path = settings.db_path

    os.makedirs(os.path.dirname(_db_path), exist_ok=True)

    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA)

        try:
            await db.execute("ALTER TABLE machines ADD COLUMN verified INTEGER DEFAULT 0")
        except Exception:
            pass

        await db.execute("DELETE FROM settings WHERE key = 'machine_denylist'")
        await db.commit()
    logger.info("SQLite initialized: %s (WAL mode)", _db_path)

    global _pool
    _pool = asyncio.Queue(maxsize=_POOL_SIZE)
    for _ in range(_POOL_SIZE):
        conn = await aiosqlite.connect(_db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await _pool.put(conn)
    logger.info("SQLite connection pool: %d connections", _POOL_SIZE)


async def close_db_pool() -> None:
    global _pool, _shutting_down
    if not _pool:
        return
    _shutting_down = True

    async with _in_flight_lock:
        if _in_flight > 0:
            _all_returned.clear()

    if _in_flight > 0:
        try:
            await asyncio.wait_for(_all_returned.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Shutdown: %d connections still in use after 10s", _in_flight)

    while not _pool.empty():
        conn = await _pool.get()
        try:
            await conn.close()
        except Exception:
            pass
    _pool = None


@asynccontextmanager
async def get_db():
    global _in_flight
    if _pool is None or _shutting_down:
        raise RuntimeError("SQLite pool not available")
    conn = await asyncio.wait_for(_pool.get(), timeout=30.0)
    async with _in_flight_lock:
        _in_flight += 1
    try:
        yield conn
    except Exception:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            await _pool.put(conn)
        except Exception:
            try:
                await conn.close()
            except Exception:
                pass
        async with _in_flight_lock:
            _in_flight -= 1
            if _in_flight <= 0 and _shutting_down:
                _all_returned.set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
async def get_user_by_id(db: aiosqlite.Connection, user_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_user(db: aiosqlite.Connection, user_id: str, username: str, role: str = "user") -> dict:
    await db.execute("INSERT INTO users (id, username, role) VALUES (?, ?, ?)", (user_id, username, role))
    await db.commit()
    return {"id": user_id, "username": username, "role": role}


async def list_users(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute("SELECT * FROM users ORDER BY created_at")
    return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
async def get_api_key_by_hash(db: aiosqlite.Connection, key_hash: str) -> dict | None:
    """Legacy lookup by exact hash (SHA256). Used internally."""
    cursor = await db.execute("SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1", (key_hash,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_api_keys_by_prefix(db: aiosqlite.Connection, prefix: str) -> list[dict]:
    """Find active API keys by prefix for bcrypt verification."""
    cursor = await db.execute("SELECT * FROM api_keys WHERE key_prefix = ? AND is_active = 1", (prefix,))
    return [dict(r) for r in await cursor.fetchall()]


async def create_api_key(db: aiosqlite.Connection, user_id: str, key_hash: str,
                          key_prefix: str, label: str, role: str) -> int:
    cursor = await db.execute(
        "INSERT INTO api_keys (user_id, key_hash, key_prefix, label, role) VALUES (?, ?, ?, ?, ?)",
        (user_id, key_hash, key_prefix, label, role),
    )
    await db.commit()
    return cursor.lastrowid


async def touch_api_key(db: aiosqlite.Connection, key_id: int, ip: str | None):
    await db.execute(
        "UPDATE api_keys SET last_used_at = ?, last_used_ip = ? WHERE id = ?",
        (_now(), ip, key_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------
async def upsert_machine(db: aiosqlite.Connection, machine_id: str, hostname: str,
                          ip: str, gpu_name: str | None = None, gpu_count: int = 0,
                          gpu_mem_mb: int | None = None, version: str | None = None) -> dict:
    now = _now()
    await db.execute("""
        INSERT INTO machines (machine_id, hostname, ip, gpu_name, gpu_count, gpu_mem_mb, version, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(machine_id) DO UPDATE SET
            hostname = excluded.hostname,
            ip = excluded.ip,
            gpu_name = COALESCE(excluded.gpu_name, machines.gpu_name),
            gpu_count = CASE WHEN excluded.gpu_count > 0 THEN excluded.gpu_count ELSE machines.gpu_count END,
            gpu_mem_mb = COALESCE(excluded.gpu_mem_mb, machines.gpu_mem_mb),
            version = COALESCE(excluded.version, machines.version),
            last_seen = excluded.last_seen
    """, (machine_id, hostname, ip, gpu_name, gpu_count, gpu_mem_mb, version, now, now))
    await db.commit()
    cursor = await db.execute("SELECT * FROM machines WHERE machine_id = ?", (machine_id,))
    row = await cursor.fetchone()
    return dict(row)


async def get_machine(db: aiosqlite.Connection, machine_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM machines WHERE machine_id = ?", (machine_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_machines(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute("SELECT * FROM machines ORDER BY last_seen DESC")
    return [dict(r) for r in await cursor.fetchall()]


async def update_machine(db: aiosqlite.Connection, machine_id: str, **fields) -> bool:
    if not fields:
        return False

    allowed = {"name", "tags", "pending_command"}
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return False

    sets = ", ".join(f"{k} = ?" for k in safe_fields)
    vals = list(safe_fields.values()) + [machine_id]
    cursor = await db.execute(f"UPDATE machines SET {sets} WHERE machine_id = ?", vals)
    await db.commit()
    return cursor.rowcount > 0


async def delete_machine(db: aiosqlite.Connection, machine_id: str) -> bool:
    await db.execute("DELETE FROM machine_verify WHERE machine_id = ?", (machine_id,))
    await db.execute("DELETE FROM found_keys WHERE machine_id = ?", (machine_id,))
    cursor = await db.execute("DELETE FROM machines WHERE machine_id = ?", (machine_id,))
    await db.commit()
    return cursor.rowcount > 0


async def set_pending_command(db: aiosqlite.Connection, machine_id: str, command: str | None):
    await db.execute("UPDATE machines SET pending_command = ? WHERE machine_id = ?", (command, machine_id))
    await db.commit()


async def consume_pending_command(db: aiosqlite.Connection, machine_id: str) -> str | None:
    cursor = await db.execute("SELECT pending_command FROM machines WHERE machine_id = ?", (machine_id,))
    row = await cursor.fetchone()
    if not row or not row["pending_command"]:
        return None
    cmd = row["pending_command"]
    await db.execute("UPDATE machines SET pending_command = NULL WHERE machine_id = ?", (machine_id,))
    await db.commit()
    return cmd


# ---------------------------------------------------------------------------
# Found Keys
# ---------------------------------------------------------------------------
async def add_found_key(db: aiosqlite.Connection, x_value: int, y_value: str, machine_id: str):
    await db.execute(
        "INSERT INTO found_keys (x_value, y_value, machine_id) VALUES (?, ?, ?)",
        (x_value, y_value, machine_id),
    )
    await db.commit()


async def list_found_keys(db: aiosqlite.Connection, limit: int = 100) -> list[dict]:
    cursor = await db.execute("SELECT * FROM found_keys ORDER BY found_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in await cursor.fetchall()]


async def count_found_keys(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT COUNT(*) FROM found_keys")
    row = await cursor.fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
async def get_setting(db: aiosqlite.Connection, key: str, default: str = "") -> str:
    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row and row["value"] else default


async def set_setting(db: aiosqlite.Connection, key: str, value: str):
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, _now()),
    )
    await db.commit()


async def get_all_settings(db: aiosqlite.Connection) -> dict[str, str]:
    cursor = await db.execute("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in await cursor.fetchall()}


# ---------------------------------------------------------------------------
# Test Items
# ---------------------------------------------------------------------------
async def insert_test_items(db: aiosqlite.Connection, x_values: list[int]):
    await db.execute("DELETE FROM test_items")
    for x in x_values:
        await db.execute("INSERT OR IGNORE INTO test_items (x_value, status) VALUES (?, 'pending')", (x,))
    await db.commit()


async def get_test_next(db: aiosqlite.Connection, count: int, machine_id: str) -> list[int]:
    now = _now()
    cursor = await db.execute(
        "UPDATE test_items SET status = 'active', machine_id = ?, assigned_at = ? "
        "WHERE id IN (SELECT id FROM test_items WHERE status = 'pending' LIMIT ?) "
        "RETURNING x_value",
        (machine_id, now, count),
    )
    rows = await cursor.fetchall()
    await db.commit()
    return [r[0] for r in rows]


async def get_test_status(db: aiosqlite.Connection) -> dict:
    cursor = await db.execute("SELECT * FROM test_items ORDER BY id")
    items = [dict(r) for r in await cursor.fetchall()]
    total = len(items)
    done = sum(1 for i in items if i["status"] == "done")
    found = sum(1 for i in items if i["found"])
    return {"items": items, "total": total, "done": done, "found": found, "complete": total > 0 and done == total}


async def mark_test_done(db: aiosqlite.Connection, x_value: int):
    await db.execute("UPDATE test_items SET status = 'done', completed_at = ? WHERE x_value = ?", (_now(), x_value))
    await db.commit()


async def mark_test_found(db: aiosqlite.Connection, x_value: int, y_value: str):
    await db.execute(
        "UPDATE test_items SET found = 1, found_y = ?, found_at = ? WHERE x_value = ?",
        (y_value, _now(), x_value),
    )
    await db.commit()


async def clear_test_items(db: aiosqlite.Connection):
    await db.execute("DELETE FROM test_items")
    await db.commit()


async def is_test_mode(db: aiosqlite.Connection) -> bool:
    return await get_setting(db, "test_mode", "0") == "1"


# ---------------------------------------------------------------------------
# Machine Verification
# ---------------------------------------------------------------------------
async def is_machine_verified(db: aiosqlite.Connection, machine_id: str) -> bool:
    cursor = await db.execute("SELECT verified FROM machines WHERE machine_id = ?", (machine_id,))
    row = await cursor.fetchone()
    return bool(row and row["verified"])


async def set_machine_verified(db: aiosqlite.Connection, machine_id: str):
    await db.execute("UPDATE machines SET verified = 1 WHERE machine_id = ?", (machine_id,))
    await db.commit()


async def init_machine_verify(db: aiosqlite.Connection, machine_id: str, seeds: list[int]):
    for x in seeds:
        await db.execute("INSERT OR IGNORE INTO machine_verify (machine_id, x_value) VALUES (?, ?)", (machine_id, x))
    await db.commit()


async def get_machine_unfound_seeds(db: aiosqlite.Connection, machine_id: str) -> list[int]:
    cursor = await db.execute("SELECT x_value FROM machine_verify WHERE machine_id = ? AND found = 0", (machine_id,))
    return [r["x_value"] for r in await cursor.fetchall()]


async def mark_machine_seed_found(db: aiosqlite.Connection, machine_id: str, x_value: int):
    await db.execute(
        "UPDATE machine_verify SET found = 1, found_at = ? WHERE machine_id = ? AND x_value = ?",
        (_now(), machine_id, x_value),
    )
    await db.commit()


async def check_all_seeds_found(db: aiosqlite.Connection, machine_id: str) -> bool:
    cursor = await db.execute("SELECT COUNT(*) FROM machine_verify WHERE machine_id = ? AND found = 0", (machine_id,))
    row = await cursor.fetchone()
    return row[0] == 0


# ---------------------------------------------------------------------------
# Stats History (debug snapshots)
# ---------------------------------------------------------------------------
async def insert_stats_snapshot(
    db: aiosqlite.Connection,
    completed: int, inflight: int, ready_queue: int,
    requeued_total: int, found_keys: int,
    machines_online: int, machines_total: int, step: int,
):
    await db.execute(
        "INSERT INTO stats_history (ts, completed, inflight, ready_queue, requeued_total, "
        "found_keys, machines_online, machines_total, step) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_now(), completed, inflight, ready_queue, requeued_total,
         found_keys, machines_online, machines_total, step),
    )
    await db.commit()


async def get_stats_history(db: aiosqlite.Connection, hours: int = 24, limit: int = 1500) -> list[dict]:
    cursor = await db.execute(
        "SELECT * FROM stats_history WHERE ts >= datetime('now', ?) ORDER BY ts ASC LIMIT ?",
        (f"-{hours} hours", limit),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def cleanup_stats_history(db: aiosqlite.Connection, keep_hours: int = 24) -> int:
    cursor = await db.execute(
        "DELETE FROM stats_history WHERE ts < datetime('now', ?)",
        (f"-{keep_hours} hours",),
    )
    await db.commit()
    return cursor.rowcount
