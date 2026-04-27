"""
Client for Godwin's calls API (Cytracom wrapper) at stevesbbs.com.

Endpoint:
  GET {GODWIN_API_URL}?page=1&limit=100&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&scrollId=

Response shape:
  {
    "data": {
      "total": int,
      "search": {
        "scroll_id": str,
        "records": [ {
          "uuid": str,                  # → call_id
          "started_on_ts": int,         # epoch ms
          "started_on": str,            # ISO 8601 with tz offset
          "recording_url": str,         # S3 mp3 download URL
          "linked_id": str,
          "original_caller_id": str,    # `"NAME" <number>`
          "origin_state": str,
          "monitored": bool,
          "spam": bool,
          "legs": [ { caller: {type, number, name}, callee: {...}, ... }, ... ]
        }, ... ]
      }
    }
  }
"""

from datetime import date, timedelta

import httpx

from app.config import settings


def _direction_from_legs(legs: list[dict]) -> str:
    """
    Inbound: first leg's caller is external (someone outside calling in).
    Outbound: first leg's caller is an extension (a tech calling out).
    Default: inbound (matches greeting-skip default).
    """
    if not legs:
        return "inbound"
    caller_type = (legs[0].get("caller") or {}).get("type")
    return "outbound" if caller_type == "extension" else "inbound"


def _normalize_record(rec: dict) -> dict | None:
    """Extract the fields the pipeline needs. Returns None if unprocessable."""
    call_id = rec.get("uuid")
    recording_url = rec.get("recording_url")
    if not (call_id and recording_url):
        return None
    return {
        "call_id": call_id,
        "audio_url": recording_url,
        "started_on": rec.get("started_on"),       # ISO 8601 string from Cytracom
        "started_on_ts": rec.get("started_on_ts"),  # epoch ms from Cytracom
        "direction": _direction_from_legs(rec.get("legs") or []),
        "caller_id": rec.get("original_caller_id"),
        "spam": rec.get("spam", False),
        "monitored": rec.get("monitored", False),
    }


class GodwinClient:
    def __init__(self) -> None:
        if not settings.godwin_api_url:
            raise RuntimeError("GODWIN_API_URL not set")
        headers = {"Authorization": f"Bearer {settings.godwin_api_token}"} if settings.godwin_api_token else {}
        self._http = httpx.AsyncClient(base_url="", headers=headers, timeout=30)
        self._base = settings.godwin_api_url

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_calls(self, start: str, end: str) -> list[dict]:
        """
        Fetch all calls between start and end (ISO 8601 dates). Paginates via
        scroll_id until the API returns no more records. Returns a flat list
        of normalized call dicts ready for pipeline.process_call().
        """
        # Godwin's API expects YYYY-MM-DD; tolerate ISO 8601 by truncating.
        start_d = start.split("T", 1)[0]
        end_d = end.split("T", 1)[0]

        normalized: list[dict] = []
        scroll_id = ""
        for _ in range(50):  # safety bound, ~5000 calls
            params = {
                "page": 1,
                "limit": 100,
                "startDate": start_d,
                "endDate": end_d,
                "scrollId": scroll_id,
            }
            r = await self._http.get(self._base, params=params)
            r.raise_for_status()
            payload = r.json().get("data") or {}
            search = payload.get("search") or {}
            records = search.get("records") or []
            if not records:
                break
            for rec in records:
                norm = _normalize_record(rec)
                if norm:
                    normalized.append(norm)
            next_scroll = search.get("scroll_id") or ""
            if not next_scroll or next_scroll == scroll_id:
                break
            scroll_id = next_scroll
        return normalized

    async def list_calls_for_date(self, day: date) -> list[dict]:
        s = day.isoformat()
        return await self.list_calls(s, s)

    async def list_calls_yesterday(self) -> list[dict]:
        return await self.list_calls_for_date(date.today() - timedelta(days=1))

    async def download_audio(self, audio_url: str, dest_path: str) -> None:
        async with self._http.stream("GET", audio_url) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
