"""
Tests for the WebSocket STT endpoint (/v1/audio/transcribe).

The STTService.acquire_session / release_session path is mocked (see conftest.py
stt_client fixture) so these tests don't need SimulStreaming or a GPU.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omnivoice_server.app import create_app
from omnivoice_server.config import Settings


def test_health_reports_stt_loaded(stt_client: TestClient):
    r = stt_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["stt"]["enabled"] is True
    assert body["stt"]["loaded"] is True


def test_health_503_when_stt_not_loaded(stt_settings: Settings):
    """Without patching is_loaded=True, /health should report not-ready."""
    app = create_app(stt_settings)
    # Force the lifespan to skip real loading by NOT patching; use a bare TestClient
    # context after manually marking the TTS model loaded but not the STT one.
    from unittest.mock import AsyncMock, patch

    with patch("omnivoice_server.services.model.ModelService.load", new_callable=AsyncMock):
        with patch(
            "omnivoice_server.services.model.ModelService.is_loaded",
            new_callable=lambda: property(lambda self: True),
        ):
            with patch(
                "omnivoice_server.services.stt_model.STTModelService.load",
                new_callable=AsyncMock,
            ):
                # Leave STTModelService.is_loaded at its default (False)
                with TestClient(app) as c:
                    r = c.get("/health")
                    assert r.status_code == 503
                    body = r.json()
                    assert body["ready"] is False
                    assert body["stt"]["enabled"] is True
                    assert body["stt"]["loaded"] is False


def test_ws_receives_partial_and_final(stt_client: TestClient):
    silent_pcm = b"\x00\x00" * 1600  # 100 ms of silence @ 16 kHz

    with stt_client.websocket_connect("/v1/audio/transcribe") as ws:
        ws.send_bytes(silent_pcm)
        first = ws.receive_json()
        assert "text" in first
        assert first["is_final"] is False
        assert first["text"].startswith("partial")

        ws.send_text(json.dumps({"type": "eof"}))

        # After EOF, the server flushes a final update and closes.
        saw_final = False
        for _ in range(5):
            try:
                msg = ws.receive_json()
            except WebSocketDisconnect:
                break
            if msg.get("is_final") is True:
                saw_final = True
                assert msg["text"] == "final"
                break
        assert saw_final, "expected a final transcript frame before close"


def test_ws_rejects_when_stt_disabled(client: TestClient):
    """STT disabled (the default `client` fixture) → WS should close immediately."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/audio/transcribe"):
            pass


def test_ws_requires_auth_when_api_key_set(tmp_path_factory):
    """If OMNIVOICE_API_KEY is set, an unauth'd WS connect must be closed."""
    from unittest.mock import AsyncMock, patch

    settings = Settings(
        device="cpu",
        num_step=4,
        max_concurrent=1,
        api_key="secret-key",
        profile_dir=tmp_path_factory.mktemp("profiles"),
        stt_enabled=True,
        stt_vad=False,
    )
    app = create_app(settings)

    with patch("omnivoice_server.services.model.ModelService.load", new_callable=AsyncMock):
        with patch(
            "omnivoice_server.services.model.ModelService.is_loaded",
            new_callable=lambda: property(lambda self: True),
        ):
            with patch(
                "omnivoice_server.services.stt_model.STTModelService.load",
                new_callable=AsyncMock,
            ):
                with patch(
                    "omnivoice_server.services.stt_model.STTModelService.is_loaded",
                    new_callable=lambda: property(lambda self: True),
                ):
                    with TestClient(app) as c:
                        # No token → rejected
                        with pytest.raises(WebSocketDisconnect):
                            with c.websocket_connect("/v1/audio/transcribe"):
                                pass
                        # Wrong token → rejected
                        with pytest.raises(WebSocketDisconnect):
                            with c.websocket_connect("/v1/audio/transcribe?token=wrong"):
                                pass
