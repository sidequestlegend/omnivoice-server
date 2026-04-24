"""Verify server-side drain-on-EOF + idle-flush fixes."""

from __future__ import annotations

import asyncio
import json
import time

import numpy as np
import soundfile as sf
import websockets

WS_URL = "ws://localhost:8880/v1/audio/transcribe"
WAV_PATH = r"c:\workspace\TwentyLabs\profiles\shane\ref_audio.wav"
CHUNK_MS = 100


async def stream_test(name, pcm16, terminator, wait_before_terminator_s=0.0):
    print(f"\n=== {name} ===")
    print(f"  {len(pcm16)/16000:.2f}s of audio, terminator={terminator!r}, wait={wait_before_terminator_s}s")

    frames = []
    t0 = time.perf_counter()

    async with websockets.connect(WS_URL, max_size=None) as ws:
        async def sender():
            chunk_samples = int(16000 * CHUNK_MS / 1000)
            for i in range(0, len(pcm16), chunk_samples):
                slice_ = pcm16[i:i + chunk_samples]
                await ws.send(slice_.tobytes())
                await asyncio.sleep(CHUNK_MS / 1000)
            if wait_before_terminator_s > 0:
                await asyncio.sleep(wait_before_terminator_s)
            if terminator is not None:
                await ws.send(json.dumps({"type": terminator}))

        send_task = asyncio.create_task(sender())
        try:
            async with asyncio.timeout(30):
                async for msg in ws:
                    now = time.perf_counter() - t0
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue
                    frames.append((now, data))
        except asyncio.TimeoutError:
            print("  (timeout reading)")
        except websockets.exceptions.ConnectionClosed:
            pass
        send_task.cancel()

    partials = [f for f in frames if not f[1].get("is_final")]
    finals = [f for f in frames if f[1].get("is_final")]
    accumulated = "".join(f[1].get("text", "") for f in frames)
    print(f"  received: {len(partials)} partial(s), {len(finals)} final(s)")
    print(f"  accumulated ({len(accumulated.strip())} chars): {accumulated.strip()!r}")
    if not finals:
        print("  FAIL NO FINAL RECEIVED")
    else:
        print(f"  OK final arrived at {finals[-1][0]:.2f}s")


async def main():
    audio, sr = sf.read(WAV_PATH, dtype="float32", always_2d=False)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if sr != 16000:
        ratio = sr / 16000
        out_n = int(len(audio) / ratio)
        resampled = np.zeros(out_n, dtype=np.float32)
        for i in range(out_n):
            s = int(i * ratio)
            e = int((i + 1) * ratio)
            resampled[i] = audio[s:e].mean() if e > s else 0
        audio = resampled
    pcm16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)

    # T1 was the failing case: immediate EOF — should now get the FULL transcript
    # because of server-side drain-on-EOF.
    await stream_test("T1: immediate EOF (expect full transcript via server drain)", pcm16, "eof")

    # T5: NO terminator at all — just stop sending audio. Idle watchdog should fire.
    await stream_test("T5: stop sending audio, no terminator (expect server idle-flush)", pcm16, None, wait_before_terminator_s=2.0)


if __name__ == "__main__":
    asyncio.run(main())
