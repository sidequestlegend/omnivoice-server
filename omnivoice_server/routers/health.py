"""Health and metrics endpoints."""

from __future__ import annotations

import time

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    """Readiness check. 503 while either TTS or STT model is still loading, 200 when ready."""
    cfg = request.app.state.cfg
    model_svc = request.app.state.model_svc
    stt_model_svc = getattr(request.app.state, "stt_model_svc", None)
    ram_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)

    tts_loaded = bool(model_svc.is_loaded)
    stt_required = bool(getattr(cfg, "stt_enabled", False))
    stt_loaded = bool(stt_model_svc.is_loaded) if stt_model_svc is not None else False

    ready = tts_loaded and (stt_loaded or not stt_required)

    body: dict = {
        "status": "healthy" if ready else "starting",
        "ready": ready,
        # Legacy top-level keys preserved for existing clients/tests.
        "model_loaded": tts_loaded,
        "model_id": cfg.model_id,
        "tts": {
            "loaded": tts_loaded,
            "model_id": cfg.model_id,
        },
        "stt": {
            "enabled": stt_required,
            "loaded": stt_loaded,
            "model_path": getattr(cfg, "stt_model_path", None) if stt_required else None,
        },
        "memory_rss_mb": ram_mb,
    }

    if not ready:
        return JSONResponse(status_code=503, content=body)

    body["uptime_s"] = round(time.monotonic() - request.app.state.start_time, 1)
    return body


@router.get("/metrics")
async def metrics(request: Request):
    """Request metrics and current memory usage."""
    metrics_svc = request.app.state.metrics_svc
    snapshot = metrics_svc.snapshot()
    snapshot["ram_mb"] = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)

    # Include script metrics if orchestrator is available
    script_orchestrator = getattr(request.app.state, "script_orchestrator", None)
    if script_orchestrator is not None:
        snapshot.update(script_orchestrator.script_metrics.snapshot())

    return snapshot
