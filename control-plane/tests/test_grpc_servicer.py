import pytest
import json
import uuid
from unittest.mock import AsyncMock, patch
from app.grpc_servicer import GovernorServicer
import sys
sys.path.insert(0, "/home/claude/governor/protos/generated")
import interceptor_pb2

SAMPLE_PAYLOAD = {
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello <system-reminder>remove me</system-reminder>"}],
    "tools": [],
    "litellm_call_id": str(uuid.uuid4()),
}

MATCHING_RULES = [{
    "id": str(uuid.uuid4()), "name": "Strip reminders", "priority": 10,
    "match_logic": {"==": [1, 1]},
    "mutate_logic": {"strip_tag": ["<system-reminder>"]},
}]


@pytest.mark.asyncio
async def test_proceeds_when_no_rules_match():
    servicer = GovernorServicer()
    request = interceptor_pb2.ContextRequest(
        trace_id=str(uuid.uuid4()),
        model="claude-sonnet-4-6",
        raw_json_payload=json.dumps(SAMPLE_PAYLOAD),
    )
    with patch("app.grpc_servicer.load_rules_from_redis", new=AsyncMock(return_value=[])):
        response = await servicer.MutateContext(request, None)
    assert response.action == interceptor_pb2.ContextResponse.Action.PROCEED


@pytest.mark.asyncio
async def test_mutates_when_rules_match():
    servicer = GovernorServicer()
    request = interceptor_pb2.ContextRequest(
        trace_id=str(uuid.uuid4()),
        model="claude-sonnet-4-6",
        raw_json_payload=json.dumps(SAMPLE_PAYLOAD),
    )
    with patch("app.grpc_servicer.load_rules_from_redis", new=AsyncMock(return_value=MATCHING_RULES)):
        with patch("app.grpc_servicer.should_record", new=AsyncMock(return_value=None)):
            response = await servicer.MutateContext(request, None)
    assert response.action == interceptor_pb2.ContextResponse.Action.MUTATED
    result = json.loads(response.modified_json_payload)
    assert "<system-reminder>" not in result["messages"][0]["content"]


@pytest.mark.asyncio
async def test_fails_open_on_exception():
    servicer = GovernorServicer()
    request = interceptor_pb2.ContextRequest(
        trace_id=str(uuid.uuid4()),
        model="claude-sonnet-4-6",
        raw_json_payload="NOT VALID JSON",
    )
    response = await servicer.MutateContext(request, None)
    assert response.action == interceptor_pb2.ContextResponse.Action.PROCEED
