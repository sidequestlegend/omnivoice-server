"""
Real-time streaming STT client for omnivoice-server.

Captures mic audio at 16 kHz mono int16 and streams it as raw binary WebSocket
frames to /v1/audio/transcribe. Prints transcript updates as they arrive.

Requirements:
    pip install websockets pyaudio
    # Windows: if pyaudio fails to install, use a prebuilt wheel:
    #   pip install pipwin && pipwin install pyaudio

Usage:
    # Local Docker container
    python streaming_transcribe.py --url ws://localhost:8880/v1/audio/transcribe

    # Against a Salad deployment
    python streaming_transcribe.py --url wss://<deployment>.salad.cloud/v1/audio/transcribe \
        --api-key $OMNIVOICE_API_KEY

    # From a WAV file instead of a mic
    python streaming_transcribe.py --url ws://localhost:8880/v1/audio/transcribe --file sample.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

try:
    import websockets
except ImportError as exc:
    raise SystemExit("websockets is required: pip install websockets") from exc

# Audio format constants (must match the server's expected input)
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
FRAMES_PER_CHUNK = 1_600  # 100 ms @ 16 kHz


def _build_url_and_headers(url: str, api_key: str | None) -> tuple[str, list[tuple[str, str]]]:
    """Return (url, extra_headers). API key travels as a query param for WS browser compat."""
    headers: list[tuple[str, str]] = []
    if api_key:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={api_key}"
    return url, headers


async def _send_from_mic(ws: websockets.WebSocketClientProtocol) -> None:
    try:
        import pyaudio
    except ImportError as exc:
        raise SystemExit("pyaudio is required for mic capture: pip install pyaudio") from exc

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAMES_PER_CHUNK,
    )
    print(f"[mic] capturing @ {SAMPLE_RATE} Hz — speak into the microphone (Ctrl+C to stop)")
    try:
        while True:
            data = stream.read(FRAMES_PER_CHUNK, exception_on_overflow=False)
            await ws.send(data)
    except asyncio.CancelledError:
        raise
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        try:
            await ws.send(json.dumps({"type": "eof"}))
        except Exception:
            pass


async def _send_from_file(ws: websockets.WebSocketClientProtocol, wav_path: Path) -> None:
    """Simulate realtime from a 16 kHz mono 16-bit WAV file."""
    with wave.open(str(wav_path), "rb") as wf:
        if (
            wf.getnchannels() != CHANNELS
            or wf.getframerate() != SAMPLE_RATE
            or wf.getsampwidth() != SAMPLE_WIDTH
        ):
            raise SystemExit(
                f"File must be {SAMPLE_RATE} Hz mono 16-bit PCM WAV. "
                f"Got: {wf.getframerate()} Hz, {wf.getnchannels()} ch, "
                f"{wf.getsampwidth() * 8}-bit"
            )
        print(f"[file] streaming {wav_path} ({wf.getnframes() / SAMPLE_RATE:.1f}s)")
        chunk_s = FRAMES_PER_CHUNK / SAMPLE_RATE
        while True:
            data = wf.readframes(FRAMES_PER_CHUNK)
            if not data:
                break
            await ws.send(data)
            await asyncio.sleep(chunk_s)
    await ws.send(json.dumps({"type": "eof"}))


async def _receive(ws: websockets.WebSocketClientProtocol) -> None:
    start = time.monotonic()
    partial_line = ""
    async for raw in ws:
        if isinstance(raw, bytes):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[server raw] {raw}")
            continue
        if "error" in payload:
            print(f"\n[error] {payload['error']}", file=sys.stderr)
            continue
        text = payload.get("text", "").strip()
        if not text:
            continue
        elapsed = time.monotonic() - start
        is_final = payload.get("is_final", False)
        if is_final:
            if partial_line:
                print()
                partial_line = ""
            print(f"[{elapsed:6.2f}s FINAL ] {text}")
        else:
            line = f"[{elapsed:6.2f}s partial] {text}"
            # Overwrite the current partial on stdout.
            sys.stdout.write("\r" + line.ljust(max(len(partial_line), len(line))))
            sys.stdout.flush()
            partial_line = line


async def run(url: str, api_key: str | None, wav_file: Path | None) -> None:
    full_url, headers = _build_url_and_headers(url, api_key)
    print(f"[connect] {full_url}")
    async with websockets.connect(full_url, additional_headers=headers, max_size=None) as ws:
        send_task = asyncio.create_task(
            _send_from_file(ws, wav_file) if wav_file else _send_from_mic(ws)
        )
        recv_task = asyncio.create_task(_receive(ws))
        done, pending = await asyncio.wait(
            {send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                raise exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream mic/file audio to omnivoice-server STT.")
    parser.add_argument(
        "--url",
        default="ws://localhost:8880/v1/audio/transcribe",
        help="WebSocket URL (ws:// or wss://)",
    )
    parser.add_argument("--api-key", default=None, help="Bearer token if the server has auth on")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to a 16 kHz mono 16-bit WAV to stream instead of using the mic",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.url, args.api_key, args.file))
    except KeyboardInterrupt:
        print("\n[stopped]")


if __name__ == "__main__":
    main()
