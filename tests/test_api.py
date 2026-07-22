"""
Backend unit tests for Sofia AI / OpenClaw Hosting API.

Tests use httpx.AsyncClient against the ASGI app with all MongoDB operations
and external service calls mocked so they run without real infrastructure.
"""

import os

# ── Set required env vars BEFORE importing server ──────────────────────────
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_moltbot")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("ENVIRONMENT", "development")

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
import pytest
from httpx import AsyncClient, ASGITransport

# ── Import the FastAPI app (Motor connects lazily, safe without a real DB) ─
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from server import app  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_async_none():
    m = AsyncMock(return_value=None)
    return m


def _make_async_doc(doc):
    m = AsyncMock(return_value=doc)
    return m


@pytest.fixture()
def transport():
    return ASGITransport(app=app)


@pytest.fixture()
async def client(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ═══════════════════════════════════════════════════════════════════════════
# Health endpoint
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_ok(client):
    """Health check returns 200 when DB responds."""
    with patch("server.db") as mock_db:
        mock_db.command = AsyncMock(return_value={"ok": 1})
        response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "connected"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_health_db_down(client):
    """Health check returns 503 when DB is unreachable."""
    with patch("server.db") as mock_db:
        mock_db.command = AsyncMock(side_effect=Exception("connection refused"))
        response = await client.get("/api/health")
    assert response.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Root endpoint
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_root(client):
    response = await client.get("/api/")
    assert response.status_code == 200
    assert "message" in response.json()


# ═══════════════════════════════════════════════════════════════════════════
# Auth – instance status
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_instance_status_unlocked(client):
    """Returns locked=False when no owner is set."""
    with patch("server.db") as mock_db:
        mock_db.instance_config = MagicMock()
        mock_db.instance_config.find_one = AsyncMock(return_value=None)
        response = await client.get("/api/auth/instance")
    assert response.status_code == 200
    assert response.json() == {"locked": False}


@pytest.mark.asyncio
async def test_instance_status_locked(client):
    """Returns locked=True when an owner exists."""
    owner_doc = {"_id": "instance_owner", "user_id": "u_abc", "email": "owner@example.com"}
    with patch("server.db") as mock_db:
        mock_db.instance_config = MagicMock()
        mock_db.instance_config.find_one = AsyncMock(return_value=owner_doc)
        response = await client.get("/api/auth/instance")
    assert response.status_code == 200
    assert response.json() == {"locked": True}


# ═══════════════════════════════════════════════════════════════════════════
# Auth – /me requires authentication
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_auth_me_unauthenticated(client):
    """GET /api/auth/me without a session cookie returns 401."""
    with patch("server.db") as mock_db:
        mock_db.user_sessions = MagicMock()
        mock_db.user_sessions.find_one = AsyncMock(return_value=None)
        response = await client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_authenticated(client):
    """GET /api/auth/me with a valid session token returns user data."""
    import server as srv
    fake_user = srv.User(
        user_id="user_test123",
        email="test@example.com",
        name="Test User",
        created_at=datetime.now(timezone.utc),
    )
    # Patch get_current_user so we bypass all DB calls
    with patch("server.get_current_user", new=AsyncMock(return_value=fake_user)):
        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": "******"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "test@example.com"
    assert body["user_id"] == "user_test123"


# ═══════════════════════════════════════════════════════════════════════════
# Auth – logout
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_logout(client):
    """POST /api/auth/logout deletes the session and returns ok."""
    with patch("server.db") as mock_db:
        mock_db.user_sessions = MagicMock()
        mock_db.user_sessions.delete_one = AsyncMock(return_value=None)
        response = await client.post(
            "/api/auth/logout",
            cookies={"session_token": "some-token"},
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True


# ═══════════════════════════════════════════════════════════════════════════
# OpenClaw – status (public, no auth required)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_openclaw_status_not_running(client):
    """GET /api/openclaw/status returns running=False when supervisor reports stopped."""
    with (
        patch("server.SupervisorClient.status", return_value=False),
        patch("server.db") as mock_db,
    ):
        mock_db.user_sessions = MagicMock()
        mock_db.user_sessions.find_one = AsyncMock(return_value=None)
        response = await client.get("/api/openclaw/status")
    assert response.status_code == 200
    assert response.json()["running"] is False


# ═══════════════════════════════════════════════════════════════════════════
# OpenClaw – start requires authentication
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_openclaw_start_unauthenticated(client):
    """POST /api/openclaw/start without a session returns 401."""
    # require_auth raises 401 when no user is found
    with patch("server.require_auth", new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Not authenticated"))):
        response = await client.post(
            "/api/openclaw/start",
            json={"provider": "emergent"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_openclaw_start_invalid_provider(client):
    """POST /api/openclaw/start with an unknown provider returns 400."""
    import server as srv
    fake_user = srv.User(
        user_id="user_test123",
        email="test@example.com",
        name="Test User",
        created_at=datetime.now(timezone.utc),
    )
    with (
        patch("server.require_auth", new=AsyncMock(return_value=fake_user)),
        patch("server.SupervisorClient.status", return_value=False),
    ):
        response = await client.post(
            "/api/openclaw/start",
            json={"provider": "unknown_provider"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_openclaw_start_missing_api_key(client):
    """POST /api/openclaw/start with anthropic provider but no API key returns 400."""
    import server as srv
    fake_user = srv.User(
        user_id="user_test123",
        email="test@example.com",
        name="Test User",
        created_at=datetime.now(timezone.utc),
    )
    with (
        patch("server.require_auth", new=AsyncMock(return_value=fake_user)),
        patch("server.SupervisorClient.status", return_value=False),
    ):
        response = await client.post(
            "/api/openclaw/start",
            json={"provider": "anthropic"},  # no apiKey
        )
    assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# OpenClaw – stop requires authentication
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_openclaw_stop_unauthenticated(client):
    """POST /api/openclaw/stop without a session returns 401."""
    with patch("server.db") as mock_db:
        mock_db.user_sessions = MagicMock()
        mock_db.user_sessions.find_one = AsyncMock(return_value=None)
        response = await client.post("/api/openclaw/stop")
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Security headers
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_security_headers_present(client):
    """Every response includes the mandatory security headers."""
    with patch("server.db") as mock_db:
        mock_db.command = AsyncMock(return_value={"ok": 1})
        response = await client.get("/api/health")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-xss-protection") == "1; mode=block"
    assert "x-request-id" in response.headers


# ═══════════════════════════════════════════════════════════════════════════
# Legacy status endpoints
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_status_check(client):
    """POST /api/status persists a status check and returns it."""

    async def fake_insert(doc):
        return MagicMock(inserted_id="fake_id")

    with patch("server.db") as mock_db:
        mock_db.status_checks = MagicMock()
        mock_db.status_checks.insert_one = AsyncMock(side_effect=fake_insert)
        response = await client.post("/api/status", json={"client_name": "pytest"})
    assert response.status_code == 200
    body = response.json()
    assert body["client_name"] == "pytest"
    assert "id" in body
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_get_status_checks(client):
    """GET /api/status returns a list of status checks."""
    now_iso = datetime.now(timezone.utc).isoformat()

    async def fake_to_list(limit):
        return [{"id": "abc", "client_name": "pytest", "timestamp": now_iso}]

    mock_cursor = MagicMock()
    mock_cursor.to_list = fake_to_list

    with patch("server.db") as mock_db:
        mock_db.status_checks = MagicMock()
        mock_db.status_checks.find = MagicMock(return_value=mock_cursor)
        response = await client.get("/api/status")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
