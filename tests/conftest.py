"""
Shared fixtures for all tests.

FIX: settings() fixture previously used pytest.tmp_path_factory.mktemp()
as a plain attribute call — this is not valid Python. tmp_path_factory must
be declared as a fixture parameter. Fixed below.
"""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, patch

import pytest
import torch
from fastapi.testclient import TestClient

from omnivoice_server.app import create_app
from omnivoice_server.config import Settings

# ── Audio helpers ─────────────────────────────────────────────────────────────


def make_silence_tensor(duration_s: float = 1.0) -> torch.Tensor:
    """Return a silent (1, T) float32 tensor at 24kHz."""
    num_samples = int(24_000 * duration_s)
    return torch.zeros(1, num_samples)


def make_wav_bytes(duration_frames: int = 0, sample_rate: int = 24000) -> bytes:
    """
    Minimal valid WAV file. Used by clone and profile tests.
    duration_frames=0 gives the smallest valid WAV (44-byte header, no audio).
    Tests that need parseable audio should pass duration_frames > 0.
    """
    data_size = duration_frames * 2  # 16-bit mono
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
        + b"\x00" * data_size
    )


# ── Mock inference ────────────────────────────────────────────────────────────


def _mock_synthesize(req, **_kwargs):
    """Fake synthesis — returns 1s of silence immediately."""
    from omnivoice_server.services.inference import SynthesisResult

    tensor = make_silence_tensor(1.0)
    return SynthesisResult(tensors=[tensor], duration_s=1.0, latency_s=0.05)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path_factory):  # FIX: tmp_path_factory is a fixture param, not pytest attr
    profile_dir = tmp_path_factory.mktemp("profiles")
    # STT is opt-in for tests — most cases don't touch it. Tests that exercise
    # the WebSocket endpoint use the stt_settings/stt_client fixtures below.
    return Settings(
        device="cpu",
        num_step=4,
        max_concurrent=1,
        api_key="",
        profile_dir=profile_dir,
        stt_enabled=False,
    )


@pytest.fixture
def client(settings):
    app = create_app(settings)

    with patch("omnivoice_server.services.model.ModelService.load", new_callable=AsyncMock):
        with patch(
            "omnivoice_server.services.model.ModelService.is_loaded",
            new_callable=lambda: property(lambda self: True),
        ):
            with TestClient(app) as c:
                c.app.state.inference_svc.synthesize = AsyncMock(side_effect=_mock_synthesize)
                yield c


@pytest.fixture
def stt_settings(tmp_path_factory):
    profile_dir = tmp_path_factory.mktemp("profiles")
    return Settings(
        device="cpu",
        num_step=4,
        max_concurrent=1,
        api_key="",
        profile_dir=profile_dir,
        stt_enabled=True,
        stt_model_path="large-v3",
        stt_vad=False,  # VAD requires torch.hub.load — skip in unit tests
        stt_max_concurrent=1,
        stt_emit_interval_ms=50,
    )


@pytest.fixture
def stt_client(stt_settings):
    """A TestClient with both TTS and STT services fully mocked."""
    from omnivoice_server.services.stt import STTSession

    app = create_app(stt_settings)

    # Fake STT session: records inserted audio, returns a canned partial on each
    # process_iter() and a canned final on finish().
    class _FakeSession:
        def __init__(self):
            import time as _time
            self.chunks: list[bytes] = []
            self.closed = False
            self.iters = 0
            # Attributes the idle-flush watchdog reads (see emitter_loop)
            self.last_audio_ts = _time.monotonic()
            self.speech_since_last_flush = False

        def insert_audio_chunk(self, pcm_bytes: bytes) -> None:
            import time as _time
            self.chunks.append(pcm_bytes)
            self.last_audio_ts = _time.monotonic()
            self.speech_since_last_flush = True

        async def process_iter(self):
            self.iters += 1
            from omnivoice_server.services.stt import TranscriptUpdate
            return TranscriptUpdate(
                start_ms=0,
                end_ms=500,
                text=f"partial #{self.iters}",
                is_final=False,
                emission_time_ms=self.iters * 100,
            )

        async def finish(self):
            from omnivoice_server.services.stt import TranscriptUpdate
            return TranscriptUpdate(
                start_ms=0,
                end_ms=1000,
                text="final",
                is_final=True,
                emission_time_ms=999,
            )

        def close(self) -> None:
            self.closed = True

    async def _fake_acquire_session(self):  # bound method on STTService
        session = _FakeSession()
        app.state.last_stt_session = session
        return session

    def _fake_release_session(self, session):
        session.close()

    with patch("omnivoice_server.services.model.ModelService.load", new_callable=AsyncMock):
        with patch(
            "omnivoice_server.services.model.ModelService.is_loaded",
            new_callable=lambda: property(lambda self: True),
        ):
            with patch("omnivoice_server.services.stt_model.STTModelService.load",
                       new_callable=AsyncMock):
                with patch(
                    "omnivoice_server.services.stt_model.STTModelService.is_loaded",
                    new_callable=lambda: property(lambda self: True),
                ):
                    with patch(
                        "omnivoice_server.services.stt.STTService.acquire_session",
                        _fake_acquire_session,
                    ), patch(
                        "omnivoice_server.services.stt.STTService.release_session",
                        _fake_release_session,
                    ):
                        with TestClient(app) as c:
                            c.app.state.inference_svc.synthesize = AsyncMock(
                                side_effect=_mock_synthesize
                            )
                            yield c

    # Silence unused-import warnings
    _ = STTSession


@pytest.fixture
def sample_audio_bytes():
    """A minimal WAV suitable for upload tests."""
    return make_wav_bytes(duration_frames=100)
