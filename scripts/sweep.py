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


async def run(hours_back: int = 24) -> int:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours_back)
    client = GodwinClient()

    try:
        calls = await client.list_calls(start.isoformat(), end.isoformat())
    except Exception as e:
        log.error("Failed to list calls: %s", e)
        await client.aclose()
        return 1

    log.info("Sweep window %s → %s, %d calls", start.isoformat(), end.isoformat(), len(calls))

    processed = 0
    failed = 0
    for call in calls:
        call_id = call.get("call_id") or call.get("id")
        audio_url = call.get("audio_url") or call.get("recording_url")
        direction = call.get("direction", "inbound")
        if not (call_id and audio_url):
            log.warning("Skipping malformed call: %s", call)
            failed += 1
            continue
        try:
            await pipeline.process_call(call_id, audio_url, direction=direction)
            processed += 1
        except Exception as e:
            log.exception("Failed processing call %s: %s", call_id, e)
            failed += 1

    await client.aclose()
    log.info("Sweep complete: %d processed, %d failed", processed, failed)
    return 0 if failed == 0 else 2


def main() -> int:
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    return asyncio.run(run(hours_back=hours))


if __name__ == "__main__":
    sys.exit(main())
