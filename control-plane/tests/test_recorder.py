import pytest
import json
import uuid
from unittest.mock import AsyncMock, patch
from app.recorder import should_record, push_trace, flush_to_db


@pytest.mark.asyncio
async def test_should_record_returns_session_id_when_active():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "session-abc"
    with patch("app.recorder.get_redis", return_value=mock_redis):
        result = await should_record()
    assert result == "session-abc"


@pytest.mark.asyncio
async def test_should_record_returns_none_when_inactive():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = ""
    with patch("app.recorder.get_redis", return_value=mock_redis):
        result = await should_record()
    assert result is None


@pytest.mark.asyncio
async def test_push_trace_rpushes_to_redis():
    mock_redis = AsyncMock()
    with patch("app.recorder.get_redis", return_value=mock_redis):
        await push_trace("session-1", {
            "trace_id": str(uuid.uuid4()),
            "model": "claude-sonnet-4-6",
            "original_payload": {},
            "final_payload": {},
            "mutation_steps": [],
            "action": "PROCEED",
        })
    mock_redis.rpush.assert_called_once()
    key = mock_redis.rpush.call_args[0][0]
    assert "session-1" in key
