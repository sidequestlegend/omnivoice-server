# omnivoice-server — local Docker workflow

This project is only ever run inside the CUDA Docker container. There is no host-Python run path; the only local Python processes are the thin client scripts in [examples/](examples/) that talk to the containerized server over localhost.

All commands below run from the repo root.

---

## Prerequisites (one-time)

1. **Docker Desktop** with the WSL 2 backend enabled.
2. **NVIDIA driver ≥ 555** on the Windows host (Blackwell support). The driver handles WSL GPU passthrough automatically.
3. Verify GPU passthrough is working before any first build:
   ```powershell
   docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
   ```
   Must print your GPU in an `nvidia-smi` table. If it fails, restart Docker Desktop; if still failing, reinstall Docker Desktop ≥ 4.29.
4. A **`.env`** file at the repo root (gitignored, dockerignored). Example contents:
   ```
   HF_TOKEN=<your_huggingface_token>
   OMNIVOICE_CORS_ALLOW_ORIGINS=*
   OMNIVOICE_STT_MODEL_PATH=/home/ubuntu/.cache/whisper/large-v3.pt
   ```
   `HF_TOKEN` enables higher HF download rate limits. `OMNIVOICE_STT_MODEL_PATH` points at the mounted whisper cache so the model persists across container recreates.

---

## Build the image

```powershell
docker compose -f docker-compose-cuda.yml build
```

- First build: ~10–20 minutes (CUDA base image pull, torch wheels, SimulStreaming clone, model bake if enabled).
- Subsequent builds: seconds to minutes depending on which layer was invalidated.
- Image size lands around **20.5 GB** (matches `maemreyo/omnivoice-server:latest-cuda`).

