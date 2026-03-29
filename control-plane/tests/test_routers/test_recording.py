import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_start_recording_returns_session_id(client: AsyncClient):
    resp = await client.post("/recording/start")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "started_at" in data


@pytest.mark.asyncio
async def test_start_recording_twice_fails(client: AsyncClient):
    await client.post("/recording/start")
    resp = await client.post("/recording/start")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_status_reflects_active_session(client: AsyncClient):
    await client.post("/recording/start")
    resp = await client.get("/recording/status")
    assert resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_stop_recording_flushes_and_closes(client: AsyncClient):
    start_resp = await client.post("/recording/start")
    session_id = start_resp.json()["session_id"]
    stop_resp = await client.post("/recording/stop")
    assert stop_resp.status_code == 200
    data = stop_resp.json()
    assert data["session_id"] == session_id
    assert "traces_flushed" in data


@pytest.mark.asyncio
async def test_stop_without_active_session_fails(client: AsyncClient):
    resp = await client.post("/recording/stop")
    assert resp.status_code == 404
