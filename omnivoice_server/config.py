"""
Server configuration.
Priority: CLI flags > env vars > defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import platformdirs
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    import torch


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNIVOICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate foreign env vars (HF_TOKEN, PATH, etc.)
    )

    # Server
    host: str = Field(default="127.0.0.1", description="Bind host")
    port: int = Field(default=8880, ge=0, le=65535)
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Model
    model_id: str = Field(
        default="k2-fsa/OmniVoice",
        description="HuggingFace repo ID or local path",
    )
    model_cache_dir: Path | None = Field(
        default=None,
        description="Override HuggingFace model cache directory",
    )
    device: Literal["auto", "cuda", "mps", "cpu"] = "cpu"
    num_step: int = Field(default=32, ge=1, le=64)  # Upstream default

    # STT (Whisper via SimulStreaming). SimulStreaming must be on PYTHONPATH —
    # baked in by Dockerfile.cuda at /opt/simulstreaming-repo.
    # Default off so importing Settings in non-Docker environments (tests,
    # standalone dev) doesn't require SimulStreaming. docker-compose-cuda.yml
    # sets OMNIVOICE_STT_ENABLED=true to turn it on in the container.
    stt_enabled: bool = Field(
        default=False,
        description="Load the Whisper STT model at startup.",
    )
    stt_model_path: str = Field(
        default="large-v3",
        description=(
            "Whisper model name or path to a .pt file. Plain names like 'large-v3' / 'medium' / "
            "'small' trigger an auto-download into the HF cache. Absolute paths load from disk."
        ),
    )
    stt_language: str = Field(
        default="en",
        description="ISO 639-1 language code, or 'auto' for detection.",
    )
    stt_task: Literal["transcribe", "translate"] = Field(
        default="transcribe",
        description="Whether to transcribe in source language or translate to English.",
    )
    stt_beams: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Beam search width. 1 = greedy decoder.",
    )
    stt_min_chunk_size: float = Field(
        default=0.5,
        ge=0.1,
        le=5.0,
        description="Seconds of buffered audio before running inference.",
    )
    stt_audio_max_len: float = Field(
        default=30.0,
        ge=5.0,
        le=60.0,
        description="Max seconds of audio buffered on the model side before it forces a flush.",
    )
    stt_frame_threshold: int = Field(
        default=25,
        ge=1,
        le=250,
        description="AlignAtt frame threshold (upstream --frame_threshold).",
    )
    stt_vad: bool = Field(
        default=True,
        description="Wrap the online processor in Silero VAD (VACOnlineASRProcessor).",
    )
    stt_vac_chunk_size: float = Field(
        default=0.04,
        ge=0.01,
        le=0.5,
        description="VAD chunk size in seconds.",
    )
    stt_max_concurrent: int = Field(
        default=1,
        ge=1,
        le=8,
        description=(
            "Max concurrent STT sessions. The shared Whisper model has internal state, "
            "so values > 1 queue sessions rather than running them in parallel."
        ),
    )
    stt_emit_interval_ms: int = Field(
        default=250,
        ge=50,
        le=2000,
        description="Minimum interval between transcript emission attempts, in ms.",
    )
    stt_init_prompt: str | None = Field(
        default=None,
        description="Optional initial prompt text to bias the decoder (scrolls with context).",
    )
    stt_static_init_prompt: str | None = Field(
        default=None,
        description="Initial prompt that is never scrolled out (e.g. domain terminology).",
    )
    stt_max_context_tokens: int | None = Field(
        default=None,
        description="Cap on context tokens carried between iterations. None = upstream default.",
    )

    # Advanced generation params (passed through to OmniVoice.generate())
    # Expose the ones users are likely to tune; leave the rest at upstream defaults.
    guidance_scale: float = Field(
        default=2.0,
        ge=0.0,
        le=10.0,
        description="CFG scale. Higher = stronger voice conditioning.",
    )
    denoise: bool = Field(
        default=True,
        description="Enable upstream denoising token. Recommended on.",
    )
    t_shift: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,  # Upstream docs don't specify max; allowing up to 2.0 for flexibility
        description="Noise schedule shift. Affects quality/speed tradeoff.",
    )
    position_temperature: float = Field(
        default=5.0,
        ge=0.0,
        le=10.0,
        description=(
            "Temperature for mask-position selection. "
            "0=deterministic/greedy, higher=more diversity."
        ),
    )
    class_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description=(
            "Temperature for token sampling at each step. 0=greedy, higher=more randomness."
        ),
    )

    # Inference
    max_concurrent: int = Field(
        default=2,
        ge=1,
        le=16,
        description="Max simultaneous inference calls",
    )
    request_timeout_s: int = Field(
        default=120,
        description="Max seconds per synthesis request before 504",
    )
    shutdown_timeout: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Seconds to wait for in-flight requests on shutdown",
    )

    # Voice profiles
    profile_dir: Path = Field(
        default=Path(platformdirs.user_data_dir("omnivoice")) / "profiles",
        description="Directory for saved voice cloning profiles",
    )

    # Auth
    api_key: str = Field(
        default="",
        description="Optional Bearer token. Empty = no auth.",
    )

    # CORS
    # NoDecode: skip pydantic-settings' JSON-list decoding so our custom validator
    # below can accept "*", comma-separated values, or a JSON array.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5001",
            "http://127.0.0.1:5001",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        description="Allowed CORS origins for browser clients.",
    )
    cors_allow_credentials: bool = Field(
        default=False,
        description="Allow credentialed CORS requests. Requires explicit origins.",
    )

    # Streaming
    stream_chunk_max_chars: int = Field(
        default=400,
        description="Max chars per sentence chunk when streaming",
    )

    max_ref_audio_mb: int = Field(
        default=25,
        ge=1,
        le=200,
        description="Max upload size for ref_audio files in megabytes.",
    )

    default_voice: str = Field(
        default="female, british accent",
        description=(
            "Default voice description used when no voice is specified for a speaker. "
            "Deployers can customise this for non-English use cases."
        ),
    )

    @property
    def max_ref_audio_bytes(self) -> int:
        """Return max upload size in bytes."""
        return self.max_ref_audio_mb * 1024 * 1024

    @field_validator("device")
    @classmethod
    def resolve_auto_device(cls, v: str) -> str:
        if v != "auto":
            return v
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def parse_cors_allow_origins(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("cors_allow_origins must be a list of strings")
                return parsed
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    @field_validator("cors_allow_credentials")
    @classmethod
    def validate_cors_credentials(cls, value: bool, info):
        origins = info.data.get("cors_allow_origins", [])
        if value and "*" in origins:
            raise ValueError(
                "cors_allow_credentials cannot be true when cors_allow_origins includes '*'"
            )
        return value

    @property
    def torch_dtype(self) -> torch.dtype:
        """Return appropriate torch dtype for device."""
        import torch

        if self.device in ("cuda", "mps"):
            return torch.float16
        return torch.float32

    @property
    def torch_device_map(self) -> str:
        """Map to device string for OmniVoice.from_pretrained()."""
        if self.device == "cuda":
            return "cuda:0"
        return self.device  # "mps" or "cpu"
