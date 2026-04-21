"""
WS /v1/audio/transcribe — live streaming STT.

Wire format:
    Client -> Server: binary frames, raw PCM int16 LE, 16 kHz mono.
                      Optional JSON text frames for control:
                        {"type": "eof"}     # final flush, graceful close
                        {"type": "reset"}   # not implemented in this revision

    Server -> Client: JSON text frames:
                      {"start_ms": N, "end_ms": N, "text": "...", "is_final": bool,
                       "emission_time_ms": N}
                      On error: {"error": {"code": "...", "message": "..."}}

Auth: if OMNIVOICE_API_KEY is set, clients must send the bearer token via either
  - query param `?token=<key>`, OR
  - Sec-WebSocket-Protocol subprotocol `bearer.<key>`.
HTTP middleware doesn't run for WS upgrades, so we duplicate the check here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from ..services.stt import STTService, STTSession, TranscriptUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


# WebSocket close codes
_WS_CODE_NORMAL = 1000
_WS_CODE_GOING_AWAY = 1001
_WS_CODE_UNSUPPORTED = 1003
_WS_CODE_POLICY_VIOLATION = 1008
_WS_CODE_INTERNAL_ERROR = 1011
_WS_CODE_SERVICE_RESTART = 1012


def _extract_bearer(websocket: WebSocket) -> str | None:
    """Pull a bearer token from query `?token=` or Sec-WebSocket-Protocol `bearer.<key>`."""
    token = websocket.query_params.get("token")
    if token:
        return token
    proto_hdr = websocket.headers.get("sec-websocket-protocol", "")
    for raw in proto_hdr.split(","):
        part = raw.strip()
        if part.startswith("bearer."):
            return part[len("bearer.") :]
    return None


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    if websocket.application_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json({"error": {"code": code, "message": message}})
    except Exception:
        logger.debug("failed to send error frame", exc_info=True)


@router.websocket("/audio/transcribe")
async def transcribe_ws(websocket: WebSocket) -> None:
    app = websocket.app
    cfg = app.state.cfg

    if not getattr(cfg, "stt_enabled", False):
        await websocket.close(code=_WS_CODE_UNSUPPORTED, reason="STT disabled")
        return

    # Auth: duplicate the HTTP middleware check because middleware doesn't run on WS upgrade.
    if cfg.api_key:
        token = _extract_bearer(websocket)
        if token != cfg.api_key:
            await websocket.close(code=_WS_CODE_POLICY_VIOLATION, reason="auth required")
            return

    stt_svc: STTService | None = getattr(app.state, "stt_svc", None)
    if stt_svc is None:
        await websocket.close(code=_WS_CODE_SERVICE_RESTART, reason="STT not ready")
        return

    await websocket.accept()

    session: STTSession | None = None
    try:
        session = await stt_svc.acquire_session()
    except Exception:
        logger.exception("failed to acquire STT session")
        await _send_error(websocket, "session_unavailable", "Could not acquire STT session")
        await websocket.close(code=_WS_CODE_INTERNAL_ERROR)
        return

    stop_event = asyncio.Event()
    eof_event = asyncio.Event()

    reader = asyncio.create_task(_reader_loop(websocket, session, stop_event, eof_event))
    emitter = asyncio.create_task(
        _emitter_loop(websocket, session, cfg.stt_emit_interval_ms, stop_event, eof_event)
    )

    try:
        await asyncio.wait({reader, emitter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_event.set()
        for task in (reader, emitter):
            if not task.done():
                task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(reader, emitter, return_exceptions=True)

        if session is not None:
            try:
                if websocket.application_state == WebSocketState.CONNECTED:
                    final = await session.finish()
                    if final is not None:
                        await _safe_send_update(websocket, final)
            except Exception:
                logger.debug("final flush failed", exc_info=True)
            finally:
                stt_svc.release_session(session)

        if websocket.application_state != WebSocketState.DISCONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=_WS_CODE_NORMAL)


async def _reader_loop(
    websocket: WebSocket,
    session: STTSession,
    stop_event: asyncio.Event,
    eof_event: asyncio.Event,
) -> None:
    """Pump client frames into the session until disconnect or EOF."""
    try:
        while not stop_event.is_set():
            msg = await websocket.receive()

            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                return

            if "bytes" in msg and msg["bytes"] is not None:
                session.insert_audio_chunk(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                await _handle_control(msg["text"], websocket, session, eof_event)
                if eof_event.is_set():
                    return
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("reader loop failed")
        await _send_error(websocket, "reader_failed", "Input stream error")


async def _handle_control(
    raw: str,
    websocket: WebSocket,
    session: STTSession,
    eof_event: asyncio.Event,
) -> None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        await _send_error(websocket, "bad_control", "Control frame must be JSON")
        return
    if not isinstance(payload, dict):
        await _send_error(websocket, "bad_control", "Control payload must be an object")
        return
    ctype = payload.get("type")
    if ctype == "eof":
        eof_event.set()
    elif ctype == "flush":
        # Client-driven utterance boundary — used as a watchdog when Silero VAD
        # gets stuck in its 0.35–0.50 hysteresis band and never emits end-of-speech.
        # Emits whatever transcript the model has buffered as is_final=True, then
        # resets internal state so the session stays open for the next utterance.
        try:
            final = await session.finish()
            if final is not None:
                await _safe_send_update(websocket, final)
        except Exception:
            logger.exception("flush failed")
            await _send_error(websocket, "flush_failed", "session flush raised")
    elif ctype == "reset":
        await _send_error(websocket, "unsupported", "reset is not implemented")
    else:
        await _send_error(websocket, "bad_control", f"Unknown control type: {ctype!r}")


async def _emitter_loop(
    websocket: WebSocket,
    session: STTSession,
    emit_interval_ms: int,
    stop_event: asyncio.Event,
    eof_event: asyncio.Event,
) -> None:
    """Poll the session for transcript updates and forward them to the client."""
    interval_s = max(emit_interval_ms, 50) / 1000.0
    try:
        while not stop_event.is_set() and not eof_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                return
            except asyncio.TimeoutError:
                pass

            update = await session.process_iter()
            if update is not None:
                await _safe_send_update(websocket, update)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("emitter loop failed")


async def _safe_send_update(websocket: WebSocket, update: TranscriptUpdate) -> None:
    if websocket.application_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json(update.to_dict())
    except WebSocketDisconnect:
        raise
    except Exception:
        logger.debug("send_json failed", exc_info=True)


# Re-export so app.py can check this name for early failure
__all__ = ["router"]


# Mypy/ruff happiness — keep a few imports flagged so unused-warnings don't trigger.
_ = (status, Any)
