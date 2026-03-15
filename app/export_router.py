"""
app/export_router.py — Public export endpoint for found keys.

Designed to be hit directly (bypassing Cloudflare) from any IP.
Auth: EXPORT_TOKEN via Authorization header.
Falls back to TRAINER_AUTH_TOKEN only if EXPORT_TOKEN is not set (with a warning).
"""
import hmac
import logging

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.config import get_settings
from app.db.sqlite import count_found_keys, get_db

router = APIRouter(prefix="/export", tags=["export"])
settings = get_settings()
_logger = logging.getLogger("pool_server.export")

_warned_fallback = False


def _get_export_token() -> str:
    global _warned_fallback
    if settings.EXPORT_TOKEN:
        return settings.EXPORT_TOKEN
    if settings.TRAINER_AUTH_TOKEN:
        if not _warned_fallback:
            _logger.warning(
                "EXPORT_TOKEN not set — falling back to TRAINER_AUTH_TOKEN. "
                "Set a separate EXPORT_TOKEN in .env for production."
            )
            _warned_fallback = True
        return settings.TRAINER_AUTH_TOKEN
    return ""


def _check_export_auth(request: Request):
    expected = _get_export_token()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Export not configured")

    provided = request.headers.get("Authorization", "").strip()
    if not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token required (Authorization header)")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")


@router.get("/found_keys")
async def export_found_keys(
    request: Request,
    limit: int = Query(default=10000, ge=1, le=100000),
    offset: int = Query(default=0, ge=0),
):
    """Return found keys as JSON. Supports pagination via limit/offset."""
    _check_export_auth(request)

    async with get_db() as db:
        total = await count_found_keys(db)
        cursor = await db.execute(
            "SELECT x_value, y_value, machine_id, found_at FROM found_keys ORDER BY found_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

    return {"total": total, "count": len(rows), "offset": offset, "keys": rows}
