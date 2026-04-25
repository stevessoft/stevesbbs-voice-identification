"""
Cytracom API client. Used only as a fallback if Godwin's wrapper API is
unavailable. Prefer godwin_client.py for the daily sweep path.
"""

import httpx

from app.config import settings


class CytracomClient:
    def __init__(self) -> None:
        if not settings.cytracom_api_token:
            raise RuntimeError("CYTRACOM_API_TOKEN not set")
        self._http = httpx.AsyncClient(
            base_url=settings.cytracom_base_url,
            headers={"Authorization": f"Bearer {settings.cytracom_api_token}"},
            timeout=30,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_calls(self, start: str, end: str) -> list[dict]:
        # Cytracom Insights endpoint shape pending verification. Placeholder:
        r = await self._http.get("/insights/calls", params={"start": start, "end": end})
        r.raise_for_status()
        return r.json().get("data", [])

    async def download_recording(self, recording_url: str, dest_path: str) -> None:
        async with self._http.stream("GET", recording_url) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
