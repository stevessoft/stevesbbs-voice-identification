import hashlib
import hmac
import json
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_result(payload: dict, callback_url: str | None = None) -> int:
    url = callback_url or settings.webhook_url
    if not url:
        log.warning("No webhook URL configured; result not posted: %s", payload.get("call_id"))
        return 0

    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        headers["X-Signature-SHA256"] = _sign(body, settings.webhook_secret)

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, content=body, headers=headers)
        log.info("Webhook %s → %d for call %s", url, r.status_code, payload.get("call_id"))
        return r.status_code
