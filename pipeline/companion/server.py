from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .memory import memory_snapshot
from .session import CompanionSession, run_startup_warmup

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = PROJECT_ROOT / "ui"
PUBLIC_DIR = UI_DIR / "public"

app = FastAPI(title="newCompanion", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if PUBLIC_DIR.exists():
    app.mount("/public", StaticFiles(directory=str(PUBLIC_DIR)), name="public")


@app.get("/health")
async def health() -> dict[str, Any]:
    cfg = load_config()
    return {"ok": True, "mode": cfg.get("mode", "mock")}


@app.get("/api/memory")
async def api_memory() -> dict[str, Any]:
    return memory_snapshot()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(UI_DIR / "preview.html")


async def _safe_send(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        return False


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = CompanionSession()
    if not await _safe_send(websocket, session.snapshot()):
        return
    await run_startup_warmup(session)
    if not await _safe_send(websocket, {"type": "memory", "memory": memory_snapshot()}):
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _safe_send(websocket, {"type": "error", "message": "invalid json"})
                continue

            mtype = msg.get("type")

            if mtype == "ping":
                await _safe_send(websocket, {"type": "pong"})
                continue

            if mtype == "memory":
                await _safe_send(websocket, {"type": "memory", "memory": memory_snapshot()})
                continue

            if mtype == "listen":
                for event in await session.set_listening(bool(msg.get("on", True))):
                    if not await _safe_send(websocket, event):
                        return
                continue

            if mtype == "stt":
                await _safe_send(websocket, session.set_stt_backend(str(msg.get("backend", ""))))
                continue

            if mtype == "text":
                async for event in session.handle_user_text(str(msg.get("text", ""))):
                    if not await _safe_send(websocket, event):
                        return
                continue

            if mtype == "audio":
                async for event in session.handle_audio_chunk(str(msg.get("data", ""))):
                    if not await _safe_send(websocket, event):
                        return
                continue

            await _safe_send(websocket, {"type": "error", "message": f"unknown type: {mtype}"})
    except WebSocketDisconnect:
        return
    except Exception as exc:
        print(f"[ws] closed with error: {exc}", flush=True)
        return


def main() -> None:
    import uvicorn

    cfg = load_config()
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = int(cfg.get("server", {}).get("port", 8765))
    uvicorn.run("companion.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
