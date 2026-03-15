"""
app/security/middleware.py — CORS, security headers, request logging.
"""
import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("pool_server")


def setup_cors(app: FastAPI) -> None:
    from app.config import get_settings
    _settings = get_settings()
    raw = getattr(_settings, "CORS_ORIGINS", "").strip()
    allowed_origins = [o.strip() for o in raw.split(",") if o.strip()] if raw else []
    if not allowed_origins:
        logger.warning("CORS_ORIGINS is empty — all cross-origin requests will be blocked. "
                       "Set CORS_ORIGINS in .env (e.g. https://bbdata.net)")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self' https://fonts.gstatic.com; "
            "object-src 'none'; base-uri 'self';"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start = time.time()
        response: Response = await call_next(request)
        elapsed = (time.time() - start) * 1000
        client_ip = (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or (request.client.host if request.client else "?")
        ).strip()
        logger.info(
            "[%s] %s %s %s -> %d (%.1fms)",
            request_id,
            client_ip,
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        response.headers["X-Request-ID"] = request_id
        return response


def setup_middleware(app: FastAPI) -> None:
    setup_cors(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
