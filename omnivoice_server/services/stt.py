"""
Per-WebSocket STT session plumbing.

The Whisper model held by STTModelService carries internal buffer state on each
`insert_audio` / `infer` call, so two concurrent sessions would clobber each other.
We serialise inference through a single asyncio.Lock and gate session admission with
an asyncio.Semaphore (size = cfg.stt_max_concurrent).

Each session wraps SimulStreaming's `SimulWhisperOnline` (optionally wrapped again
by `VACOnlineASRProcessor` for Silero VAD). Audio chunks arrive as raw PCM int16 LE
at 16 kHz mono; we convert to float32 numpy before passing upstream.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import Settings
from .stt_model import STTModelService

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


@dataclass
class TranscriptUpdate:
    """One transcript emission sent to the client as a JSON text frame."""

    start_ms: int
    end_ms: int
    text: str
    is_final: bool
    emission_time_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "is_final": self.is_final,
            "emission_time_ms": self.emission_time_ms,
        }


def _pcm16_bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert little-endian int16 PCM bytes to a 1-D float32 numpy array in [-1, 1]."""
    if not pcm_bytes:
        return np.zeros(0, dtype=np.float32)
    ints = np.frombuffer(pcm_bytes, dtype=np.int16)
    return ints.astype(np.float32) / 32768.0


class STTSession:
    """One per open WebSocket. Holds the SimulStreaming online processor for this stream."""

    def __init__(
        self,
        online: Any,
        inference_lock: asyncio.Lock,
        executor: ThreadPoolExecutor,
        model_svc: STTModelService,
        session_start: float,
    ) -> None:
        self._online = online
        self._inference_lock = inference_lock
        self._executor = executor
        self._model_svc = model_svc
        self._session_start = session_start
        self._closed = False
        # Idle-flush watchdog state (see _emitter_loop in routers/transcribe.py).
        # last_audio_ts tracks when the most recent audio chunk arrived; when
        # emissions have been quiet for stt_idle_flush_ms we force a finish()
        # to catch VAD-stuck utterances.
        self.last_audio_ts: float = time.monotonic()
        self.speech_since_last_flush: bool = False

    def insert_audio_chunk(self, pcm_int16_le: bytes) -> None:
        """Append raw PCM int16 LE @ 16 kHz mono to the session buffer. Non-blocking."""
        if self._closed:
            return
        audio = _pcm16_bytes_to_float32(pcm_int16_le)
        if audio.size == 0:
            return
        self._online.insert_audio_chunk(audio)
        self.last_audio_ts = time.monotonic()
        self.speech_since_last_flush = True

    async def process_iter(self) -> TranscriptUpdate | None:
        """Run one inference pass. Serialised across all sessions via the shared lock."""
        if self._closed:
            return None
        loop = asyncio.get_running_loop()
        async with self._inference_lock:
            raw = await loop.run_in_executor(self._executor, self._process_iter_sync)
        return self._normalise(raw, is_final=False)

    async def finish(self) -> TranscriptUpdate | None:
        """Final flush. Serialised."""
        if self._closed:
            return None
        loop = asyncio.get_running_loop()
        async with self._inference_lock:
            raw = await loop.run_in_executor(self._executor, self._finish_sync)
        return self._normalise(raw, is_final=True)

    def _process_iter_sync(self) -> dict[str, Any] | None:
        try:
            return self._online.process_iter()
        except AssertionError as exc:
            # SimulStreaming's online processor raises AssertionError in some edge cases
            # (e.g. when fed before any speech has been detected). Upstream main loops
            # swallow these; do the same.
            logger.debug("process_iter assertion (non-fatal): %s", exc)
            return None
        except Exception:
            logger.exception("process_iter failed")
            return None

    def _finish_sync(self) -> dict[str, Any] | None:
        try:
            return self._online.finish()
        except Exception:
            logger.exception("finish failed")
            return None

    def _normalise(
        self,
        raw: dict[str, Any] | None,
        *,
        is_final: bool,
    ) -> TranscriptUpdate | None:
        if raw is None:
            return None
        text = str(raw.get("text", ""))
        # Caller's is_final=True means "forced finish" — don't let a stale
        # raw["is_final"]=False from VAC downgrade it. Empty-text forced finals
        # still pass through so clients always get the end-of-utterance signal.
        effective_final = is_final or bool(raw.get("is_final", False))
        if not text and not effective_final:
            return None
        return TranscriptUpdate(
            start_ms=int(round(float(raw.get("start", 0.0)) * 1000)),
            end_ms=int(round(float(raw.get("end", 0.0)) * 1000)),
            text=text,
            is_final=effective_final,
            emission_time_ms=int(round((time.monotonic() - self._session_start) * 1000)),
        )

    def close(self) -> None:
        self._closed = True


class STTService:
    """Gatekeeper: admission semaphore + shared inference lock + executor."""

    def __init__(
        self,
        model_svc: STTModelService,
        executor: ThreadPoolExecutor,
        cfg: Settings,
    ) -> None:
        self._model_svc = model_svc
        self._executor = executor
        self._cfg = cfg
        self._admission = asyncio.Semaphore(cfg.stt_max_concurrent)
        self._inference_lock = asyncio.Lock()

    async def acquire_session(self) -> STTSession:
        """Acquire an admission slot and build a fresh online processor."""
        await self._admission.acquire()
        try:
            online = self._build_online_processor()
            return STTSession(
                online=online,
                inference_lock=self._inference_lock,
                executor=self._executor,
                model_svc=self._model_svc,
                session_start=time.monotonic(),
            )
        except Exception:
            self._admission.release()
            raise

    def release_session(self, session: STTSession) -> None:
        """Release the admission slot. Must be called exactly once per acquire_session."""
        session.close()
        self._admission.release()
        self._model_svc.cleanup()

    def _build_online_processor(self) -> Any:
        """Instantiate SimulWhisperOnline (and wrap with VAC if configured)."""
        vac_cls = _load_vac_class()

        online = _build_simul_whisper_online(self._model_svc.asr)

        if self._cfg.stt_vad and self._model_svc.vad_model is not None and vac_cls is not None:
            online = vac_cls(self._cfg.stt_min_chunk_size, online)

        return online


def _load_vac_class() -> Any | None:
    """Dynamically import VACOnlineASRProcessor; returns None if unavailable."""
    try:
        from simulstreaming.whisper.whisper_streaming.vac_online_processor import (  # noqa: N811
            VACOnlineASRProcessor,
        )
    except ImportError:
        return None
    return VACOnlineASRProcessor


def _build_simul_whisper_online(asr: Any) -> Any:
    """
    Build a fresh SimulWhisperOnline(asr). Imported lazily because the SimulStreaming
    root script defines these classes at module-level and is only importable once
    /opt/simulstreaming-repo is on PYTHONPATH.
    """
    # simulstreaming_whisper.py is at the repo root of SimulStreaming; it defines
    # SimulWhisperOnline (the per-session online processor).
    try:
        from simulstreaming_whisper import SimulWhisperOnline  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Cannot import SimulWhisperOnline. Ensure /opt/simulstreaming-repo is on PYTHONPATH."
        ) from exc
    return SimulWhisperOnline(asr)
