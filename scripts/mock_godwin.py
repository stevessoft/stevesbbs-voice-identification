"""
Local mock of Godwin's calls-by-date-range API and webhook receiver.

Use during dev to end-to-end test the sweep + pipeline without waiting on
Godwin's real service. Drop sample audio files in ./mock_data/audio/, list
them in ./mock_data/calls.json (a list of {call_id, audio_url, started_at})
where audio_url points to /audio/<filename> on this server.

    python -m scripts.mock_godwin
    # then in another shell:
    GODWIN_API_URL=http://localhost:9000 \
    WEBHOOK_URL=http://localhost:9000/webhook \
    python -m scripts.sweep
"""

import json
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s mock | %(message)s")
log = logging.getLogger("mock_godwin")

ROOT = Path(__file__).resolve().parent.parent / "mock_data"
ROOT.mkdir(exist_ok=True)
(ROOT / "audio").mkdir(exist_ok=True)
CALLS_FILE = ROOT / "calls.json"
WEBHOOK_LOG = ROOT / "webhook_log.jsonl"

app = FastAPI(title="mock-godwin")
app.mount("/audio", StaticFiles(directory=str(ROOT / "audio")), name="audio")


@app.get("/calls")
def list_calls(start: str | None = None, end: str | None = None) -> list[dict]:
    if not CALLS_FILE.exists():
        return []
    return json.loads(CALLS_FILE.read_text())


@app.post("/webhook")
async def receive_webhook(req: Request) -> JSONResponse:
    body = await req.body()
    payload = json.loads(body)
    log.info("Received webhook for %s: speakers=%s segments=%d",
             payload.get("call_id"), payload.get("speakers"), len(payload.get("segments", [])))
    with WEBHOOK_LOG.open("a") as f:
        f.write(json.dumps(payload) + "\n")
    return JSONResponse({"ok": True})


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "calls_file": str(CALLS_FILE), "log_file": str(WEBHOOK_LOG)}


if __name__ == "__main__":
    log.info("Mock Godwin server starting on :9000. Calls file: %s", CALLS_FILE)
    uvicorn.run(app, host="0.0.0.0", port=9000, log_level="info")
