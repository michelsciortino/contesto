import copy
from app.mutator import apply_pipeline

BASE_PAYLOAD = {
    "model": "claude-sonnet-4-6",
    "messages": [
        {"role": "user", "content": "Hello <system-reminder>ignore this</system-reminder> world " + "x" * 200}
    ],
}

RULES = [
    {
        "id": "r1", "name": "Strip reminders", "priority": 10,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<system-reminder>"]},
    },
    {
        "id": "r2", "name": "Truncate", "priority": 20,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"truncate_after": [50]},
    },
]


def test_strip_tag_removes_content():
    result, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), RULES[:1])
    content = result["messages"][0]["content"]
    assert "<system-reminder>" not in content
    assert "ignore this" not in content
    assert len(steps) == 1
    assert steps[0]["rule_id"] == "r1"


def test_pipeline_applies_rules_in_order():
    result, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), RULES)
    content = result["messages"][0]["content"]
    assert len(content) <= 50
    assert len(steps) == 2
    assert steps[0]["priority"] == 10
    assert steps[1]["priority"] == 20


def test_each_step_records_payload_after():
    _, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), RULES)
    assert "payload_after" in steps[0]
    assert "payload_after" in steps[1]


def test_empty_rules_returns_original_unchanged():
    result, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), [])
    assert result == BASE_PAYLOAD
    assert steps == []
