"""
app/export_router.py — Public export endpoint for found keys.

Designed to be hit directly (bypassing Cloudflare) from any IP.
Auth: EXPORT_TOKEN via query param or Authorization header.
If EXPORT_TOKEN is not set, falls back to TRAINER_AUTH_TOKEN.
"""
import hmac

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.config import get_settings
from app.db.sqlite import count_found_keys, get_db, list_found_keys

router = APIRouter(prefix="/export", tags=["export"])
settings = get_settings()


def _get_export_token() -> str:
    return settings.EXPORT_TOKEN or settings.TRAINER_AUTH_TOKEN


def _check_export_auth(request: Request, token: str | None = None):
    expected = _get_export_token()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Export not configured")

    provided = token or request.headers.get("Authorization", "").strip()
    if not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token required")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")


@router.get("/found_keys")
async def export_found_keys(
    request: Request,
    token: str | None = Query(default=None),
    limit: int = Query(default=10000, ge=1, le=100000),
    offset: int = Query(default=0, ge=0),
):
    """Return found keys as JSON. Supports pagination via limit/offset."""
    _check_export_auth(request, token)

    async with get_db() as db:
        total = await count_found_keys(db)
        cursor = await db.execute(
            "SELECT x_value, y_value, machine_id, found_at FROM found_keys ORDER BY found_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

    return {"total": total, "count": len(rows), "offset": offset, "keys": rows}
