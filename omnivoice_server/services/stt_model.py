"""
Loads and holds the Whisper STT model singleton (SimulStreaming's PaddedAlignAttWhisper).
Model is loaded once at startup and shared across all WebSocket sessions. Whisper's
internal state is session-shared, so STT inference must be serialised — the service
layer (services/stt.py) handles that.

SimulStreaming is not pip-installable; Dockerfile.cuda clones it to /opt/simulstreaming-repo
and adds that directory to PYTHONPATH.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import psutil
import torch

from ..config import Settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class STTModelLoadError(RuntimeError):
    pass


class STTModelService:
    """Holds the single PaddedAlignAttWhisper instance and (optionally) a shared Silero VAD."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._asr: Any | None = None  # SimulWhisperASR (duck-typed to avoid import at class time)
        self._vad_model: Any | None = None  # Silero VAD torch module, None if stt_vad=False
        self._loaded = False

    async def load(self) -> None:
        """Blocking model load, offloaded to a short-lived thread."""
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as ex:
            await loop.run_in_executor(ex, self._load_sync)

    def _load_sync(self) -> None:
        ram_before = _get_ram_mb()
        t0 = time.monotonic()

        logger.info(
            "Loading STT model '%s' on %s (task=%s, language=%s, beams=%d, vad=%s)",
            self.cfg.stt_model_path,
            self.cfg.device,
            self.cfg.stt_task,
            self.cfg.stt_language,
            self.cfg.stt_beams,
            self.cfg.stt_vad,
        )

        if self.cfg.device == "cuda" and torch.cuda.is_available():
            cap = torch.cuda.get_device_capability()
            name = torch.cuda.get_device_name()
            logger.info("CUDA device: %s (compute capability %d.%d)", name, cap[0], cap[1])

        try:
            from simulstreaming.whisper.simul_whisper.config import AlignAttConfig
            from simulstreaming.whisper.simul_whisper.simul_whisper import PaddedAlignAttWhisper
        except ImportError as exc:
            raise STTModelLoadError(
                "SimulStreaming is not importable. It must be cloned to /opt/simulstreaming-repo "
                "and that path added to PYTHONPATH (see Dockerfile.cuda)."
            ) from exc

        decoder_type = "beam" if self.cfg.stt_beams > 1 else "greedy"

        cfg = AlignAttConfig(
            model_path=self.cfg.stt_model_path,
            segment_length=self.cfg.stt_min_chunk_size,
            frame_threshold=self.cfg.stt_frame_threshold,
            language=self.cfg.stt_language,
            audio_max_len=self.cfg.stt_audio_max_len,
            audio_min_len=0.0,
            cif_ckpt_path="",
            decoder_type=decoder_type,
            beam_size=self.cfg.stt_beams,
            task=self.cfg.stt_task,
            never_fire=False,
            init_prompt=self.cfg.stt_init_prompt,
            max_context_tokens=self.cfg.stt_max_context_tokens,
            static_init_prompt=self.cfg.stt_static_init_prompt,
            logdir=None,
        )

        try:
            model = PaddedAlignAttWhisper(cfg)
        except Exception as exc:
            raise STTModelLoadError(f"PaddedAlignAttWhisper init failed: {exc}") from exc

        # Mirror SimulWhisperASR's public shape so services/stt.py can build
        # SimulWhisperOnline(asr) — it only reads asr.model.
        self._asr = _ASRShim(model=model)

        if self.cfg.stt_vad:
            try:
                vad_model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    trust_repo=True,
                )
                self._vad_model = vad_model
            except Exception as exc:
                logger.warning("Silero VAD load failed: %s — STT will run without VAD.", exc)
                self._vad_model = None

        elapsed = time.monotonic() - t0
        ram_after = _get_ram_mb()
        logger.info(
            "STT model loaded in %.1fs. RAM: %.0fMB -> %.0fMB (+%.0fMB)",
            elapsed,
            ram_before,
            ram_after,
            ram_after - ram_before,
        )
        self._loaded = True

    @property
    def asr(self) -> Any:
        if not self._loaded or self._asr is None:
            raise RuntimeError("STT model not loaded")
        return self._asr

    @property
    def vad_model(self) -> Any | None:
        return self._vad_model

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def cleanup(self) -> None:
        """Free cached GPU memory after inference."""
        gc.collect()
        if self.cfg.device == "cuda":
            try:
                torch.cuda.empty_cache()
            except Exception as exc:
                logger.debug("cuda.empty_cache failed (non-fatal): %s", exc)


class _ASRShim:
    """Minimal duck-type of SimulWhisperASR. SimulWhisperOnline only reads `.model`."""

    def __init__(self, model: Any) -> None:
        self.model = model


def _get_ram_mb() -> float:
    return psutil.Process().memory_info().rss / 1024 / 1024
