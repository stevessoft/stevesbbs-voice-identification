"""
Daily sweep: pull new calls from Godwin's API for a date range, process each
one through the pipeline, and POST results to the webhook.

Designed to be triggered by Godwin's daily cron (or any external scheduler).
Reads GODWIN_API_URL + WEBHOOK_URL from env.
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from app import pipeline
from app.config import settings
from app.godwin_client import GodwinClient

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("sweep")


async def run(hours_back: int = 24, skip_spam: bool = True) -> int:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours_back)
    client = GodwinClient()

    try:
        calls = await client.list_calls(start.isoformat(), end.isoformat())
    except Exception as e:
        log.error("Failed to list calls: %s", e)
        await client.aclose()
        return 1

    log.info("Sweep window %s → %s, %d calls", start.date(), end.date(), len(calls))

    processed = 0
    failed = 0
    skipped = 0
    for call in calls:
        if skip_spam and call.get("spam"):
            skipped += 1
            continue
        call_id = call["call_id"]
        audio_url = call["audio_url"]
        direction = call.get("direction", "inbound")
        try:
            await pipeline.process_call(call_id, audio_url, direction=direction)
            processed += 1
        except Exception as e:
            log.exception("Failed processing call %s: %s", call_id, e)
            failed += 1

    await client.aclose()
    log.info("Sweep complete: %d processed, %d skipped (spam), %d failed", processed, skipped, failed)
    return 0 if failed == 0 else 2


def main() -> int:
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    return asyncio.run(run(hours_back=hours))


if __name__ == "__main__":
    sys.exit(main())
