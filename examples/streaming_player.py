"""
Real-time streaming audio player for omnivoice-server.

Plays PCM audio stream from the server in real-time using pyaudio.

Requirements:
    pip install httpx pyaudio

Usage:
    python streaming_player.py "Your text to synthesize"
"""

import sys
import time

import httpx
import pyaudio

BASE_URL = "https://cheese-ceviche-kut3blzridwujkll.salad.cloud"
API_KEY = ""  # Set if server requires auth

# Audio format constants (must match server output)
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
# Output buffer: ~21ms @ 24kHz. Smaller = lower start-of-playback latency.
FRAMES_PER_BUFFER = 512


def stream_and_play(
    text: str,
    instructions: str | None = None,
    speed: float = 1.0,
    position_temperature: float = 0.0,
    api_key: str | None = None,
):
    """Stream audio from server and play in real-time."""

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

    print(f"Streaming: {text[:50]}...")
    print(f"Instructions: {instructions or '[default design prompt]'}, Speed: {speed}x")
    print(
        f"Position temperature: {position_temperature} (0.0=deterministic, higher=more variation)"
    )
    print("Playing audio...")

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        t0 = time.perf_counter()
        with httpx.stream(
            "POST",
            f"{BASE_URL}/v1/audio/speech",
            headers=headers,
            json={
                "model": "omnivoice",
                "input": text,
                "speed": speed,
                "response_format": "pcm",
                "stream": True,
                "num_step": 32
            },
            timeout=60.0,
        ) as response:
            response.raise_for_status()
            t_headers = time.perf_counter()
            print(
                f"[{(t_headers - t0) * 1000:7.0f} ms] {response.status_code} "
                f"{response.reason_phrase}  ({response.headers.get('content-type', '?')})",
                flush=True,
            )

            # Verify audio format from headers
            sample_rate = int(response.headers.get("X-Audio-Sample-Rate", SAMPLE_RATE))
            if sample_rate != SAMPLE_RATE:
                print(f"Warning: Server sample rate {sample_rate}Hz != expected {SAMPLE_RATE}Hz")

            # iter_bytes() with no chunk_size yields decoded content as it arrives
            # (unbuffered, but handles Content-Encoding). iter_raw() would skip
            # decoding; chunk_size=N would buffer until N bytes are ready.
            bytes_received = 0
            first_byte_at = None
            first_write_at = None
            chunk_index = 0
            # Network chunks can split mid-sample. For 16-bit PCM, writing odd
            # bytes makes PyAudio drop the trailing byte, which byte-swaps every
            # subsequent sample → garbled audio. Carry the stray byte forward.
            leftover = b""
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                now = time.perf_counter()
                if first_byte_at is None:
                    first_byte_at = now
                chunk_index += 1
                bytes_received += len(chunk)
                print(
                    f"[{(now - t0) * 1000:7.0f} ms] chunk #{chunk_index:<4} "
                    f"{len(chunk):6,} B  (total {bytes_received:,} B, "
                    f"{bytes_received / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH):.2f}s audio)",
                    flush=True,
                )
                data = leftover + chunk
                if len(data) % SAMPLE_WIDTH:
                    leftover = data[-1:]
                    data = data[:-1]
                else:
                    leftover = b""
                if data:
                    stream.write(data)
                    if first_write_at is None:
                        first_write_at = time.perf_counter()
            if leftover:
                # Pad final stray byte so we don't drop a half-sample.
                stream.write(leftover + b"\x00")

            duration_s = bytes_received / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
            print("\n✓ Playback complete")
            print(f"  Received: {bytes_received:,} bytes")
            print(f"  Duration: {duration_s:.2f}s")
            print("  Timings (ms from request start):")
            print(f"    headers:      {(t_headers - t0) * 1000:7.0f}")
            if first_byte_at is not None:
                print(f"    first byte:   {(first_byte_at - t0) * 1000:7.0f}  "
                      f"(server TTFB incl. synth of sentence 1)")
            if first_write_at is not None:
                print(f"    first write:  {(first_write_at - t0) * 1000:7.0f}  "
                      "(PyAudio accepted first frames)")

    except httpx.HTTPStatusError as e:
        print(f"\n✗ HTTP error: {e.response.status_code}")
        print(f"  {e.response.text}")
    except Exception as e:
        print(f"\n✗ Error: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: python streaming_player.py <text> [instructions] [speed] [position_temperature]"
        )
        print()
        print("Examples:")
        print('  python streaming_player.py "Hello world"')
        print('  python streaming_player.py "Hello world" "female,british accent"')
        print('  python streaming_player.py "Hello world" "male,low pitch" 1.2')
        print('  python streaming_player.py "Hello world" "female,american accent" 1.0 0.0')
        print()
        print("Note: position_temperature=0.0 (default) ensures consistent voice across chunks")
        sys.exit(1)

    text = sys.argv[1]
    instructions = sys.argv[2] if len(sys.argv) > 2 else None
    speed = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    position_temperature = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    stream_and_play(text, instructions, speed, position_temperature, API_KEY)


if __name__ == "__main__":
    main()
