"""
Client for Godwin's calls-by-date-range API. Endpoint shape pending — fill in
once Godwin shares the contract.
"""

import httpx

from app.config import settings


class GodwinClient:
    def __init__(self) -> None:
        if not settings.godwin_api_url:
            raise RuntimeError("GODWIN_API_URL not set")
        self._http = httpx.AsyncClient(
            base_url=settings.godwin_api_url,
            headers={"Authorization": f"Bearer {settings.godwin_api_token}"} if settings.godwin_api_token else {},
            timeout=30,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_calls(self, start: str, end: str) -> list[dict]:
        # Pending Godwin's contract. Expected shape per call:
        # {"call_id": str, "audio_url": str, "started_at": iso8601, ...}
        r = await self._http.get("/calls", params={"start": start, "end": end})
        r.raise_for_status()
        return r.json()

    async def download_audio(self, audio_url: str, dest_path: str) -> None:
        async with self._http.stream("GET", audio_url) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
