"""Minimal WebSocket STT smoke test. Streams 3s of 440 Hz sine + 2s silence, prints responses."""
import asyncio
import json
import math
import struct
import time

import websockets

URL = "ws://localhost:8880/v1/audio/transcribe"
SR = 16000
TONE_S = 3.0
SILENCE_S = 2.0
CHUNK_MS = 100


def pcm16_tone(freq_hz: float, sec: float) -> bytes:
    n = int(sec * SR)
    amp = 16000
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq_hz * i / SR)))
        for i in range(n)
    )


def pcm16_silence(sec: float) -> bytes:
    return b"\x00\x00" * int(sec * SR)


async def main() -> None:
    audio = pcm16_tone(440.0, TONE_S) + pcm16_silence(SILENCE_S)
    chunk = int(SR * CHUNK_MS / 1000) * 2  # bytes per 100ms
    print(f"[smoke] connecting {URL}")
    t0 = time.monotonic()
    updates = []
    async with websockets.connect(URL, max_size=None, open_timeout=10) as ws:
        print(f"[smoke] connected in {time.monotonic() - t0:.2f}s")

        async def sender():
            for i in range(0, len(audio), chunk):
                await ws.send(audio[i : i + chunk])
                await asyncio.sleep(CHUNK_MS / 1000.0)
            await ws.send(json.dumps({"type": "eof"}))
            print("[smoke] sent EOF")

        async def receiver():
            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        continue
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        print(f"[smoke] non-JSON: {msg}")
                        continue
                    updates.append(data)
                    print(f"[smoke] recv: {data}")
            except websockets.ConnectionClosed as e:
                print(f"[smoke] closed: code={e.code} reason={e.reason!r}")

        await asyncio.wait(
            [asyncio.create_task(sender()), asyncio.create_task(receiver())],
            timeout=30,
            return_when=asyncio.ALL_COMPLETED,
        )
    print(f"[smoke] done in {time.monotonic() - t0:.2f}s. updates_received={len(updates)}")


asyncio.run(main())
