from app.redis_client import RULES_KEY, RECORDING_KEY, traces_key


def test_key_constants_defined():
    assert RULES_KEY == "governor:rules"
    assert RECORDING_KEY == "governor:recording"


def test_traces_key_includes_session_id():
    assert traces_key("abc-123") == "governor:traces:abc-123"
