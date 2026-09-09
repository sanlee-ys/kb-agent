"""Tests for app.py — the FastAPI wrapper and GET /health.

Offline: TestClient talks to the ASGI app in process. /health must not
construct a KBAgent and must not need chroma_db.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_mod


@pytest.fixture
def client():
    with TestClient(app_mod.app) as test_client:
        yield test_client


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_construct_agent(client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("KBAgent must not run for GET /health")

    monkeypatch.setattr(app_mod, "KBAgent", boom)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_function_is_pure():
    assert app_mod.health() == {"status": "ok"}