**Production / Salad builds** (bakes model weights into the image so cold starts on ephemeral filesystems don't redownload 6 GB):

```powershell
docker compose -f docker-compose-cuda.yml build --build-arg BAKE_MODELS=true
```

Image size goes to ~26 GB but startup drops from ~6 min to ~45 s on machines without the cache volumes.

---

## Bring up

```powershell
docker compose -f docker-compose-cuda.yml up -d
```

- Starts the container in detached mode, binds `:8880` to the host.
- Weights are loaded from the mounted cache volumes (`./.hf-cache`, `./.torch-cache`, `./.whisper-cache`) — typical startup is ~60 s with caches, ~6 min on a completely cold machine.

Watch the startup logs to confirm both models loaded:

```powershell
docker compose -f docker-compose-cuda.yml logs -f omnivoice-server
```

Look for, in order:

```
Model loaded in <N>s. RAM: ...           ← OmniVoice TTS
STT model loaded in <N>s. RAM: ...       ← Whisper large-v3 via SimulStreaming
Startup complete in <N>s. Listening on http://0.0.0.0:8880
OMNIVOICE_READY host=0.0.0.0 port=8880
```

Verify from the host:

```powershell
curl http://localhost:8880/health
# Expect 200 with {"ready":true,"tts":{"loaded":true},"stt":{"loaded":true}, ...}
```

---

## Take down

```powershell
docker compose -f docker-compose-cuda.yml down
```

- Stops and removes the container + network.
- Leaves volumes (`./.hf-cache`, `./.torch-cache`, `./.whisper-cache`, `./profiles`) intact so the next `up` reuses cached weights and saved voice profiles.

To also remove named volumes (not normally needed — forces a full weight redownload on next start):

```powershell
docker compose -f docker-compose-cuda.yml down -v
```

---

## Rebuild after code changes

Any change to files under `omnivoice_server/` or `pyproject.toml` requires a rebuild to take effect (we don't mount the source at runtime):

```powershell
docker compose -f docker-compose-cuda.yml build
docker compose -f docker-compose-cuda.yml up -d --force-recreate
```

`--force-recreate` ensures the running container uses the new image rather than the cached one.

---

## Static frontend

The web studio at [omnivoice_server/static/index.html](omnivoice_server/static/index.html) is a single-file HTML app served directly by the FastAPI server at `/`. Open **http://localhost:8880/** in Chrome or Edge — no separate static server is needed.

If `OMNIVOICE_API_KEY` is set in `.env`, the studio shows a login overlay on first load. The entered password **is** the API key: on success it's saved to `localStorage["omnivoice_api_key"]` and reused as `Authorization: Bearer <key>` on HTTP calls and `?token=<key>` on WebSocket connections. A "Sign out" button in the header clears localStorage and returns to the login screen. When `OMNIVOICE_API_KEY` is empty, the login step is skipped entirely — the frontend probes [`GET /auth/status`](omnivoice_server/routers/health.py) on page load to decide.

---

## Common checks

| What | Command |
| --- | --- |
| Is it running? | `docker ps --format "{{.Names}} {{.Status}}"` |
| Recent logs | `docker logs omnivoice-server --tail 30` |
| Health + model state | `curl http://localhost:8880/health` |
| GPU VRAM usage | `nvidia-smi --query-gpu=memory.used,memory.total --format=csv` |
| List installed Ollama LLMs (used by the web studio's middle panel) | `curl http://localhost:11434/api/tags` |
| List TTS voices (built-ins + cloned profiles) | `curl http://localhost:8880/v1/voices` |

---

## Endpoints overview

- `POST /v1/audio/speech` — non-streaming TTS → WAV/PCM bytes
- `POST /v1/audio/speech` with `stream=true` — chunked PCM streaming
- **`WS /v1/audio/speech/stream`** — WebSocket streaming TTS with per-sentence `{"type":"segment",…}` markers
- `POST /v1/audio/speech/clone` — one-shot voice cloning (multipart upload)
- `GET /v1/voices` — list built-in voices + saved clone profiles
- `POST /v1/voices/profiles` — save a cloning profile (multipart: `profile_id`, `ref_audio`, `ref_text?`, `overwrite?`)
- `DELETE /v1/voices/profiles/{id}` — remove a profile
- `PATCH /v1/voices/profiles/{id}` — update ref audio and/or text
- **`WS /v1/audio/transcribe`** — live STT via SimulStreaming Whisper (binary PCM 16 kHz mono in, JSON transcript frames out). Client control: `{"type":"eof"}` (finish and close), `{"type":"flush"}` (finish current utterance, keep session open — used when VAD gets stuck).
- `GET /` — serves the single-file web studio
- `GET /health` — readiness (returns 503 while either model still loading). Unauthenticated.
- `GET /metrics` — request counters + RAM. Unauthenticated.
- `GET /auth/status` — `{"required": bool}`. Unauthenticated so the frontend can probe before prompting for a password.

---

## Image layer summary (for debugging size)

The image breaks down roughly as:
- CUDA 12.8 cudnn-runtime base image + system apt deps: ~11 GB
- Python venv (torch + nvidia pip packages + triton + deps): ~7.2 GB
- SimulStreaming clone: ~8 MB
- Model caches are mounted, not baked (~0 MB in image) unless `BAKE_MODELS=true`

Running totals live in the "Image size" entry of `docker images omnivoice-server`.

---

## Project layout

| Path | What lives here |
| --- | --- |
| [omnivoice_server/app.py](omnivoice_server/app.py) | FastAPI app factory + lifespan (loads both TTS and STT models at startup) |
| [omnivoice_server/config.py](omnivoice_server/config.py) | `Settings` (pydantic-settings). Every knob is here with an `OMNIVOICE_*` env var. |
| [omnivoice_server/cli.py](omnivoice_server/cli.py) | argparse → `Settings` → uvicorn |
| [omnivoice_server/routers/speech.py](omnivoice_server/routers/speech.py) | HTTP + WS TTS endpoints, voice resolution (auto/design/preset/clone) |
| [omnivoice_server/routers/voices.py](omnivoice_server/routers/voices.py) | voice profile CRUD (cloning) |
| [omnivoice_server/routers/transcribe.py](omnivoice_server/routers/transcribe.py) | WS STT endpoint |
| [omnivoice_server/routers/health.py](omnivoice_server/routers/health.py) | `/health`, `/metrics`, `/auth/status` (unauthenticated probe for the frontend) |
| [omnivoice_server/static/index.html](omnivoice_server/static/index.html) | Three-panel web studio: STT · LLM (Ollama) · TTS, with pipeline mode. Served at `/`. |
| [omnivoice_server/services/model.py](omnivoice_server/services/model.py) | OmniVoice singleton + dtype-candidate fallback loader |
| [omnivoice_server/services/stt_model.py](omnivoice_server/services/stt_model.py) | Whisper (SimulStreaming) singleton + Silero VAD |
| [omnivoice_server/services/inference.py](omnivoice_server/services/inference.py) | Async wrapper around `model.generate()` with thread-pool + semaphore |
| [omnivoice_server/services/stt.py](omnivoice_server/services/stt.py) | Per-WebSocket STT session, serialised via asyncio.Lock |
| [omnivoice_server/services/profiles.py](omnivoice_server/services/profiles.py) | voice clone profile storage (filesystem-backed) |
| [omnivoice_server/voice_presets.py](omnivoice_server/voice_presets.py) | OpenAI preset mappings + canonical `DESIGN_ATTRIBUTES` dict |
| [omnivoice_server/utils/text.py](omnivoice_server/utils/text.py) | `split_sentences` (greedy grouping) + `split_to_sentences` (one per) |
| [omnivoice_server/utils/audio.py](omnivoice_server/utils/audio.py) | tensor ↔ WAV / PCM helpers + upload validators |
| [Dockerfile.cuda](Dockerfile.cuda) | Multi-stage-free CUDA image; includes SimulStreaming git-clone + optional model bake |
| [docker-compose-cuda.yml](docker-compose-cuda.yml) | Local Docker Desktop orchestration with volume-mounted caches |
| [examples/streaming_player.py](examples/streaming_player.py) | CLI TTS client (WebSocket, prints per-sentence timestamps) |
| [examples/streaming_transcribe.py](examples/streaming_transcribe.py) | CLI STT client (mic or WAV file) |
| [tests/](tests/) | pytest suite, mocks both models via `AsyncMock` + property patches |

---

## Running tests

Run locally against the installed Python env (not in the container):

```powershell
python -m pytest tests/ --ignore=tests/test_cors.py
```

`tests/test_cors.py` is excluded because it imports `omnivoice` (the TTS model library), which isn't required to be installed in a dev environment. The container path has it.

Current green baseline: **199 passed, 4 skipped**. If you see failures referencing `hf_token: Extra inputs are not permitted`, it means `.env` is being read by pydantic-settings. `config.py` sets `extra="ignore"` to tolerate this; if you reintroduced the issue, that's the fix.

---

## Environment variables (full reference)

All `OMNIVOICE_*` vars map to `Settings` fields in [config.py](omnivoice_server/config.py). Compose loads `.env` via `env_file`; yml-level `environment:` overrides `.env` (so keep secrets in `.env` and stable server config in the yml).

| Variable | Default | Purpose |
| --- | --- | --- |
| `OMNIVOICE_HOST` | `127.0.0.1` | Bind host |
| `OMNIVOICE_PORT` | `8880` | Bind port |
| `OMNIVOICE_DEVICE` | `cpu` | `auto` / `cuda` / `mps` / `cpu` |
| `OMNIVOICE_MODEL_ID` | `k2-fsa/OmniVoice` | TTS HF repo ID or local path |
| `OMNIVOICE_NUM_STEP` | `32` | Diffusion steps |
| `OMNIVOICE_STT_ENABLED` | `false` | Turn on Whisper STT loading (compose sets `true`) |
| `OMNIVOICE_STT_MODEL_PATH` | `large-v3` | Whisper model name or `.pt` path. Set to `/home/ubuntu/.cache/whisper/large-v3.pt` to reuse the mounted cache. |
| `OMNIVOICE_STT_LANGUAGE` | `en` | ISO code or `auto` |
| `OMNIVOICE_STT_VAD` | `true` | Silero VAD (VACOnlineASRProcessor) |
| `OMNIVOICE_STT_MAX_CONCURRENT` | `1` | Hard-forced to serialised anyway — Whisper model has shared state |
| `OMNIVOICE_MAX_CONCURRENT` | `2` | TTS concurrency |
| `OMNIVOICE_API_KEY` | _(empty)_ | Bearer token for HTTP auth **and** login password for the web studio. Empty → no auth, studio skips login. WS endpoints accept it via `?token=` query. |
| `OMNIVOICE_CORS_ALLOW_ORIGINS` | `http://localhost:3000,...` | Comma-separated, `*` for wildcard (gated off by credentials validator) |
| `HF_TOKEN` | _(empty)_ | HuggingFace access token. Not an `OMNIVOICE_*` var — it's read by the `huggingface_hub` library directly. |
| `HF_HUB_OFFLINE` | _(unset)_ | Set to `1` in prod (e.g. Salad) to fail fast if anything tries to hit HF at runtime |
| `BAKE_MODELS` (build arg) | `false` | `--build-arg BAKE_MODELS=true` to pre-download all weights into the image |
| `TORCH_CHANNEL` (build arg) | `stable` | `--build-arg TORCH_CHANNEL=nightly` for bleeding-edge Blackwell kernels |
| `SIMULSTREAMING_SHA` (build arg) | `fc8cfed…6513a5` | Pinned SimulStreaming commit |

---

## Ollama (used by the web studio's LLM panel)

The middle panel of the web studio talks to a **local Ollama instance** at `http://localhost:11434` (configurable in the UI). The studio expects at least one model installed; it auto-fetches the list via `/api/tags`.

One-time setup:

```powershell
# Install Ollama from https://ollama.com/download/windows, then:
ollama pull qwen3.5         # or qwen3.5:9b for the quantised variant
```

If the studio can't connect to Ollama, the log shows a `model list fetch failed` warning and a `OLLAMA_ORIGINS=*` hint. By default Ollama allows `localhost:*` and `127.0.0.1:*` origins — the studio at `http://localhost:8880` should Just Work without any Ollama-side config.

Thinking models (Qwen 3, DeepSeek-R1) have their chain-of-thought suppressed via a `"think": false` field on the chat request; no additional stripping is done client-side.

---

## Web studio feature overview

The three-column layout at [omnivoice_server/static/index.html](omnivoice_server/static/index.html):

- **STT** (left) — live mic capture, 16 kHz mono PCM binary frames over WS, partials + finals rendered with timestamps. Includes a silence-watchdog that sends `{"type":"flush"}` if Silero VAD gets stuck in its 0.35–0.50 hysteresis dead zone.
- **LLM** (middle) — Ollama `/api/chat` streaming, token-by-token rendering, TTFT/tokens/tok-per-sec stats, editable system prompt pre-seeded with OmniVoice's inline non-verbal tags (`[laughter]`, `[sigh]`, etc.) and a minimal-punctuation directive. Model dropdown populated from Ollama's `/api/tags`.
- **TTS** (right) — WebSocket streaming to `/v1/audio/speech/stream`, per-sentence segment markers logged inline, live output meter + waveform, gapless chunk scheduling via shared `AudioContext`. Voice dropdown lists built-ins + cloned profiles; `design (custom…)` reveals an attributes input with clickable chips pulled from `/v1/voices` (`design_attributes`); `＋` opens a clone-creation form that accepts **either** a file upload **or** an in-browser WAV recording, with a one-click "🪄 Transcribe recording" that round-trips through the live STT endpoint to auto-fill the ref-text field.
- **Pipeline mode** (header toggle) — chains STT → LLM → TTS. Accumulates STT partials into a full utterance on `is_final`, feeds to Ollama, splits the LLM stream on sentence boundaries so TTS starts synthesising sentence 1 before the LLM finishes sentence 3. Barge-in: speaking mid-playback aborts the TTS queue **and** any in-flight LLM stream.

---

## Salad cloud deployment (out of scope for local; but relevant notes)

- Bake weights into the image: `docker compose build --build-arg BAKE_MODELS=true`.
- Push to a registry Salad can pull from (GHCR / Docker Hub).
- Set `OMNIVOICE_API_KEY` in Salad's env (the public URL is internet-facing).
- Set `HF_HUB_OFFLINE=1` so runtime doesn't try to hit HF.
- WebSocket works through Salad's Container Gateway unchanged (TLS terminates at the gateway; plain `ws://` inside the container).
- Voice profiles at `/home/ubuntu/app/profiles` are **ephemeral on Salad** (no persistent volumes). Document or replace `ProfileService` with an S3-backed implementation if persistence matters.

---

## Debugging gotchas

- **STT FINAL never fires.** Silero VAD's stock config (threshold 0.5, silence-end threshold 0.35, 500 ms min silence) has a hysteresis band where low-level background noise keeps it "triggered" forever. The web studio works around this with a silence-timeout watchdog that sends `{"type":"flush"}` to the server; the server then calls `session.finish()` and emits a final without closing the WS. See [routers/transcribe.py](omnivoice_server/routers/transcribe.py) `_handle_control`.
- **Whisper redownloads on container recreate.** openai-whisper (via SimulStreaming) caches `.pt` files in the current working directory by default, not `~/.cache/whisper`. The `.whisper-cache` volume plus `OMNIVOICE_STT_MODEL_PATH=/home/ubuntu/.cache/whisper/large-v3.pt` in `.env` pin it to the mounted path.
- **Pydantic fails with `hf_token: Extra inputs are not permitted`.** The `.env` file is being loaded; add `extra="ignore"` to `SettingsConfigDict` in [config.py](omnivoice_server/config.py).
- **Triton "Python.h: No such file or directory" warning spam.** Missing `python3-dev` in the apt step; Triton falls back to a slow PyTorch kernel implementation. Fixed in the current Dockerfile.
- **Barge-in doesn't trigger.** Peak threshold or frame count too high. Defaults in [omnivoice_server/static/index.html](omnivoice_server/static/index.html): `BARGE_PEAK_THRESHOLD = 0.08`, `BARGE_FRAMES = 2`. The `mic active · peak <N>` log line shows what you're hitting; drop the threshold below that.
- **CORS wildcard fails with `JSONDecodeError`.** `pydantic-settings` tries to JSON-parse `list[str]` env values. `cors_allow_origins` in [config.py](omnivoice_server/config.py) uses `Annotated[list[str], NoDecode]` so the custom validator handles `*`/comma-separated/JSON-array strings.
- **5090 CUDA errors.** If stable torch 2.8+cu128 ever throws sm_120 issues, rebuild with `--build-arg TORCH_CHANNEL=nightly`. Tag the output image as `…-torch-nightly` so it doesn't get promoted to production by accident.

---

## Related planning docs

- `C:\Users\shane\.claude\plans\i-want-to-come-glittery-tiger.md` — original multi-phase plan for the STT integration + Salad deployment. Still accurate for the server-side architecture; the later web-studio work is not captured there.
