"""
Real-time streaming audio player for omnivoice-server, using the WebSocket
TTS endpoint that carries sentence-level segment markers.

Wire format (server → client):
    binary frame  →  raw PCM int16 LE @ 24 kHz mono (one or more per sentence)
    text frame    →  JSON control/metadata messages:
                     {"type":"segment","index":N,"start_s":F,"end_s":F,"text":"…"}
                     {"type":"done","total_segments":N,"total_duration_s":F,…}
                     {"type":"error","code":"…","message":"…"}

Client → server: one text frame at the start, JSON matching SpeechRequest.

Requirements:
    pip install websockets pyaudio

Usage:
    python streaming_player.py "Your text to synthesize"
    python streaming_player.py "Hello" --voice alloy --speed 1.1
    python streaming_player.py "Hello" --url ws://localhost:8880 --api-key $KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from urllib.parse import urlsplit, urlunsplit

try:
    import websockets
except ImportError as exc:
    raise SystemExit("websockets is required: pip install websockets") from exc
try:
    import pyaudio
except ImportError as exc:
    raise SystemExit("pyaudio is required: pip install pyaudio") from exc

# Audio format constants (must match server output)
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
# Output buffer: ~21ms @ 24kHz. Smaller = lower start-of-playback latency.
FRAMES_PER_BUFFER = 512


def _coerce_ws_url(url: str) -> str:
    """Accept http://… or ws://… for --url; always return a ws:// or wss:// URL
    pointing at /v1/audio/speech/stream."""
    parts = urlsplit(url)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parts.scheme)
    if not scheme:
        raise SystemExit(f"unsupported URL scheme: {parts.scheme!r}")
    path = parts.path or ""
    if not path.endswith("/v1/audio/speech/stream"):
        path = path.rstrip("/") + "/v1/audio/speech/stream"
    return urlunsplit((scheme, parts.netloc, path, parts.query, parts.fragment))


async def stream_and_play(
    text: str,
    url: str,
    voice: str,
    speed: float,
    api_key: str | None = None,
) -> None:
    ws_url = _coerce_ws_url(url)
    if api_key:
        joiner = "&" if "?" in ws_url else "?"
        ws_url = f"{ws_url}{joiner}token={api_key}"

    # Initialize PyAudio — open the output stream eagerly so it's ready the
    # instant the first byte arrives (avoids device-init cost on first write).
    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        output=True,
        frames_per_buffer=FRAMES_PER_BUFFER,
    )

    print(f"Streaming: {text[:60]}{'…' if len(text) > 60 else ''}")
    print(f"Voice: {voice}  Speed: {speed}x")
    print(f"Connecting to {ws_url.replace('token=' + (api_key or ''), 'token=***')}")

    t0 = time.perf_counter()
    first_audio_at: float | None = None
    first_segment_at: float | None = None
    bytes_received = 0
    segments: list[dict] = []

    request_body = {
        "model": "omnivoice",
        "input": text,
        "voice": voice,
        "speed": speed,
        "response_format": "pcm",
        "stream": True,
    }

    try:
        async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
            t_open = time.perf_counter()
            print(f"[{(t_open - t0) * 1000:7.0f} ms] ws connected", flush=True)
            await ws.send(json.dumps(request_body))

            # Network chunks can split mid-sample. For 16-bit PCM, writing odd
            # bytes makes PyAudio drop the trailing byte, which byte-swaps every
            # subsequent sample → garbled audio. Carry the stray byte forward.
            leftover = b""
            async for message in ws:
                now = time.perf_counter()
                if isinstance(message, (bytes, bytearray)):
                    if first_audio_at is None:
                        first_audio_at = now
                    bytes_received += len(message)
                    data = leftover + message
                    if len(data) % SAMPLE_WIDTH:
                        leftover = data[-1:]
                        data = data[:-1]
                    else:
                        leftover = b""
                    if data:
                        stream.write(data)
                    continue

                # Text frame: control/metadata
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    print(f"  raw text: {message}")
                    continue
                kind = msg.get("type")
                if kind == "segment":
                    if first_segment_at is None:
                        first_segment_at = now
                    segments.append(msg)
                    elapsed = (now - t0) * 1000
                    print(
                        f"[{elapsed:7.0f} ms] segment #{msg['index']:<2} "
                        f"{msg['start_s']:>6.2f}–{msg['end_s']:>6.2f}s "
                        f"({msg.get('duration_s', msg['end_s'] - msg['start_s']):.2f}s)  "
                        f"\"{msg['text']}\"",
                        flush=True,
                    )
                elif kind == "done":
                    elapsed = (now - t0) * 1000
                    print(
                        f"[{elapsed:7.0f} ms] done · {msg.get('total_segments')} segments · "
                        f"{msg.get('total_duration_s'):.2f}s audio",
                        flush=True,
                    )
                elif kind == "error":
                    print(f"\n✗ server error: {msg.get('code')} — {msg.get('message')}")
                else:
                    print(f"  unknown message: {msg}")

            if leftover:
                # Pad final stray byte so we don't drop a half-sample.
                stream.write(leftover + b"\x00")

    except Exception as exc:
        print(f"\n✗ error: {exc}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    duration_s = bytes_received / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
    print("\n✓ playback complete")
    print(f"  Received: {bytes_received:,} bytes  ({duration_s:.2f}s audio)")
    print(f"  Segments: {len(segments)}")
    print("  Timings (ms from connect):")
    if first_segment_at is not None:
        print(
            f"    first segment marker:  {(first_segment_at - t0) * 1000:7.0f}  "
            "(server has synthesised sentence 1 enough to know its duration)"
        )
    if first_audio_at is not None:
        print(
            f"    first PCM byte:        {(first_audio_at - t0) * 1000:7.0f}  "
            "(start of playback)"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Stream TTS audio from omnivoice-server over WebSocket, "
        "printing per-sentence timestamps as they arrive."
    )
    parser.add_argument("text", help="text to synthesize")
    parser.add_argument("--url", default="ws://localhost:8880", help="server base URL")
    parser.add_argument("--voice", default="auto", help="voice (auto, preset, design:..., clone:...)")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed, 0.25–4.0")
    parser.add_argument("--api-key", default=None, help="bearer token if the server has auth on")
    args = parser.parse_args()

    try:
        asyncio.run(
            stream_and_play(
                text=args.text,
                url=args.url,
                voice=args.voice,
                speed=args.speed,
                api_key=args.api_key,
            )
        )
    except KeyboardInterrupt:
        print("\n[stopped]")


if __name__ == "__main__":
    main()
