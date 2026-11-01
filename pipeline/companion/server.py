from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import load_config
from .learning_state import learning_state_payload, save_learning_state
from .loop_state import commit_evening, commit_morning, loop_payload, set_focus
from .memory import memory_snapshot
from .session import CompanionSession, run_startup_warmup
from .user_memory import companion_memory_payload, save_user_memory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = PROJECT_ROOT / "ui"
PUBLIC_DIR = UI_DIR / "public"

app = FastAPI(title="newCompanion", version="0.3.0")
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
    llm = cfg.get("llm") or {}
    key_set = bool(str(llm.get("api_key") or "").strip())
    return {
        "ok": True,
        "mode": cfg.get("mode", "mock"),
        "llm": {
            "backend": llm.get("backend"),
            "model": llm.get("model"),
            "base_url": llm.get("base_url"),
            "key_configured": key_set,
        },
        "tts": {"backend": (cfg.get("tts") or {}).get("backend")},
    }


@app.get("/api/memory")
async def api_memory() -> dict[str, Any]:
    return memory_snapshot()


class CompanionMemoryUpdate(BaseModel):
    content: str = Field(default="", description="Full markdown of user_memory.md")


@app.get("/api/companion-memory")
async def api_get_companion_memory() -> dict[str, Any]:
    return companion_memory_payload()


@app.put("/api/companion-memory")
async def api_put_companion_memory(body: CompanionMemoryUpdate) -> dict[str, Any]:
    saved = save_user_memory(body.content)
    payload = companion_memory_payload()
    payload["content"] = saved
    payload["ok"] = True
    return payload


class LearningStateUpdate(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/learning-state")
async def api_get_learning_state() -> dict[str, Any]:
    return learning_state_payload()


@app.put("/api/learning-state")
async def api_put_learning_state(body: LearningStateUpdate) -> dict[str, Any]:
    saved = save_learning_state(body.state or {})
    payload = learning_state_payload()
    payload["state"] = saved
    payload["ok"] = True
    return payload


class LoopMorning(BaseModel):
    action: str = ""
    identity: str = ""
    last_week: str = ""
    this_week: str = ""


class LoopEvening(BaseModel):
    has_evidence: bool = False
    evidence: str = ""
    this_week: str = ""


class LoopFocus(BaseModel):
    focused: bool = False


@app.get("/api/loop")
async def api_get_loop() -> dict[str, Any]:
    return loop_payload()


@app.post("/api/loop/morning")
async def api_loop_morning(body: LoopMorning) -> dict[str, Any]:
    try:
        saved = commit_morning(
            body.action,
            identity=body.identity,
            last_week=body.last_week,
            this_week=body.this_week,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    payload = loop_payload()
    payload["loop"] = saved
    payload["ok"] = True
    payload["learning_state"] = learning_state_payload()
    return payload


@app.post("/api/loop/evening")
async def api_loop_evening(body: LoopEvening) -> dict[str, Any]:
    try:
        saved = commit_evening(body.has_evidence, body.evidence, this_week=body.this_week)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    payload = loop_payload()
    payload["loop"] = saved
    payload["ok"] = True
    payload["learning_state"] = learning_state_payload()
    return payload


@app.post("/api/loop/focus")
async def api_loop_focus(body: LoopFocus) -> dict[str, Any]:
    saved = set_focus(body.focused)
    payload = loop_payload()
    payload["loop"] = saved
    payload["ok"] = True
    return payload


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(UI_DIR / "preview.html")


@app.get("/overlay")
async def overlay() -> FileResponse:
    return FileResponse(UI_DIR / "overlay.html")


@app.get("/phone")
async def phone() -> FileResponse:
    return FileResponse(UI_DIR / "phone.html")


async def _safe_send(
    websocket: WebSocket,
    payload: dict[str, Any],
    *,
    session: CompanionSession | None = None,
) -> bool:
    try:
        await websocket.send_json(payload)
        if (
            session is not None
            and payload.get("type") == "audio"
            and payload.get("data")
        ):
            session.note_ws_audio_sent()
        return True
    except Exception:
        return False


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = CompanionSession()
    if not await _safe_send(websocket, session.snapshot(), session=session):
        return
    await run_startup_warmup(session)
    if not await _safe_send(
        websocket, {"type": "memory", "memory": memory_snapshot()}, session=session
    ):
        return
    if not await _safe_send(
        websocket,
        {"type": "companion_memory", "companion_memory": companion_memory_payload()},
        session=session,
    ):
        return
    if not await _safe_send(
        websocket,
        {"type": "learning_state", "learning_state": learning_state_payload()},
        session=session,
    ):
        return
    if not await _safe_send(
        websocket,
        {"type": "loop", "loop": loop_payload()},
        session=session,
    ):
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _safe_send(
                    websocket, {"type": "error", "message": "invalid json"}, session=session
                )
                continue

            mtype = msg.get("type")

            if mtype == "ping":
                await _safe_send(websocket, {"type": "pong"}, session=session)
                continue

            if mtype == "memory":
                await _safe_send(
                    websocket, {"type": "memory", "memory": memory_snapshot()}, session=session
                )
                continue

            if mtype == "companion_memory":
                if "content" in msg:
                    save_user_memory(str(msg.get("content") or ""))
                await _safe_send(
                    websocket,
                    {"type": "companion_memory", "companion_memory": companion_memory_payload()},
                    session=session,
                )
                continue

            if mtype == "learning_state":
                if isinstance(msg.get("state"), dict):
                    save_learning_state(msg["state"])
                await _safe_send(
                    websocket,
                    {"type": "learning_state", "learning_state": learning_state_payload()},
                    session=session,
                )
                continue

            if mtype == "loop":
                op = str(msg.get("op") or "")
                try:
                    if op == "morning":
                        commit_morning(
                            str(msg.get("action") or ""),
                            identity=str(msg.get("identity") or ""),
                            last_week=str(msg.get("last_week") or ""),
                            this_week=str(msg.get("this_week") or ""),
                        )
                    elif op == "evening":
                        commit_evening(
                            bool(msg.get("has_evidence")),
                            str(msg.get("evidence") or ""),
                            this_week=str(msg.get("this_week") or ""),
                        )
                    elif op == "focus":
                        set_focus(bool(msg.get("focused")))
                except ValueError as exc:
                    await _safe_send(
                        websocket, {"type": "error", "message": str(exc)}, session=session
                    )
                await _safe_send(
                    websocket,
                    {"type": "loop", "loop": loop_payload()},
                    session=session,
                )
                await _safe_send(
                    websocket,
                    {"type": "learning_state", "learning_state": learning_state_payload()},
                    session=session,
                )
                continue

            if mtype == "listen":
                for event in await session.set_listening(bool(msg.get("on", True))):
                    if not await _safe_send(websocket, event, session=session):
                        return
                continue

            if mtype == "stt":
                await _safe_send(
                    websocket,
                    session.set_stt_backend(str(msg.get("backend", ""))),
                    session=session,
                )
                continue

            if mtype == "text":
                async for event in session.handle_user_text(str(msg.get("text", ""))):
                    if not await _safe_send(websocket, event, session=session):
                        return
                continue

            if mtype == "audio":
                async for event in session.handle_audio_chunk(str(msg.get("data", ""))):
                    if not await _safe_send(websocket, event, session=session):
                        return
                continue

            await _safe_send(
                websocket,
                {"type": "error", "message": f"unknown type: {mtype}"},
                session=session,
            )
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
