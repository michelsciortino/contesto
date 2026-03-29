import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_traces_list_empty_before_recording(client: AsyncClient):
    resp = await client.get("/traces")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_traces_list_scoped_by_session(client: AsyncClient):
    start = await client.post("/recording/start")
    session_id = start.json()["session_id"]
    await client.post("/recording/stop")
    resp = await client.get(f"/traces?session_id={session_id}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_recordings_list(client: AsyncClient):
    await client.post("/recording/start")
    await client.post("/recording/stop")
    resp = await client.get("/recordings")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert "trace_count" in resp.json()[0]
