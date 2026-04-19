## Troubleshooting

### Model Download Issues

The model is downloaded from HuggingFace on first run. If you encounter issues:

```bash
# Pre-download the model
python -c "from omnivoice import OmniVoice; OmniVoice.from_pretrained('k2-fsa/OmniVoice')"

# Or use a local model
omnivoice-server --model-id /path/to/local/model
```

### CUDA Out of Memory

Reduce concurrent requests or use CPU:

```bash
omnivoice-server --max-concurrent 1 --device cpu
```

### Audio Quality Issues

Increase inference steps for better quality:

```bash
omnivoice-server --num-step 32
```

### Browser CORS Errors

If `curl` works but a browser frontend fails with a CORS error, the frontend and server are running on different origins and the browser is blocking the request.

Start the server with the frontend origin explicitly allowed:

```bash
omnivoice-server --cors-origins "http://localhost:5001"
```

#### Manual Smoke Test: Preflight

Verify that browser preflight succeeds:

```bash
curl -i -X OPTIONS http://127.0.0.1:8880/v1/audio/speech \
  -H "Origin: http://localhost:5001" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type,Authorization"
```

Expected result:

- HTTP status `200`
- `Access-Control-Allow-Origin: http://localhost:5001`
- `Access-Control-Allow-Methods` includes `POST`

#### Manual Smoke Test: Actual Request

```bash
curl -i -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H "Origin: http://localhost:5001" \
  -H "Content-Type: application/json" \
  -d '{"model":"omnivoice","input":"Hello from browser smoke test","voice":"auto"}' \
  --output /tmp/omnivoice-smoke.wav
```

Expected result:

- HTTP status `200`
- `Access-Control-Allow-Origin: http://localhost:5001`
- audio file written successfully

#### Manual Smoke Test: Auth + CORS

If API key auth is enabled, preflight should still succeed and failed auth responses should still include CORS headers for allowed origins:

```bash
omnivoice-server --api-key secret-token --cors-origins "http://localhost:5001"
```

```bash
curl -i -X OPTIONS http://127.0.0.1:8880/v1/audio/speech \
  -H "Origin: http://localhost:5001" \
  -H "Access-Control-Request-Method: POST"
```

```bash
curl -i -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H "Origin: http://localhost:5001" \
  -H "Content-Type: application/json" \
  -d '{"model":"omnivoice","input":"Missing auth should still be CORS-visible","voice":"auto"}'
```

Expected result:

- Preflight returns `200`
- Unauthorized POST returns `401`
- Unauthorized response still includes `Access-Control-Allow-Origin` for the allowed origin
