"""
app/notifications/telegram.py — Telegram notifications via Bot API.
"""
import logging

import httpx

from app.config import get_settings
from app.db.sqlite import get_db, get_setting

logger = logging.getLogger("pool_server.telegram")

_API = "https://api.telegram.org/bot{token}/{method}"


async def _get_credentials() -> tuple[str, str]:
    try:
        async with get_db() as db:
            token = await get_setting(db, "telegram_bot_token")
            chat_id = await get_setting(db, "telegram_chat_id")
    except Exception:
        token, chat_id = "", ""

    if not token or not chat_id:
        s = get_settings()
        token = token or (s.TELEGRAM_BOT_TOKEN or "").strip()
        chat_id = chat_id or (s.TELEGRAM_CHAT_ID or "").strip()

    return token, chat_id


async def send_message(text: str, parse_mode: str = "", disable_preview: bool = True, timeout: float = 10.0) -> bool:
    token, chat_id = await _get_credentials()
    if not token or not chat_id:
        return False
    url = _API.format(token=token, method="sendMessage")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode or None,
                "disable_web_page_preview": disable_preview,
            })
        data = r.json()
        if not data.get("ok"):
            logger.warning("Telegram API error: %s", data)
        return bool(data.get("ok"))
    except Exception as e:
        logger.error("Telegram send error: %s", e)
        return False


async def send_notification(text: str) -> bool:
    return await send_message(text)
