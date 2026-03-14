"""
app/auth/router.py — Login / Logout / Me (SQLite version).
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from jose import jwt
from pydantic import BaseModel

from app.auth.api_keys import verify_api_key
from app.auth.dependencies import JWT_ALGORITHM, get_current_user
from app.config import get_settings
from app.db.sqlite import get_api_keys_by_prefix, get_db, get_user_by_id

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()
_logger = logging.getLogger("pool_server.auth")


class LoginRequest(BaseModel):
    api_key: str


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "unknown")
    ).strip()


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    prefix = body.api_key[:4] if len(body.api_key) >= 4 else body.api_key
    async with get_db() as db:
        candidates = await get_api_keys_by_prefix(db, prefix)
        api_key = None
        for candidate in candidates:
            if verify_api_key(body.api_key, candidate["key_hash"]):
                api_key = candidate
                break

        if not api_key:
            _logger.warning("Failed login from %s", _client_ip(request))
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

        user = await get_user_by_id(db, api_key["user_id"])
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    token_data = {
        "sub": user["id"],
        "role": api_key["role"],
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    token = jwt.encode(token_data, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)

    response.set_cookie(
        "session", token,
        httponly=True, samesite="strict",
        max_age=86400 * 7,
        secure=not settings.DEBUG,
        path="/",
    )

    return {
        "ok": True,
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    }


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session", path="/", httponly=True, samesite="strict", secure=not settings.DEBUG)
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    user, role = await get_current_user(request)
    return {"id": user["id"], "username": user["username"], "role": role, "is_active": bool(user.get("is_active", 1))}
