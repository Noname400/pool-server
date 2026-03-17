"""
app/auth/dependencies.py — FastAPI auth dependencies (SQLite version).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from app.auth.api_keys import verify_api_key
from app.config import get_settings
from app.db.sqlite import get_api_keys_by_prefix, get_db, get_user_by_id, touch_api_key

settings = get_settings()
_logger = logging.getLogger("pool_server.auth")

JWT_ALGORITHM = "HS256"


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    cookie_token = request.cookies.get("session")
    if cookie_token:
        return cookie_token
    return None


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "unknown")
    ).strip()


def _parse_expires_at(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    # SQLite/Javascript formats may use trailing "Z", normalize for fromisoformat.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _resolve_api_key(token: str, request: Request) -> tuple[dict, str]:
    prefix = token[:4] if len(token) >= 4 else token
    async with get_db() as db:
        candidates = await get_api_keys_by_prefix(db, prefix)
        api_key = None
        for candidate in candidates:
            if verify_api_key(token, candidate["key_hash"]):
                api_key = candidate
                break

        if not api_key:
            _logger.warning("Failed API key auth from %s", _client_ip(request))
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

        if api_key.get("expires_at"):
            exp = _parse_expires_at(api_key["expires_at"])
            if exp is None:
                _logger.warning("Invalid expires_at for api key id=%s", api_key["id"])
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
            if exp < datetime.now(timezone.utc):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired")

        user = await get_user_by_id(db, api_key["user_id"])
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")

        await touch_api_key(db, api_key["id"], _client_ip(request))

    return user, api_key["role"]


async def _resolve_jwt(token: str) -> tuple[dict, str]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    user_id = payload.get("sub")
    role = payload.get("role", "user")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    async with get_db() as db:
        user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user, role


async def get_current_user(request: Request) -> tuple[dict, str]:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    if "." in token and token.count(".") == 2:
        return await _resolve_jwt(token)

    return await _resolve_api_key(token, request)


async def require_admin(request: Request) -> dict:
    user, role = await get_current_user(request)
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
