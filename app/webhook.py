import json
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


async def post_result(payload: dict, callback_url: str | None = None) -> int:
    """
    POST a result to the configured webhook URL.

    Auth: if WEBHOOK_SECRET is set, sent as `Authorization: Bearer <secret>`.
    Otherwise no auth header. Body is JSON.
    """
    url = callback_url or settings.webhook_url
    if not url:
        log.warning("No webhook URL configured; result not posted: %s", payload.get("call_id"))
        return 0

    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        headers["Authorization"] = f"Bearer {settings.webhook_secret}"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, content=body, headers=headers)
        log.info("Webhook %s → %d for call %s", url, r.status_code, payload.get("call_id"))
        return r.status_code
