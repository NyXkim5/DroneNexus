"""
Tests for WebSocket JWT authentication in WebSocketHandler.

Exercises _authenticate_ws with auth_enabled on/off, valid/invalid/missing tokens.
Uses a fake WebSocket that tracks accept/close calls to avoid needing the full app.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from api.auth import create_access_token
from api.websocket import WebSocketHandler


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeWebSocket:
    """Minimal WebSocket double that records accept/close and query params."""

    def __init__(self, query_params: dict | None = None) -> None:
        self.query_params: dict = query_params or {}
        self.accepted: bool = False
        self.closed: bool = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


class _FakeApp:
    db = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handler() -> WebSocketHandler:
    return WebSocketHandler(_FakeApp())


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests: auth_enabled = False (default)
# ---------------------------------------------------------------------------

def test_ws_auth_disabled_allows_no_token(monkeypatch):
    """When auth_enabled is False, connections succeed without a token."""
    monkeypatch.setenv("OVERWATCH_AUTH_ENABLED", "false")
    ws = _FakeWebSocket()
    handler = _handler()
    user = _run(handler._authenticate_ws(ws))
    assert user is not None
    assert user.username == "anonymous"
    assert user.role == "operator"
    assert not ws.closed


# ---------------------------------------------------------------------------
# Tests: auth_enabled = True, no token
# ---------------------------------------------------------------------------

def test_ws_auth_enabled_rejects_no_token(monkeypatch):
    """When auth_enabled is True and no token, socket is closed with 4001."""
    monkeypatch.setenv("OVERWATCH_AUTH_ENABLED", "true")
    ws = _FakeWebSocket()
    handler = _handler()
    user = _run(handler._authenticate_ws(ws))
    assert user is None
    assert ws.closed
    assert ws.close_code == 4001
    assert ws.close_reason == "Authentication required"


# ---------------------------------------------------------------------------
# Tests: auth_enabled = True, valid token
# ---------------------------------------------------------------------------

def test_ws_auth_enabled_accepts_valid_token(monkeypatch):
    """When auth_enabled is True and a valid token is provided, user is returned."""
    monkeypatch.setenv("OVERWATCH_AUTH_ENABLED", "true")
    token = create_access_token("operator", "operator")
    ws = _FakeWebSocket(query_params={"token": token})
    handler = _handler()
    user = _run(handler._authenticate_ws(ws))
    assert user is not None
    assert user.username == "operator"
    assert user.role == "operator"
    assert not ws.closed


def test_ws_auth_enabled_accepts_viewer_token(monkeypatch):
    """Viewer role token is accepted and returns correct role."""
    monkeypatch.setenv("OVERWATCH_AUTH_ENABLED", "true")
    token = create_access_token("viewer", "viewer")
    ws = _FakeWebSocket(query_params={"token": token})
    handler = _handler()
    user = _run(handler._authenticate_ws(ws))
    assert user is not None
    assert user.username == "viewer"
    assert user.role == "viewer"
    assert not ws.closed


# ---------------------------------------------------------------------------
# Tests: auth_enabled = True, invalid / expired token
# ---------------------------------------------------------------------------

def test_ws_auth_enabled_rejects_invalid_token(monkeypatch):
    """Garbage token closes socket with 4003."""
    monkeypatch.setenv("OVERWATCH_AUTH_ENABLED", "true")
    ws = _FakeWebSocket(query_params={"token": "not.a.real.jwt"})
    handler = _handler()
    user = _run(handler._authenticate_ws(ws))
    assert user is None
    assert ws.closed
    assert ws.close_code == 4003
    assert ws.close_reason is not None
    assert len(ws.close_reason) > 0


def test_ws_auth_enabled_rejects_expired_token(monkeypatch):
    """Expired token closes socket with 4003."""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    from api.auth import SECRET_KEY, ALGORITHM

    monkeypatch.setenv("OVERWATCH_AUTH_ENABLED", "true")

    # Build a token that expired 1 hour ago
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "operator",
        "role": "operator",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    expired_token = pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    ws = _FakeWebSocket(query_params={"token": expired_token})
    handler = _handler()
    user = _run(handler._authenticate_ws(ws))
    assert user is None
    assert ws.closed
    assert ws.close_code == 4003
    assert "expired" in ws.close_reason.lower()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
