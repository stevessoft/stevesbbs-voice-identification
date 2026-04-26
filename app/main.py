import asyncio
import hmac
import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel

from app import enrollment, pipeline, speaker_id
from app.config import settings
from app.godwin_client import GodwinClient

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

app = FastAPI(title="stevesbbs-voice-identification", version="0.1.0")


def _require_admin(x_admin_secret: str | None) -> None:
    """Constant-time check of the admin shared secret. If admin_secret is
    unset (empty), admin endpoints are open for local dev."""
    if not settings.admin_secret:
        return
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret, settings.admin_secret):
        raise HTTPException(status_code=401, detail="invalid X-Admin-Secret")


class ProcessRequest(BaseModel):
    call_id: str
    audio_url: str
    callback_url: str | None = None
    direction: str = "inbound"  # "inbound" applies the greeting skip; "outbound" does not
    greeting_skip_seconds: float | None = None  # override env default; None = use env


class ReallocateRequest(BaseModel):
    tech_name: str
    audio_url: str


class ImportRequest(BaseModel):
    profiles: dict[str, list[float]]
    replace: bool = True  # if False, merge with existing


class SweepRequest(BaseModel):
    # Both optional. If both omitted, sweep yesterday (00:00 → 23:59 UTC).
    start_date: str | None = None  # YYYY-MM-DD (inclusive)
    end_date: str | None = None    # YYYY-MM-DD (inclusive)
    skip_spam: bool = True


# In-process job registry. Lightweight and per-container; resets on restart.
# Sufficient for the daily-cron use case where Godwin polls for completion
# or just relies on per-call webhooks streaming in.
_sweep_jobs: dict[str, dict] = {}


async def _run_sweep_job(job_id: str, start_date: str, end_date: str, skip_spam: bool) -> None:
    job = _sweep_jobs[job_id]
    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()

    client = GodwinClient()
    try:
        calls = await client.list_calls(start_date, end_date)
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"list_calls failed: {e}"
        await client.aclose()
        return

    job["total"] = len(calls)
    processed = skipped = failed = 0
    for call in calls:
        if skip_spam and call.get("spam"):
            skipped += 1
            job["skipped"] = skipped
            continue
        try:
            await pipeline.process_call(call["call_id"], call["audio_url"], direction=call.get("direction", "inbound"))
            processed += 1
            job["processed"] = processed
        except Exception:
            failed += 1
            job["failed"] = failed
    await client.aclose()
    job["status"] = "finished"
    job["finished_at"] = datetime.now(timezone.utc).isoformat()
    job["processed"] = processed
    job["skipped"] = skipped
    job["failed"] = failed


@app.get("/healthz")
def healthz() -> dict:
    profiles = speaker_id.load_profiles()
    return {
        "ok": True,
        "enrolled_speakers": list(profiles),
        "whisper_model": settings.whisper_model,
        "confidence_threshold": settings.confidence_threshold,
    }


@app.post("/process")
async def process(req: ProcessRequest) -> dict:
    return await pipeline.process_call(
        req.call_id,
        req.audio_url,
        callback_url=req.callback_url,
        direction=req.direction,
        greeting_skip_seconds=req.greeting_skip_seconds,
    )


@app.post("/reallocate")
async def reallocate(req: ReallocateRequest) -> dict:
    """
    Active learning hook. Godwin's UI calls this when a tech corrects a
    misattributed call. Folds the call's embedding into the corrected
    speaker's profile and re-averages. Audio deleted after.
    """
    import uuid

    import httpx

    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    local = settings.scratch_dir / f"{uuid.uuid4().hex}.audio"
    async with httpx.AsyncClient(timeout=60) as client, client.stream("GET", req.audio_url) as r:
        r.raise_for_status()
        with local.open("wb") as f:
            async for chunk in r.aiter_bytes(64 * 1024):
                f.write(chunk)

    try:
        enrollment.add_clip_to_profile(req.tech_name, local)
        speaker_id.reload_profiles()
    finally:
        try:
            local.unlink()
        except FileNotFoundError:
            pass

    return {"ok": True, "tech_name": req.tech_name, "enrolled_speakers": list(speaker_id.load_profiles())}


@app.post("/enroll/rebuild")
def enroll_rebuild(x_admin_secret: str | None = Header(default=None)) -> dict:
    """Re-scan enrollment_audio/ and rewrite the embeddings file."""
    _require_admin(x_admin_secret)
    profiles = enrollment.build_embeddings()
    speaker_id.reload_profiles()
    return {"ok": True, "enrolled_speakers": list(profiles)}


@app.post("/sweep")
async def sweep(req: SweepRequest, background: BackgroundTasks, x_admin_secret: str | None = Header(default=None)) -> dict:
    """
    Process every call in a date range from Godwin's calls API. Each
    successful call fires the webhook automatically (one per call).

    Runs in the background; this endpoint returns immediately with a
    job_id. Poll /sweep/status?job_id=... for progress, or just rely on
    the per-call webhook stream (Godwin's webhook receives a result
    payload for each processed call as it completes).

    Body:
        {
          "start_date": "YYYY-MM-DD",  // optional, default = yesterday UTC
          "end_date":   "YYYY-MM-DD",  // optional, default = yesterday UTC
          "skip_spam":  true            // optional, default true
        }

    Auth: X-Admin-Secret header.
    """
    _require_admin(x_admin_secret)

    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    start = req.start_date or yesterday
    end = req.end_date or yesterday

    job_id = f"sweep-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{len(_sweep_jobs):04d}"
    _sweep_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "window": {"start": start, "end": end, "skip_spam": req.skip_spam},
        "total": None,
        "processed": 0,
        "skipped": 0,
        "failed": 0,
    }
    background.add_task(_run_sweep_job, job_id, start, end, req.skip_spam)
    return _sweep_jobs[job_id]


@app.get("/sweep/status")
def sweep_status(job_id: str, x_admin_secret: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_secret)
    job = _sweep_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found (jobs reset on container restart)")
    return job


@app.post("/enroll/import")
def enroll_import(req: ImportRequest, x_admin_secret: str | None = Header(default=None)) -> dict:
    """
    Import pre-computed embeddings directly. Lets us load enrollment data
    onto a deployed instance without uploading raw audio. Each profile is
    a 256-dim float vector (Resemblyzer output).

    With replace=true (default), overwrites the embeddings file entirely.
    With replace=false, merges the imported profiles into the existing set.
    """
    _require_admin(x_admin_secret)

    # Validate vector shape
    for tech, vec in req.profiles.items():
        if not isinstance(vec, list) or not vec:
            raise HTTPException(status_code=422, detail=f"empty vector for {tech}")
        if len(vec) != 256:
            raise HTTPException(status_code=422, detail=f"{tech} vector len {len(vec)} != 256")

    settings.embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    if req.replace or not settings.embeddings_path.exists():
        merged = req.profiles
    else:
        existing = json.loads(settings.embeddings_path.read_text())
        merged = {**existing, **req.profiles}

    settings.embeddings_path.write_text(json.dumps(merged))
    speaker_id.reload_profiles()
    return {"ok": True, "enrolled_speakers": list(merged), "imported": list(req.profiles), "replace": req.replace}
