"""Tests for static frontend hosting and the /auth/status endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from omnivoice_server.app import create_app
from omnivoice_server.config import Settings


def _make_client(api_key: str, tmp_path_factory) -> TestClient:
    cfg = Settings(
        device="cpu",
        num_step=4,
        max_concurrent=1,
        api_key=api_key,
        profile_dir=tmp_path_factory.mktemp("profiles"),
        stt_enabled=False,
    )
    app = create_app(cfg)
    with patch("omnivoice_server.services.model.ModelService.load", new_callable=AsyncMock):
        with patch(
            "omnivoice_server.services.model.ModelService.is_loaded",
            new_callable=lambda: property(lambda self: True),
        ):
            client = TestClient(app)
            client.__enter__()
            return client


@pytest.fixture
def no_auth_client(tmp_path_factory):
    c = _make_client("", tmp_path_factory)
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def auth_client(tmp_path_factory):
    c = _make_client("secret-token", tmp_path_factory)
    yield c
    c.__exit__(None, None, None)


# ── GET / (static frontend) ─────────────────────────────────────────────────


def test_root_serves_html_without_auth(no_auth_client):
    r = no_auth_client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>OmniVoice Studio</title>" in r.text


def test_root_bypasses_auth_middleware(auth_client):
    # No Authorization header — the frontend itself must be reachable so users
    # can sign in.
    r = auth_client.get("/")
    assert r.status_code == 200
    assert "<title>OmniVoice Studio</title>" in r.text


# ── GET /auth/status ────────────────────────────────────────────────────────


def test_auth_status_false_when_key_unset(no_auth_client):
    r = no_auth_client.get("/auth/status")
    assert r.status_code == 200
    assert r.json() == {"required": False}


def test_auth_status_true_when_key_set(auth_client):
    # No Authorization header — the endpoint is unauthenticated by design so
    # the frontend can probe it before it has a key.
    r = auth_client.get("/auth/status")
    assert r.status_code == 200
    assert r.json() == {"required": True}


def test_auth_status_available_with_auth_header_too(auth_client):
    r = auth_client.get("/auth/status", headers={"Authorization": "Bearer secret-token"})
    assert r.status_code == 200
    assert r.json() == {"required": True}


# ── Regression: auth middleware still blocks /v1 routes ─────────────────────


def test_auth_middleware_still_blocks_v1_without_key(auth_client):
    r = auth_client.get("/v1/voices")
    assert r.status_code == 401


def test_auth_middleware_accepts_valid_key_on_v1(auth_client):
    r = auth_client.get("/v1/voices", headers={"Authorization": "Bearer secret-token"})
    assert r.status_code == 200
